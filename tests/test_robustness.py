"""Robustness tests: file corruption, backend errors (429, 500), SIGINT.

Complements existing tests in test_resume (corrupt JSONL), test_list_runs
(unparseable runs), test_api (529 fallback, UsageLimitExceeded).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from kodo import log
from kodo.cli import main
from kodo.cli import _main_inner
from kodo.cli._intake import _load_goal_plan
from kodo.log import RunDir
from kodo.orchestrators.api import ApiOrchestrator
from tests.conftest import FakeRunResult


# ---------------------------------------------------------------------------
# File corruption
# ---------------------------------------------------------------------------


class TestConfigCorruption:
    """Corrupt config.json on resume falls back to params from RunState."""

    def test_corrupt_config_json_falls_back_to_state_params(self, tmp_path: Path):
        """Resume with corrupt config.json reconstructs params from run state."""
        project = tmp_path / "proj"
        project.mkdir()
        run_id = "20250315_120000"
        run_root = log._runs_root() / run_id
        run_root.mkdir(parents=True)

        events = [
            {
                "event": "run_start",
                "goal": "Fix bug",
                "project_dir": str(project),
                "orchestrator": "api",
                "model": "opus",
                "max_exchanges": 20,
                "max_cycles": 1,
                "team": ["worker_fast"],
            },
            {"event": "cli_args", "team": "saga"},
            {"event": "cycle_end", "summary": "partial"},
        ]
        (run_root / "run.jsonl").write_text(
            "\n".join(json.dumps({"ts": "t", "t": 0, **e}) for e in events) + "\n"
        )
        (run_root / "goal.md").write_text("Fix bug")
        (run_root / "config.json").write_text("not valid json {{{")

        with patch("kodo.cli._main.launch_resume") as mock_resume:
            sys.argv = ["kodo", "--resume", run_id, "--yes", str(project)]
            _main_inner()

        mock_resume.assert_called_once()
        call_args = mock_resume.call_args[0]
        run_dir = call_args[0]
        assert run_dir.run_id == run_id


class TestGoalPlanCorruption:
    """Corrupt goal-plan.json returns None (graceful degradation)."""

    def test_corrupt_goal_plan_returns_none(self, tmp_path: Path):
        """_load_goal_plan with invalid JSON returns None."""
        run_dir = RunDir.create(tmp_path, "corrupt_plan")
        run_dir.goal_plan_file.write_text("not json at all")
        assert _load_goal_plan(run_dir) is None

    def test_malformed_goal_plan_returns_none(self, tmp_path: Path):
        """_load_goal_plan with valid JSON but missing stages returns None."""
        run_dir = RunDir.create(tmp_path, "empty_plan")
        run_dir.goal_plan_file.write_text('{"context": "x", "stages": []}')
        assert _load_goal_plan(run_dir) is None


class TestGoalFileCorruption:
    """--goal-file with unreadable or corrupt file fails clearly."""

    def test_goal_file_not_found_fails(self, tmp_path: Path):
        """--goal-file with missing path exits with error."""
        project = tmp_path / "proj"
        project.mkdir()
        with (
            patch("kodo.cli._launch._original_stdout", None),
            pytest.raises(SystemExit),
        ):
            sys.argv = [
                "kodo",
                "--goal-file",
                str(tmp_path / "nonexistent.md"),
                "--yes",
                str(project),
            ]
            _main_inner()


# ---------------------------------------------------------------------------
# Backend errors: 429, 500 (retry logic in ApiOrchestrator)
# ---------------------------------------------------------------------------


def _make_fake_team():
    from kodo.agent import Agent
    from tests.conftest import FakeSession

    return {"worker": Agent(FakeSession(response_text="ok"), "test", max_turns=5)}


class TestBackend429Retry:
    """429 rate limit triggers retry, then succeeds."""

    def test_429_retry_then_succeed(self, tmp_path: Path):
        """First call 429, second succeeds; cycle completes."""
        log.init(RunDir.create(tmp_path, "api_429"))
        from pydantic_ai.exceptions import ModelHTTPError

        call_count = [0]

        def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
            def fake_run_sync(prompt, *, usage_limits=None):
                nonlocal call_count
                call_count[0] += 1
                if call_count[0] == 1:
                    raise ModelHTTPError(
                        status_code=429, model_name="test", body="rate limit"
                    )
                return FakeRunResult()

            self.run_sync = fake_run_sync

        with (
            patch("kodo.orchestrators.api.Agent.__init__", fake_agent_init),
            patch.object(ApiOrchestrator, "_summarize", return_value="done"),
            patch("time.sleep"),
        ):
            orch = ApiOrchestrator(model="claude-opus-4-6")
            result = orch.cycle(
                "build feature", tmp_path, _make_fake_team(), max_exchanges=10
            )

        assert call_count[0] == 2
        assert result.summary == "done"


class TestBackend500Retry:
    """500 server error triggers retry, then succeeds."""

    def test_500_retry_then_succeed(self, tmp_path: Path):
        """First call 500, second succeeds."""
        log.init(RunDir.create(tmp_path, "api_500"))
        from pydantic_ai.exceptions import ModelHTTPError

        call_count = [0]

        def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
            def fake_run_sync(prompt, *, usage_limits=None):
                nonlocal call_count
                call_count[0] += 1
                if call_count[0] == 1:
                    raise ModelHTTPError(
                        status_code=500, model_name="test", body="internal error"
                    )
                return FakeRunResult()

            self.run_sync = fake_run_sync

        with (
            patch("kodo.orchestrators.api.Agent.__init__", fake_agent_init),
            patch.object(ApiOrchestrator, "_summarize", return_value="done"),
            patch("time.sleep"),
        ):
            orch = ApiOrchestrator(model="claude-opus-4-6")
            orch.cycle("build feature", tmp_path, _make_fake_team(), max_exchanges=10)

        assert call_count[0] == 2


class TestBackendRetriesExhausted:
    """429/500 on all retries raises ModelHTTPError."""

    def test_429_all_retries_exhausted_raises(self, tmp_path: Path):
        """429 on all 3 attempts raises ModelHTTPError."""
        log.init(RunDir.create(tmp_path, "api_429_exhausted"))
        from pydantic_ai.exceptions import ModelHTTPError

        def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
            def fake_run_sync(prompt, *, usage_limits=None):
                raise ModelHTTPError(
                    status_code=429, model_name="test", body="rate limit"
                )

            self.run_sync = fake_run_sync

        with (
            patch("kodo.orchestrators.api.Agent.__init__", fake_agent_init),
            patch("time.sleep"),
            pytest.raises(ModelHTTPError),
        ):
            orch = ApiOrchestrator(model="claude-opus-4-6")
            orch.cycle("build feature", tmp_path, _make_fake_team(), max_exchanges=10)


# ---------------------------------------------------------------------------
# SIGINT / KeyboardInterrupt
# ---------------------------------------------------------------------------


class TestSigint:
    """KeyboardInterrupt is caught by main() and exits 130."""

    def test_keyboard_interrupt_exits_130(self):
        """main() catches KeyboardInterrupt, prints message, exits 130."""
        with patch("kodo.cli._main._main_inner", side_effect=KeyboardInterrupt):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 130

    def test_keyboard_interrupt_json_mode_outputs_json(self, capsys):
        """In --json mode, KeyboardInterrupt produces JSON error."""
        with (
            patch("kodo.cli._main._main_inner", side_effect=KeyboardInterrupt),
            pytest.raises(SystemExit) as exc_info,
        ):
            sys.argv = ["kodo", "--json", "--goal", "x", "."]
            main()
        assert exc_info.value.code == 130
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["status"] == "error"
        assert "Interrupted" in data["error"]
