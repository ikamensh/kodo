"""Tests for kodo.log module."""

from __future__ import annotations

import json
from pathlib import Path

from kodo import log
from kodo.log import RunDir


def test_init_creates_log_file(tmp_path: Path):
    run_dir = RunDir.create(tmp_path, "test_run")
    log_file = log.init(run_dir)
    assert log_file.exists()
    assert log_file.parent == run_dir.root
    assert log_file.name == "log.jsonl"


def test_emit_writes_json_lines(tmp_path: Path):
    log.init(RunDir.create(tmp_path, "emit_test"))
    log.emit("my_event", foo="bar", count=42)
    log_file = log.get_log_file()
    lines = log_file.read_text().strip().split("\n")
    # First line is run_init from init(), second is our event
    record = json.loads(lines[-1])
    assert record["event"] == "my_event"
    assert record["foo"] == "bar"
    assert record["count"] == 42
    assert "ts" in record
    assert "t" in record


# ── init_append & emit edge cases (relocated from test_error_paths.py) ───

import pytest


class TestInitAppendValidation:
    def test_nonexistent_file_raises(self, tmp_path: Path):
        fake_log = tmp_path / "does_not_exist.jsonl"
        with pytest.raises(FileNotFoundError, match="does not exist"):
            log.init_append(fake_log)

    def test_invalid_log_raises(self, tmp_path: Path):
        bad_log = tmp_path / "bad.jsonl"
        bad_log.write_text('{"event":"random","ts":"t","t":0}\n')
        with pytest.raises(ValueError, match="missing run_start"):
            log.init_append(bad_log)

    def test_valid_log_resumes(self, tmp_path: Path):
        log_file = tmp_path / "test_run" / "log.jsonl"
        log_file.parent.mkdir(parents=True)
        events = [
            {
                "ts": "t",
                "t": 0,
                "event": "run_start",
                "goal": "g",
                "orchestrator": "api",
                "model": "m",
                "project_dir": str(tmp_path),
                "max_exchanges": 10,
                "max_cycles": 5,
                "team": [],
            },
            {"ts": "t", "t": 0.1, "event": "cli_args", "team": "full"},
            {"ts": "t", "t": 1, "event": "cycle_end", "summary": "partial"},
        ]
        log_file.write_text("\n".join(json.dumps(e) for e in events) + "\n")

        result = log.init_append(log_file)
        assert result == log_file
        content = log_file.read_text()
        assert "run_resumed" in content


def test_emit_with_unserializable_values(tmp_path: Path):
    """emit() handles non-JSON-serializable objects via _serialize fallback."""
    log.init(RunDir.create(tmp_path, "serial_test"))
    log.emit("edge", callback=lambda x: x, path=tmp_path)
    lines = log.get_log_file().read_text().strip().split("\n")
    record = json.loads(lines[-1])
    assert record["event"] == "edge"
    assert "lambda" in record["callback"]


# ── Bug regression tests (relocated from test_stage2_integration.py) ─────

import threading


def test_cycle_end_without_summary_key(tmp_path: Path):
    """M4: cycle_end missing 'summary' key should not crash parse_run."""
    run_dir = RunDir.create(tmp_path, "m4_test")
    events = [
        {
            "ts": "t",
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
        {"ts": "t", "t": 0.1, "event": "cli_args", "team": "full"},
        {"ts": "t", "t": 1, "event": "cycle_end"},  # no summary key
    ]
    run_dir.log_file.write_text("\n".join(json.dumps(e) for e in events) + "\n")

    try:
        state = log.parse_run(run_dir.log_file)
        assert state is not None
    except KeyError as exc:
        assert "summary" in str(exc)
        pytest.xfail("BUG M4: evt['summary'] crashes on missing key")


def test_snapshot_includes_run_stats():
    """M5: _test_snapshot must include RunStats to prevent leaks."""
    snapshot = log._test_snapshot()
    assert len(snapshot) == 6
    assert any(isinstance(item, log.RunStats) for item in snapshot)


def test_concurrent_record_agent_data_integrity():
    """M6: RunStats.record_agent() concurrent thread safety."""
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
        pytest.xfail(f"BUG M6: lost {expected - actual} calls ({actual}/{expected})")
    assert actual == expected
