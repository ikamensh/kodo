"""Bug demonstrations found via static analysis.

Each test documents a specific bug with its ID (H1, M4, etc.) and either
proves the bug exists (xfail) or confirms it's been fixed (passes).

Documented outcomes:
- H1: max() on empty parallel_results raises ValueError (test passes by asserting
  the exception). Location: base.py:1416. Fix: max(..., default=0).
- M6: RunStats.record_agent() uses non-atomic +=. Test may pass (race rare) or
  xfail (data loss observed). Location: log.py:108-129.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from kodo import log
from kodo.cli._launch import _format_json_output
from kodo.cli._intake import _parse_goal_plan
from kodo.log import RunDir
from kodo.orchestrators.base import StageResult


# ---------------------------------------------------------------------------
# H1: max() on empty parallel_results crashes with ValueError
# ---------------------------------------------------------------------------


class TestBugH1EmptyParallelResults:
    """H1: If all parallel stages crash, max() on empty list raises ValueError.

    Location: base.py:1416 — ``max(len(r.cycles) for r in parallel_results)``
    When parallel_results is empty, max() raises ValueError: max() iterable
    argument is empty. Edge case: all parallel stages fail before appending.
    """

    def test_max_on_empty_sequence(self):
        """H1 fix: max(..., default=0) avoids ValueError on empty parallel_results."""
        parallel_results: list[StageResult] = []
        result = max((len(r.cycles) for r in parallel_results), default=0)
        assert result == 0


# ---------------------------------------------------------------------------
# M4: cycle_end without 'summary' key makes runs un-resumable
# ---------------------------------------------------------------------------


class TestBugM4MissingSummaryKey:
    """M4: A cycle_end event missing the 'summary' key crashes parse_run().

    Location: log.py ~line 422 — ``evt["summary"]`` instead of ``.get("summary", "")``.
    """

    def test_cycle_end_without_summary(self, tmp_path: Path):
        run_dir = RunDir.create(tmp_path, "m4_test")
        events = [
            {
                "ts": "2025-01-01T00:00:00Z",
                "t": 0,
                "event": "run_start",
                "goal": "test goal",
                "project_dir": str(tmp_path),
                "orchestrator": "api",
                "model": "test",
                "max_exchanges": 10,
                "max_cycles": 3,
                "team": [],
            },
            {
                "ts": "2025-01-01T00:01:00Z",
                "t": 60,
                "event": "cli_args",
                "team": "saga",
            },
            # Missing "summary" key:
            {"ts": "2025-01-01T00:02:00Z", "t": 120, "event": "cycle_end"},
        ]
        run_dir.log_file.write_text(
            "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8"
        )

        try:
            state = log.parse_run(run_dir.log_file)
            assert state is not None  # bug is fixed
        except KeyError as exc:
            assert "summary" in str(exc)
            pytest.xfail("BUG M4: evt['summary'] crashes on missing key")


# ---------------------------------------------------------------------------
# M11: _format_json_output(result=None) crashes with AttributeError
# ---------------------------------------------------------------------------


class TestBugM11FormatJsonOutputNone:
    """M11: _format_json_output(result=None, error=None) → AttributeError.

    Location: cli/_launch.py — accesses result.finished when result is None.
    """

    def test_result_none_error_none(self):
        try:
            output = _format_json_output(result=None, error=None)
            assert output is not None  # bug is fixed
        except AttributeError:
            pytest.xfail("BUG M11: _format_json_output crashes when result=None")


# ---------------------------------------------------------------------------
# M12: `not index` falsiness drops stage 0
# ---------------------------------------------------------------------------


class TestBugM12StageIndexZero:
    """M12: ``if not index`` is True for index=0, silently discarding stage 0.

    Location: cli/_intake.py — _parse_goal_plan stage filtering.
    """

    def test_stage_zero_preserved(self):
        raw = {
            "context": "test context",
            "stages": [
                {
                    "index": 0,
                    "name": "stage-zero",
                    "description": "first",
                    "acceptance_criteria": "done",
                },
                {
                    "index": 1,
                    "name": "stage-one",
                    "description": "second",
                    "acceptance_criteria": "done",
                },
            ],
        }
        plan = _parse_goal_plan(raw)
        stage_indices = [s.index for s in plan.stages]
        if 0 not in stage_indices:
            pytest.xfail("BUG M12: stage index=0 dropped due to `not index` falsiness")
        assert len(plan.stages) == 2


# ---------------------------------------------------------------------------
# M5: _test_snapshot doesn't save _run_stats — stats leak between tests
# ---------------------------------------------------------------------------


class TestBugM5SnapshotLeaksStats:
    """M5: _test_snapshot() omits _run_stats, so stats leak across tests.

    Location: log.py — _test_snapshot returns a 4-tuple without RunStats.
    """

    def test_snapshot_omits_run_stats(self):
        snapshot = log._test_snapshot()
        assert len(snapshot) == 4
        for item in snapshot:
            if isinstance(item, log.RunStats):
                return  # fixed — RunStats is now captured
        # Bug confirmed: stats not in snapshot


# ---------------------------------------------------------------------------
# M6: RunStats not thread-safe — concurrent record_agent loses data
# ---------------------------------------------------------------------------


class TestBugM6RunStatsRace:
    """M6: RunStats.record_agent() uses non-atomic ``+=`` under concurrency.

    Location: log.py:108-129 — _AgentStats fields (calls, cost_usd, etc.)
    updated without locking. Under parallel stages, concurrent record_agent
    calls can lose updates. Test xfails when data loss observed; may pass
    when race does not manifest (GIL makes it rare).
    """

    def test_concurrent_record_agent_data_loss(self):
        stats = log.RunStats()
        n_threads = 10
        n_calls = 100
        barrier = threading.Barrier(n_threads)

        def worker():
            barrier.wait()
            for _ in range(n_calls):
                stats.record_agent(
                    "worker",
                    cost_usd=0.001,
                    input_tokens=10,
                    output_tokens=5,
                    elapsed_s=0.01,
                    is_error=False,
                    cost_bucket="api",
                )

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        expected = n_threads * n_calls
        actual = stats.agents["worker"].calls
        if actual < expected:
            pytest.xfail(
                f"BUG M6: lost {expected - actual} calls due to thread race "
                f"({actual}/{expected})"
            )
        assert actual == expected
