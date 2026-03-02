"""Integration tests: create synthetic runs and verify kodo's run listing.

These test the full path from JSONL on disk → list_runs / _cmd_runs → user-visible output.
"""

from __future__ import annotations

import json
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

from kodo import log
from kodo.cli import _cmd_runs
from kodo.log import RunDir

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RUN_START_TEMPLATE = {
    "event": "run_start",
    "orchestrator": "api",
    "model": "gemini-flash",
    "max_exchanges": 30,
    "max_cycles": 5,
    "team": ["worker_fast", "worker_smart"],
}
_CLI_ARGS = {"event": "cli_args", "team": "full"}


def _emit(events: list[dict]) -> list[str]:
    return [json.dumps({"ts": "2025-01-01T00:00:00Z", "t": 0, **e}) for e in events]


def _create_run(run_id: str, project_dir: str, goal: str, *, finished: bool = False):
    """Write a minimal valid run to the central store."""
    events = [
        {**_RUN_START_TEMPLATE, "goal": goal, "project_dir": project_dir},
        _CLI_ARGS,
        {"event": "cycle_end", "summary": f"worked on: {goal[:30]}"},
    ]
    if finished:
        events.append({"event": "run_end"})

    d = log._runs_root() / run_id
    d.mkdir(parents=True)
    (d / "run.jsonl").write_text("\n".join(_emit(events)) + "\n")


# ---------------------------------------------------------------------------
# RunDir.create writes to central store
# ---------------------------------------------------------------------------


class TestRunDirCreatesCentrally:
    """When a run is created, files should land in ~/.kodo/runs/, not the project."""

    def test_run_dir_not_under_project(self, tmp_path: Path):
        project = tmp_path / "myproject"
        project.mkdir()
        run_dir = RunDir.create(project, "test123")

        # The run directory should exist
        assert run_dir.root.exists()
        # It must NOT be under the project directory
        assert not str(run_dir.root).startswith(str(project))
        # It should be under the central runs root
        assert str(run_dir.root).startswith(str(log._runs_root()))

    def test_goal_file_writes_centrally(self, tmp_path: Path):
        project = tmp_path / "proj"
        project.mkdir()
        run_dir = RunDir.create(project, "run_goal")
        run_dir.goal_file.write_text("Build something cool")

        assert run_dir.goal_file.read_text() == "Build something cool"
        assert not (project / ".kodo").exists()


# ---------------------------------------------------------------------------
# kodo runs command output
# ---------------------------------------------------------------------------


class TestCmdRuns:
    """The 'kodo runs' command should produce readable output."""

    def _run_cmd(self, *args: str) -> str:
        """Execute _cmd_runs with given args, capturing stdout."""
        buf = StringIO()
        with patch.object(sys, "argv", ["kodo", "runs", *args]):
            with patch("sys.stdout", buf):
                _cmd_runs()
        return buf.getvalue()

    def test_no_runs_shows_message(self):
        output = self._run_cmd()
        assert "No runs found" in output

    def test_shows_run_id_and_goal(self, tmp_path: Path):
        _create_run("20250315_120000", str(tmp_path), "Add user auth")
        output = self._run_cmd()
        assert "20250315_120000" in output
        assert "Add user auth" in output

    def test_finished_run_shows_done(self, tmp_path: Path):
        _create_run("run_done", str(tmp_path), "Fix bug", finished=True)
        output = self._run_cmd()
        assert "done" in output

    def test_incomplete_run_shows_cycle_count(self, tmp_path: Path):
        _create_run("run_wip", str(tmp_path), "Refactor", finished=False)
        output = self._run_cmd()
        assert "cycle" in output
        assert "1/" in output  # "cycle 1/5"

    def test_filter_by_project_dir(self, tmp_path: Path):
        proj_a = tmp_path / "alpha"
        proj_a.mkdir()
        proj_b = tmp_path / "beta"
        proj_b.mkdir()
        _create_run("run_a", str(proj_a), "Alpha goal")
        _create_run("run_b", str(proj_b), "Beta goal")

        output = self._run_cmd(str(proj_a))
        assert "Alpha goal" in output
        assert "Beta goal" not in output

    def test_multiple_runs_all_shown(self, tmp_path: Path):
        for i in range(5):
            _create_run(f"run_{i:03d}", str(tmp_path), f"Goal number {i}")
        output = self._run_cmd()
        for i in range(5):
            assert f"Goal number {i}" in output

    def test_long_goal_truncated(self, tmp_path: Path):
        long_goal = "x" * 200
        _create_run("run_long", str(tmp_path), long_goal)
        output = self._run_cmd()
        # Should be truncated, not the full 200 chars
        assert "x" * 200 not in output
        assert "..." in output


# ---------------------------------------------------------------------------
# End-to-end: init → log → list
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Resume by run ID resolves through central store
# ---------------------------------------------------------------------------


class TestResumeById:
    """--resume=<run_id> should find the run in the central store."""

    def test_resume_finds_run_in_central_store(self, tmp_path: Path):
        project = tmp_path / "proj"
        project.mkdir()
        _create_run("20250315_120000", str(project), "Fix auth bug")

        from kodo.cli import _main_inner

        with (
            patch("kodo.cli._main.launch_resume") as mock_resume,
            patch.object(
                sys,
                "argv",
                ["kodo", "--resume", "20250315_120000", "--yes", "--project", str(project)],
            ),
        ):
            _main_inner()

        assert mock_resume.called
        run_dir = mock_resume.call_args[0][0]
        assert run_dir.run_id == "20250315_120000"

    def test_resume_nonexistent_run_fails(self, tmp_path: Path):
        project = tmp_path / "proj"
        project.mkdir()

        from kodo.cli import _main_inner

        with (
            patch.object(
                sys, "argv", ["kodo", "--resume", "nonexistent", "--project", str(project)]
            ),
            pytest.raises(SystemExit),
        ):
            _main_inner()


# ---------------------------------------------------------------------------
# End-to-end: init → log → list
# ---------------------------------------------------------------------------


class TestInitAndDiscover:
    """A run created via log.init should be discoverable via list_runs."""

    def test_created_run_appears_in_listing(self, tmp_path: Path):
        project = tmp_path / "proj"
        project.mkdir()
        run_dir = RunDir.create(project, "discovery_test")
        log.init(run_dir)
        log.emit(
            "run_start",
            goal="test discovery",
            orchestrator="api",
            model="m",
            project_dir=str(project),
            max_exchanges=10,
            max_cycles=3,
            team=[],
        )
        log.emit("cli_args", team="full")
        log.emit("cycle_end", summary="partial work")

        runs = log.list_runs(project)
        assert len(runs) == 1
        assert runs[0].goal == "test discovery"
        assert runs[0].run_id == "discovery_test"

    def test_find_incomplete_skips_finished(self, tmp_path: Path):
        project = tmp_path / "proj"
        project.mkdir()
        run_dir = RunDir.create(project, "finished_run")
        log.init(run_dir)
        log.emit(
            "run_start",
            goal="done goal",
            orchestrator="api",
            model="m",
            project_dir=str(project),
            max_exchanges=10,
            max_cycles=3,
            team=[],
        )
        log.emit("cli_args", team="full")
        log.emit("cycle_end", summary="all done")
        log.emit("run_end")

        # list_runs sees it
        assert len(log.list_runs(project)) == 1
        # find_incomplete_runs does not
        assert len(log.find_incomplete_runs(project)) == 0
