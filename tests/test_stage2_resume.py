"""Integration tests for kodo --resume workflow.

Creates real run directories with log.emit, invokes _main_inner with --resume,
and verifies launch_resume receives correct ResumeState.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from kodo import log
from kodo.cli._main import _main_inner
from kodo.log import RunDir
from kodo.orchestrators.base import CycleResult, RunResult


def _create_incomplete_run(
    project_dir: Path,
    goal: str,
    *,
    run_id: str | None = None,
) -> tuple[RunDir, str]:
    """Create an incomplete run (run_start + cycle_end, no run_end). Returns (RunDir, run_id)."""
    run_dir = RunDir.create(project_dir, run_id)
    log.init(run_dir)
    log.emit(
        "run_start",
        goal=goal,
        project_dir=str(project_dir.resolve()),
        orchestrator="api",
        model="gemini-flash",
        max_exchanges=30,
        max_cycles=5,
        team=["worker_fast", "worker_smart"],
    )
    log.emit("cli_args", team="saga")
    log.emit("cycle_end", summary="Partial progress after cycle 1")
    # No run_end — run is incomplete
    return run_dir, run_dir.run_id


def _create_complete_run(project_dir: Path, goal: str) -> tuple[RunDir, str]:
    """Create a completed run (has run_end)."""
    run_dir, run_id = _create_incomplete_run(project_dir, goal)
    log.emit("run_end")
    return run_dir, run_id


def _fake_run_result():
    """Minimal RunResult for mocked launch_resume."""
    return RunResult(
        cycles=[
            CycleResult(
                exchanges=1,
                total_cost_usd=0.0,
                finished=True,
                summary="Resumed and done.",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Resume finds latest incomplete run
# ---------------------------------------------------------------------------


class TestResumeLatestIncomplete:
    """--resume without run ID finds latest incomplete run."""

    def test_resume_finds_latest_incomplete_and_passes_state(self, tmp_path: Path):
        """Create incomplete run, --resume finds it and passes correct ResumeState to launch_resume."""
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        runs_root = log._runs_root()
        runs_root.mkdir(parents=True, exist_ok=True)

        _, run_id = _create_incomplete_run(project_dir, "Build a REST API")

        with (
            patch("kodo.cli._params.has_claude", return_value=True),
            patch("kodo.cli._params.check_api_key", return_value=None),
            patch("kodo.cli._main.launch_resume") as mock_resume,
        ):
            mock_resume.return_value = _fake_run_result()
            sys.argv = [
                "kodo",
                "--resume",
                "--yes",
                str(project_dir),
            ]
            _main_inner()

            mock_resume.assert_called_once()
            call_args = mock_resume.call_args
            run_dir_arg, state_arg = call_args[0]
            assert run_dir_arg.project_dir == project_dir
            assert run_dir_arg.run_id == run_id
            assert state_arg.goal == "Build a REST API"
            assert state_arg.completed_cycles == 1
            assert state_arg.finished is False
            assert state_arg.last_summary == "Partial progress after cycle 1"


# ---------------------------------------------------------------------------
# Resume with specific run ID
# ---------------------------------------------------------------------------


class TestResumeByRunId:
    """--resume RUN_ID resumes specific run."""

    def test_resume_by_run_id(self, tmp_path: Path):
        """Create run, resume by explicit run ID."""
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        runs_root = log._runs_root()
        runs_root.mkdir(parents=True, exist_ok=True)

        _, run_id = _create_incomplete_run(project_dir, "Add tests")

        with (
            patch("kodo.cli._params.has_claude", return_value=True),
            patch("kodo.cli._params.check_api_key", return_value=None),
            patch("kodo.cli._main.launch_resume") as mock_resume,
        ):
            mock_resume.return_value = _fake_run_result()
            sys.argv = [
                "kodo",
                "--resume",
                run_id,
                "--yes",
                str(project_dir),
            ]
            _main_inner()

            mock_resume.assert_called_once()
            run_dir_arg, state_arg = mock_resume.call_args[0]
            assert run_dir_arg.run_id == run_id
            assert state_arg.goal == "Add tests"


# ---------------------------------------------------------------------------
# Resume when no incomplete runs exist
# ---------------------------------------------------------------------------


class TestResumeNoIncompleteRuns:
    """--resume when all runs are complete produces clean error."""

    def test_resume_when_all_complete_produces_error(self, tmp_path: Path, capsys):
        """Completed run (has run_end) — find_incomplete_runs returns [], error message."""
        project_dir = tmp_path / "proj"
        project_dir.mkdir()
        runs_root = log._runs_root()
        runs_root.mkdir(parents=True, exist_ok=True)

        _create_complete_run(project_dir, "Already done")

        with (
            patch("kodo.cli._params.has_claude", return_value=True),
            patch("kodo.cli._params.check_api_key", return_value=None),
        ):
            sys.argv = ["kodo", "--resume", "--yes", str(project_dir)]
            with pytest.raises(SystemExit):
                _main_inner()

        err = capsys.readouterr().err
        assert "No incomplete runs found" in err or "incomplete" in err.lower()


# ---------------------------------------------------------------------------
# Resume when no runs directory exists
# ---------------------------------------------------------------------------


class TestResumeNoRunsDirectory:
    """--resume when runs directory doesn't exist produces clean error."""

    def test_resume_when_no_runs_dir_produces_error(self, tmp_path: Path, capsys):
        """Runs directory doesn't exist — clean error, no crash."""
        project_dir = tmp_path / "proj"
        project_dir.mkdir()

        # Redirect to non-existent path (don't create it)
        saved = log._test_snapshot()
        nonexistent = tmp_path / "nonexistent_runs"
        log._test_redirect_runs(nonexistent)
        try:
            with (
                patch("kodo.cli._params.has_claude", return_value=True),
                patch("kodo.cli._params.check_api_key", return_value=None),
            ):
                sys.argv = ["kodo", "--resume", "--yes", str(project_dir)]
                with pytest.raises(SystemExit):
                    _main_inner()

            err = capsys.readouterr().err
            assert "No incomplete runs found" in err or "incomplete" in err.lower()
        finally:
            log._test_restore(saved)
