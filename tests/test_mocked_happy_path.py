"""Happy-path integration tests using run_cli_mocked.py pattern.

Exercises launch_run, launch_resume, subcommands, and error paths.
No API keys or real backends required. Mirrors scripts/run_cli_mocked.py.
"""

from __future__ import annotations

import contextlib
import json
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kodo import log
from kodo.agent import Agent
from kodo.cli import _main_inner
from kodo.cli._subcommands import _cmd_backends, _cmd_runs, _cmd_teams
from kodo.orchestrators.base import CycleResult, RunResult
from tests.conftest import FakeSession

# ---------------------------------------------------------------------------
# Helpers for subcommand / run creation
# ---------------------------------------------------------------------------


def _create_run(run_id: str, project_dir: str, goal: str, *, finished: bool = False):
    """Write a minimal valid run to the central store."""
    events = [
        {
            "event": "run_start",
            "goal": goal,
            "project_dir": project_dir,
            "orchestrator": "api",
            "model": "gemini-flash",
            "max_exchanges": 30,
            "max_cycles": 5,
            "team": ["worker_fast", "worker_smart"],
        },
        {"event": "cli_args", "team": "full"},
        {"event": "cycle_end", "summary": f"worked on: {goal[:30]}"},
    ]
    if finished:
        events.append({"event": "run_end"})
    lines = [json.dumps({"ts": "2025-01-01T00:00:00Z", "t": 0, **e}) for e in events]
    d = log._runs_root() / run_id
    d.mkdir(parents=True)
    (d / "run.jsonl").write_text("\n".join(lines) + "\n")


def _make_fake_session(*, response_text: str = "Task completed successfully."):
    return FakeSession(response_text=response_text)


def _fake_make_session(backend: str, model: str, **kwargs) -> FakeSession:
    return _make_fake_session()


def _fake_build_team():
    session = _make_fake_session()
    agent = Agent(session, "Test agent", max_turns=5)
    return {"worker_fast": agent, "worker_smart": agent}


def _fake_build_orchestrator(*args, **kwargs):
    """Orchestrator mock whose run() emits log events and returns success."""

    def fake_run(
        goal,
        project_dir,
        team,
        *,
        max_exchanges,
        max_cycles,
        resume=None,
        plan=None,
        verifiers=None,
        auto_commit=True,
    ):
        log.emit(
            "run_start",
            orchestrator="api",
            model="mock",
            goal=goal,
            project_dir=str(project_dir),
            max_exchanges=max_exchanges,
            max_cycles=max_cycles,
            team={n: {"backend": "FakeSession", "model": "fake-model"} for n in team},
            resumed=resume is not None,
            resume_from_cycle=resume.completed_cycles if resume else None,
            has_stages=plan is not None and len(plan.stages) > 0 if plan else False,
            num_stages=len(plan.stages) if plan and plan.stages else 0,
        )
        log.emit("cycle_end", summary="Done.")
        log.emit(
            "run_end",
            orchestrator="api",
            total_cycles=1,
            finished=True,
            total_cost_usd=0.0,
            total_exchanges=1,
            summary="Done.",
            stages_completed=0,
        )
        return RunResult(
            cycles=[
                CycleResult(exchanges=1, finished=True, success=True, summary="Done.")
            ]
        )

    mock = MagicMock()
    mock.model = "mock"
    mock.run.side_effect = fake_run
    return mock


def _mocked_patches():
    """Patches matching scripts/run_cli_mocked.py for CLI happy path."""
    return (
        patch("kodo.cli._intake.make_session", side_effect=_fake_make_session),
        patch("kodo.factory.make_session", side_effect=_fake_make_session),
        patch("kodo.factory.has_claude", return_value=True),
        patch("kodo.factory.has_cursor", return_value=True),
        patch("kodo.factory.has_codex", return_value=False),
        patch("kodo.factory.has_gemini_cli", return_value=False),
        patch("kodo.cli._params.preferred_orchestrator", return_value="claude-code"),
        patch("kodo.cli._params.check_api_key", return_value=None),
        patch("kodo.factory._build_team_quick", _fake_build_team),
        patch("kodo.factory._build_team_full", _fake_build_team),
        patch(
            "kodo.cli._launch.build_orchestrator", side_effect=_fake_build_orchestrator
        ),
        patch("kodo.cli._main.run_improve_discovery", return_value=None),
        patch("kodo.cli._launch.preflight_check_backends", return_value=[]),
    )


class TestMockedRunHappyPath:
    """Full CLI run path with mocked backends — exercises launch_run."""

    def test_noninteractive_run_completes(self, tmp_path: Path):
        """--goal --yes --skip-intake runs launch_run and completes."""
        project = tmp_path / "proj"
        project.mkdir()

        with contextlib.ExitStack() as stack:
            for p in _mocked_patches():
                stack.enter_context(p)
            sys.argv = [
                "kodo",
                "--goal",
                "Echo hello world",
                "--yes",
                "--team",
                "quick",
                "--skip-intake",
                "--orchestrator",
                "api",
                "--orchestrator-model",
                "opus",
                "--project",
                str(project),
            ]
            _main_inner()

            runs = log.list_runs(project)
            assert len(runs) >= 1
            assert runs[0].goal == "Echo hello world"
            assert runs[0].finished

    def test_run_creates_goal_and_config_in_run_dir(self, tmp_path: Path):
        """Run writes goal.md and config.json to central run dir."""
        project = tmp_path / "proj"
        project.mkdir()

        with contextlib.ExitStack() as stack:
            for p in _mocked_patches():
                stack.enter_context(p)
            sys.argv = [
                "kodo",
                "--goal",
                "Build a thing",
                "--yes",
                "--team",
                "quick",
                "--skip-intake",
                "--project",
                str(project),
            ]
            _main_inner()

            runs = log.list_runs(project)
            assert len(runs) == 1
            run_dir = runs[0].log_file.parent
            assert (run_dir / "goal.md").exists()
            assert (run_dir / "goal.md").read_text().strip() == "Build a thing"
            assert (run_dir / "config.json").exists()
            config = json.loads((run_dir / "config.json").read_text())
            assert config["team"] == "quick"  # --team quick stored as-is


class TestMockedResumeHappyPath:
    """Full CLI resume path with mocked backends — exercises launch_resume."""

    def _create_incomplete_run(self, run_id: str, project: Path, goal: str) -> Path:
        """Create an incomplete run (no run_end) for resume testing."""
        events = [
            {
                "event": "run_start",
                "goal": goal,
                "project_dir": str(project),
                "orchestrator": "api",
                "model": "opus",
                "max_exchanges": 20,
                "max_cycles": 1,
                "team": ["worker_fast", "worker_smart"],
            },
            {"event": "cli_args", "team": "full"},
            {"event": "cycle_end", "summary": "partial work"},
        ]
        lines = [
            json.dumps({"ts": "2025-01-01T00:00:00Z", "t": 0, **e}) for e in events
        ]
        run_root = log._runs_root() / run_id
        run_root.mkdir(parents=True)
        (run_root / "run.jsonl").write_text("\n".join(lines) + "\n")
        (run_root / "goal.md").write_text(goal)
        (run_root / "config.json").write_text(
            json.dumps(
                {
                    "team": "full",
                    "orchestrator": "api",
                    "orchestrator_model": "opus",
                    "max_exchanges": 20,
                    "max_cycles": 1,
                },
                indent=2,
            )
        )
        return run_root / "run.jsonl"

    def test_resume_completes(self, tmp_path: Path):
        """--resume RUN_ID runs launch_resume and completes."""
        project = tmp_path / "proj"
        project.mkdir()
        run_id = "20250315_120000"
        self._create_incomplete_run(run_id, project, "Fix auth bug")

        with contextlib.ExitStack() as stack:
            for p in _mocked_patches():
                stack.enter_context(p)
            sys.argv = [
                "kodo",
                "--resume",
                run_id,
                "--yes",
                "--project",
                str(project),
            ]
            _main_inner()

            runs = log.list_runs(project)
            assert len(runs) >= 1
            assert any(r.run_id == run_id for r in runs)


# ---------------------------------------------------------------------------
# Subcommands: kodo runs, kodo backends, kodo teams
# ---------------------------------------------------------------------------


class TestSubcommandRuns:
    """kodo runs — list runs from central store."""

    def test_runs_no_runs_shows_message(self):
        buf = StringIO()
        with patch.object(sys, "argv", ["kodo", "runs"]):
            with patch("sys.stdout", buf):
                _cmd_runs()
        assert "No runs found" in buf.getvalue()

    def test_runs_shows_run_id_and_goal(self, tmp_path: Path):
        project = tmp_path / "proj"
        project.mkdir()
        _create_run("20250315_120000", str(project), "Add user auth")
        buf = StringIO()
        with patch.object(sys, "argv", ["kodo", "runs"]):
            with patch("sys.stdout", buf):
                _cmd_runs()
        out = buf.getvalue()
        assert "20250315_120000" in out
        assert "Add user auth" in out

    def test_runs_filter_by_project_dir(self, tmp_path: Path):
        proj_a = tmp_path / "alpha"
        proj_a.mkdir()
        proj_b = tmp_path / "beta"
        proj_b.mkdir()
        _create_run("run_a", str(proj_a), "Alpha goal")
        _create_run("run_b", str(proj_b), "Beta goal")
        buf = StringIO()
        with patch.object(sys, "argv", ["kodo", "runs", str(proj_a)]):
            with patch("sys.stdout", buf):
                _cmd_runs()
        out = buf.getvalue()
        assert "Alpha goal" in out
        assert "Beta goal" not in out


class TestSubcommandBackends:
    """kodo backends — list CLI backends and API keys."""

    def test_backends_prints_cli_and_orchestrator_sections(self):
        buf = StringIO()
        fake_proc = MagicMock(returncode=0, stdout="1.0.0\n")
        with patch.object(sys, "argv", ["kodo", "backends"]):
            with patch("sys.stdout", buf):
                with patch("subprocess.run", return_value=fake_proc):
                    _cmd_backends()
        out = buf.getvalue()
        assert "CLI backends" in out
        assert "Orchestrator models" in out
        assert "API keys" in out


class TestSubcommandTeams:
    """kodo teams — list/add/edit team configs."""

    def test_teams_list_prints_teams(self):
        buf = StringIO()
        with patch.object(sys, "argv", ["kodo", "teams"]):
            with patch("sys.stdout", buf):
                _cmd_teams()
        out = buf.getvalue()
        assert "full" in out or "quick" in out or "No teams found" in out

    def test_teams_add_requires_name(self):
        buf = StringIO()
        with patch.object(sys, "argv", ["kodo", "teams", "add"]):
            with patch("sys.stdout", buf):
                with pytest.raises(SystemExit):
                    _cmd_teams()
        assert "Usage" in buf.getvalue()

    def test_teams_edit_requires_name(self):
        buf = StringIO()
        with patch.object(sys, "argv", ["kodo", "teams", "edit"]):
            with patch("sys.stdout", buf):
                with pytest.raises(SystemExit):
                    _cmd_teams()
        assert "Usage" in buf.getvalue()

    def test_teams_unknown_subcommand_exits(self):
        buf = StringIO()
        with patch.object(sys, "argv", ["kodo", "teams", "unknown"]):
            with patch("sys.stdout", buf):
                with pytest.raises(SystemExit):
                    _cmd_teams()
        assert "Unknown" in buf.getvalue()


# ---------------------------------------------------------------------------
# --goal-file and --improve
# ---------------------------------------------------------------------------


class TestGoalFile:
    """--goal-file reads goal from a file."""

    def test_goal_file_runs_with_file_content(self, tmp_path: Path):
        project = tmp_path / "proj"
        project.mkdir()
        goal_file = tmp_path / "my-goal.md"
        goal_file.write_text("Build a REST API for todos\n\nWith auth.")

        with contextlib.ExitStack() as stack:
            for p in _mocked_patches():
                stack.enter_context(p)
            sys.argv = [
                "kodo",
                "--goal-file",
                str(goal_file),
                "--yes",
                "--skip-intake",
                "--team",
                "quick",
                "--project",
                str(project),
            ]
            _main_inner()

            runs = log.list_runs(project)
            assert len(runs) >= 1
            assert "REST API" in runs[0].goal
            assert "auth" in runs[0].goal


class TestImprove:
    """--improve runs the improve flow with staged plan."""

    def test_improve_runs_and_completes(self, tmp_path: Path):
        project = tmp_path / "proj"
        project.mkdir()
        (project / "main.py").write_text("print('hello')")

        with contextlib.ExitStack() as stack:
            for p in _mocked_patches():
                stack.enter_context(p)
            sys.argv = [
                "kodo",
                "--improve",
                "--yes",
                "--project",
                str(project),
            ]
            _main_inner()

            runs = log.list_runs(project)
            assert len(runs) >= 1
            run_dir = runs[0].log_file.parent
            assert (run_dir / "goal.md").exists()
            assert "improve" in (run_dir / "goal.md").read_text().lower()


# ---------------------------------------------------------------------------
# Error paths: OSError on goal.md, KeyError in get_team
# ---------------------------------------------------------------------------


class TestGoalMdOserror:
    """Interactive mode: OSError when reading goal.md falls back to get_goal."""

    def test_oserror_on_goal_md_falls_back_to_get_goal(self, tmp_path: Path):
        project = tmp_path / "proj"
        project.mkdir()
        goal_md = project / "goal.md"
        goal_md.write_text("Original goal in file")

        original_read_text = Path.read_text

        def patched_read_text(self, *args, **kwargs):
            if self.resolve() == goal_md.resolve():
                raise OSError(13, "Permission denied")
            return original_read_text(self, *args, **kwargs)

        with (
            patch.object(Path, "read_text", patched_read_text),
            patch(
                "kodo.cli._main.get_goal", return_value="Fallback goal from get_goal"
            ),
            patch("kodo.cli._main._offer_intake", return_value=None),
            patch("kodo.cli._main._load_or_select_params") as mock_params,
            patch("kodo.cli._main.launch_run") as mock_launch,
            patch("builtins.input", return_value="y"),
        ):
            mock_params.return_value = {
                "team": "full",
                "orchestrator": "api",
                "orchestrator_model": "gemini-flash",
                "max_exchanges": 30,
                "max_cycles": 5,
            }
            sys.argv = ["kodo", "--project", str(project)]
            _main_inner()

        mock_launch.assert_called_once()
        goal_arg = mock_launch.call_args[0][1]
        assert goal_arg == "Fallback goal from get_goal"


class TestGetTeamKeyError:
    """Invalid team in params triggers KeyError from get_team."""

    def test_invalid_mode_exits_with_error(self, tmp_path: Path):
        project = tmp_path / "proj"
        project.mkdir()

        def fake_build_params(args, project_dir):
            return {
                "team": "invalid_mode",
                "orchestrator": "api",
                "orchestrator_model": "gemini-flash",
                "max_exchanges": 30,
                "max_cycles": 5,
            }

        with (
            patch(
                "kodo.cli._main._build_params_from_flags", side_effect=fake_build_params
            ),
            patch("kodo.cli._params.preferred_orchestrator", return_value="claude-code"),
            patch("kodo.cli._params.check_api_key", return_value=None),
        ):
            sys.argv = [
                "kodo",
                "--goal",
                "Build X",
                "--yes",
                "--skip-intake",
                "--project",
                str(project),
            ]
            with pytest.raises(SystemExit):
                _main_inner()
