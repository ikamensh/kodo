"""Integration tests for kodo subcommands: runs, backends, teams.

Creates real run directory structure, patches runs path, and invokes
subcommand handlers to verify end-to-end behavior.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch


from kodo import log
from kodo.cli._subcommands import _cmd_backends, _cmd_runs, _cmd_teams


def _create_fake_run(
    runs_root: Path,
    run_id: str,
    project_dir: str,
    goal: str,
    *,
    finished: bool = False,
) -> None:
    """Create a fake run directory with run.jsonl mimicking kodo log format."""
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True)
    events = [
        {
            "ts": "2025-01-15T12:00:00Z",
            "t": 0,
            "event": "run_start",
            "goal": goal,
            "project_dir": project_dir,
            "orchestrator": "api",
            "model": "gemini-flash",
            "max_exchanges": 30,
            "max_cycles": 5,
            "team": ["worker_fast", "worker_smart"],
        },
        {"ts": "2025-01-15T12:00:01Z", "t": 1, "event": "cli_args", "team": "saga"},
        {
            "ts": "2025-01-15T12:01:00Z",
            "t": 60,
            "event": "cycle_end",
            "summary": f"worked on: {goal[:40]}",
        },
    ]
    if finished:
        events.append({"ts": "2025-01-15T12:02:00Z", "t": 120, "event": "run_end"})
    lines = [json.dumps(e) for e in events]
    (run_dir / "run.jsonl").write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# kodo runs — real run directory
# ---------------------------------------------------------------------------


class TestCmdRunsIntegration:
    """kodo runs against real run directory structure."""

    def test_runs_lists_created_runs(self, tmp_path: Path, capsys):
        """Create fake runs, invoke _cmd_runs, verify output."""
        runs_root = log._runs_root()  # conftest redirects to tmp_path/kodo_runs
        runs_root.mkdir(parents=True, exist_ok=True)

        project = str(tmp_path / "my_project")
        _create_fake_run(
            runs_root, "20250115_120000", project, "Build a REST API", finished=True
        )
        _create_fake_run(
            runs_root, "20250115_130000", project, "Add tests", finished=False
        )

        sys.argv = ["kodo", "runs"]
        _cmd_runs()

        out = capsys.readouterr().out
        assert "No runs found." not in out
        assert "20250115_120000" in out
        assert "20250115_130000" in out
        assert "Build a REST API" in out
        assert "Add tests" in out
        assert "RUN ID" in out
        assert "done" in out
        assert "cycle" in out

    def test_runs_filtered_by_project_dir(self, tmp_path: Path, capsys):
        """Filter runs by project_dir shows only matching runs."""
        runs_root = log._runs_root()
        runs_root.mkdir(parents=True, exist_ok=True)

        project_a = str((tmp_path / "proj_a").resolve())
        project_b = str((tmp_path / "proj_b").resolve())
        (tmp_path / "proj_a").mkdir()
        (tmp_path / "proj_b").mkdir()

        _create_fake_run(runs_root, "run_a", project_a, "Goal A", finished=True)
        _create_fake_run(runs_root, "run_b", project_b, "Goal B", finished=True)

        sys.argv = ["kodo", "runs", project_a]
        _cmd_runs()

        out = capsys.readouterr().out
        assert "run_a" in out
        assert "Goal A" in out
        assert "run_b" not in out
        assert "Goal B" not in out

    def test_runs_no_runs_prints_message(self, tmp_path: Path, capsys):
        """Empty runs directory prints 'No runs found.'"""
        # conftest provides empty runs dir
        sys.argv = ["kodo", "runs"]
        _cmd_runs()

        out = capsys.readouterr().out
        assert "No runs found." in out


# ---------------------------------------------------------------------------
# kodo backends — verify no crash
# ---------------------------------------------------------------------------


class TestCmdBackendsIntegration:
    """kodo backends runs without crashing."""

    def test_backends_completes(self, capsys):
        """_cmd_backends runs to completion."""
        mock_avail = MagicMock()
        mock_avail.return_value = {
            "claude": False,
            "codex": False,
            "cursor": False,
            "gemini-cli": False,
        }
        mock_avail.cache_clear = lambda: None
        with (
            patch("kodo.factory.available_backends", mock_avail),
            patch("kodo.factory.check_api_key", return_value="no key"),
        ):
            sys.argv = ["kodo", "backends"]
            _cmd_backends()

        out = capsys.readouterr().out
        assert "CLI backends (agents):" in out
        assert "Orchestrator models (API):" in out


# ---------------------------------------------------------------------------
# kodo teams — verify lists presets
# ---------------------------------------------------------------------------


class TestCmdTeamsIntegration:
    """kodo teams lists available presets."""

    def test_teams_list_shows_presets(self, capsys):
        """_cmd_teams lists built-in team presets."""
        mock_avail = MagicMock()
        mock_avail.return_value = {
            "claude": False,
            "codex": False,
            "cursor": False,
            "gemini-cli": False,
        }
        mock_avail.cache_clear = lambda: None
        with patch("kodo.factory.available_backends", mock_avail):
            sys.argv = ["kodo", "teams"]
            _cmd_teams()

        out = capsys.readouterr().out
        assert "saga" in out or "mission" in out or "quick" in out
