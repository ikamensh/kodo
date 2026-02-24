"""End-to-end tests for launch_run() and launch_resume() data flow.

These are the critical integration points between CLI and orchestrator.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from kodo.cli._launch import (
    _emit_json_and_exit,
    launch_run,
    launch_resume,
)
from kodo.factory import TeamPreset
from kodo.log import RunDir, RunState
from kodo.orchestrators.base import CycleResult, RunResult
from tests.conftest import make_agent


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _make_params(**overrides) -> dict:
    defaults = {
        "team": "saga",
        "orchestrator": "api",
        "orchestrator_model": "gemini-flash",
        "max_exchanges": 30,
        "max_cycles": 5,
        "auto_commit": True,
    }
    defaults.update(overrides)
    return defaults


def _make_fake_team_preset() -> TeamPreset:
    """TeamPreset whose build_team returns a minimal fake team."""
    return TeamPreset(
        name="test-team",
        description="Test team",
        system_prompt="You are a test orchestrator.",
        build_team=lambda: {"worker_fast": make_agent()},
        default_max_exchanges=30,
        default_max_cycles=5,
    )


# ---------------------------------------------------------------------------
# Test 1: launch_run creates RunDir, snapshots config, invokes orchestrator
# ---------------------------------------------------------------------------


def test_launch_run_creates_rundir_config_goal_and_invokes_orchestrator(tmp_path: Path):
    """launch_run() creates correct RunDir, snapshots config, goal.md, and invokes orchestrator."""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    goal_text = "Add a hello world endpoint"
    params = _make_params()

    canned_result = RunResult(
        cycles=[CycleResult(exchanges=1, finished=True, summary="Done.")]
    )
    fake_orchestrator = MagicMock()
    fake_orchestrator.run.return_value = canned_result

    with (
        patch("kodo.cli._launch.get_team", return_value=_make_fake_team_preset()),
        patch("kodo.cli._launch.build_orchestrator", return_value=fake_orchestrator),
        patch("kodo.cli._launch.preflight_check_backends", return_value=[]),
        patch("kodo.cli._launch.load_team_config", return_value=None),
    ):
        run_dir = RunDir.create(project_dir)
        result = launch_run(run_dir, goal_text, params)

    assert result is canned_result
    assert run_dir.config_file.exists()
    config = json.loads(run_dir.config_file.read_text(encoding="utf-8"))
    assert config["team"] == "saga"
    assert config["max_exchanges"] == 30

    assert run_dir.goal_file.exists()
    assert run_dir.goal_file.read_text(encoding="utf-8") == goal_text

    assert run_dir.log_file.exists()
    log_lines = run_dir.log_file.read_text(encoding="utf-8").strip().split("\n")
    assert len(log_lines) >= 2  # run_init + cli_args
    events = [json.loads(ln) for ln in log_lines]
    assert any(e.get("event") == "cli_args" for e in events)

    fake_orchestrator.run.assert_called_once()
    call_kw = fake_orchestrator.run.call_args.kwargs
    assert call_kw["max_exchanges"] == 30
    assert call_kw["max_cycles"] == 5
    assert call_kw.get("resume") is None


# ---------------------------------------------------------------------------
# Test 2: launch_run with json_mode + _emit_json_and_exit produces valid JSON
# ---------------------------------------------------------------------------


def test_launch_run_json_mode_produces_valid_json_on_stdout(tmp_path: Path):
    """When json_mode=True and _emit_json_and_exit is called, valid JSON is written to stdout."""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    goal_text = "Build a CLI tool"
    params = _make_params()

    canned_result = RunResult(
        cycles=[CycleResult(exchanges=2, finished=True, summary="Complete.")]
    )
    fake_orchestrator = MagicMock()
    fake_orchestrator.run.return_value = canned_result

    out = io.StringIO()
    args = SimpleNamespace(json=True)

    with (
        patch("kodo.cli._launch.get_team", return_value=_make_fake_team_preset()),
        patch("kodo.cli._launch.build_orchestrator", return_value=fake_orchestrator),
        patch("kodo.cli._launch.preflight_check_backends", return_value=[]),
        patch("kodo.cli._launch.load_team_config", return_value=None),
        patch("kodo.cli._launch._original_stdout", out),
    ):
        run_dir = RunDir.create(project_dir)
        result = launch_run(run_dir, goal_text, params, json_mode=True)
        with pytest.raises(SystemExit):
            _emit_json_and_exit(args, result)

    output = out.getvalue()
    parsed = json.loads(output)
    assert parsed["status"] == "completed"
    assert parsed["finished"] is True
    assert parsed["cycles"] == 1
    assert parsed["exchanges"] == 2
    assert "summary" in parsed


# ---------------------------------------------------------------------------
# Test 3: launch_resume restores state and passes to orchestrator
# ---------------------------------------------------------------------------


def test_launch_resume_restores_state_and_passes_to_orchestrator(tmp_path: Path):
    """launch_resume() restores RunState and passes resume=ResumeState to orchestrator.run()."""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    run_id = "20250224_120000"
    runs_root = tmp_path / "kodo_runs"
    runs_root.mkdir(parents=True, exist_ok=True)
    run_root = runs_root / run_id
    run_root.mkdir()

    log_file = run_root / "run.jsonl"
    run_start = {
        "ts": "2025-02-24T12:00:00Z",
        "t": 0,
        "event": "run_start",
        "goal": "Implement feature X",
        "orchestrator": "api",
        "model": "gemini-flash",
        "project_dir": str(project_dir),
        "max_exchanges": 30,
        "max_cycles": 5,
        "team": ["worker_fast"],
    }
    cli_args = {
        "ts": "2025-02-24T12:00:00Z",
        "t": 0,
        "event": "cli_args",
        "team": "saga",
        "orchestrator": "api",
        "orchestrator_model": "gemini-flash",
        "max_exchanges": 30,
        "max_cycles": 5,
    }
    cycle_end = {
        "ts": "2025-02-24T12:05:00Z",
        "t": 300,
        "event": "cycle_end",
        "summary": "Partial progress on feature X.",
    }
    with log_file.open("w") as f:
        for evt in [run_start, cli_args, cycle_end]:
            f.write(json.dumps(evt) + "\n")

    config_file = run_root / "config.json"
    config_file.write_text(json.dumps(_make_params(), indent=2), encoding="utf-8")

    state = RunState(
        run_id=run_id,
        log_file=log_file,
        goal="Implement feature X",
        orchestrator="api",
        model="gemini-flash",
        project_dir=str(project_dir),
        max_exchanges=30,
        max_cycles=5,
        team=["worker_fast"],
        completed_cycles=1,
        last_summary="Partial progress on feature X.",
        finished=False,
        agent_session_ids={"worker_fast": "sess-123"},
        team_preset="saga",
        has_stages=False,
        completed_stages=[],
        stage_summaries=[],
        current_stage_cycles=1,
        pending_exchanges=[{"task": "continue from here"}],
    )

    run_dir = RunDir(project_dir=project_dir, run_id=run_id)
    canned_result = RunResult(
        cycles=[CycleResult(exchanges=1, finished=True, summary="Resumed and done.")]
    )
    fake_orchestrator = MagicMock()
    fake_orchestrator.run.return_value = canned_result

    with (
        patch("kodo.cli._launch.get_team", return_value=_make_fake_team_preset()),
        patch("kodo.cli._launch.build_orchestrator", return_value=fake_orchestrator),
        patch("kodo.cli._launch.load_team_config", return_value=None),
    ):
        result = launch_resume(run_dir, state)

    assert result is canned_result
    fake_orchestrator.run.assert_called_once()
    call_kw = fake_orchestrator.run.call_args.kwargs
    resume = call_kw["resume"]
    assert resume is not None
    assert resume.completed_cycles == 1
    assert resume.prior_summary == "Partial progress on feature X."
    assert resume.agent_session_ids == {"worker_fast": "sess-123"}
    assert resume.pending_exchanges == [{"task": "continue from here"}]


# ---------------------------------------------------------------------------
# Test 4: launch_run handles orchestrator crash
# ---------------------------------------------------------------------------


def test_launch_run_orchestrator_crash_propagates_cleanly(tmp_path: Path):
    """When orchestrator.run() raises, launch_run propagates the exception (no silent swallow)."""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    goal_text = "Do something"
    params = _make_params()

    fake_orchestrator = MagicMock()
    fake_orchestrator.run.side_effect = RuntimeError("Orchestrator crashed")

    with (
        patch("kodo.cli._launch.get_team", return_value=_make_fake_team_preset()),
        patch("kodo.cli._launch.build_orchestrator", return_value=fake_orchestrator),
        patch("kodo.cli._launch.preflight_check_backends", return_value=[]),
        patch("kodo.cli._launch.load_team_config", return_value=None),
    ):
        run_dir = RunDir.create(project_dir)
        with pytest.raises(RuntimeError, match="Orchestrator crashed"):
            launch_run(run_dir, goal_text, params)
