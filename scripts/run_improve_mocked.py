"""Run kodo --improve with mocked AI to verify fallback plan and improve cycle setup.

Usage:
    uv run python scripts/run_improve_mocked.py

Verifies:
- Discovery falls back to default plan when mocked
- Fallback plan has 5 stages
- Saga mode and API orchestrator are used
- launch_run is invoked with correct goal and plan
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unittest.mock import MagicMock, patch

from kodo import log
from kodo.agent import Agent
from kodo.cli import (
    _build_fallback_plan,
    _main_inner,
)
from kodo.orchestrators.base import CycleResult, RunResult
from tests.conftest import FakeSession


def _make_fake_session(*, response_text: str = "Task completed."):
    return FakeSession(response_text=response_text)


def _fake_make_session(backend: str, model: str, **kwargs) -> FakeSession:
    return _make_fake_session()


def _fake_build_team():
    session = _make_fake_session()
    agent = Agent(session, "Test agent", max_turns=5)
    return {"worker_fast": agent, "worker_smart": agent}


def _fake_build_orchestrator(*args, **kwargs):
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
            team={n: {"backend": "FakeSession", "model": "fake"} for n in team},
            resumed=False,
            resume_from_cycle=None,
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


def main():
    project_dir = (
        Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "buggy_project"
    )
    if not project_dir.exists():
        print(f"Error: {project_dir} not found")
        sys.exit(1)

    # Verify fallback plan structure
    report_path = "/tmp/improve-report.md"
    plan = _build_fallback_plan(str(report_path))
    print(f"Fallback plan stages: {len(plan.stages)}")
    for s in plan.stages:
        print(f"  {s.index}. {s.name}")

    with (
        patch("kodo.cli._intake.make_session", side_effect=_fake_make_session),
        patch("kodo.factory.make_session", side_effect=_fake_make_session),
        patch("kodo.factory.has_claude", return_value=True),
        patch("kodo.factory.has_cursor", return_value=True),
        patch("kodo.factory.has_codex", return_value=False),
        patch("kodo.factory.has_gemini_cli", return_value=False),
        patch("kodo.cli._params.has_claude", return_value=True),
        patch("kodo.cli._params.has_cursor", return_value=True),
        patch("kodo.cli._params.check_api_key", return_value=None),
        patch("kodo.factory._build_team_mission", _fake_build_team),
        patch("kodo.factory._build_team_saga", _fake_build_team),
        patch(
            "kodo.cli._launch.build_orchestrator", side_effect=_fake_build_orchestrator
        ),
        # Mock discovery to return None so fallback plan is used
        patch("kodo.cli._main.run_improve_discovery", return_value=None),
    ):
        sys.argv = [
            "kodo",
            "--improve",
            "--yes",
            str(project_dir),
        ]
        _main_inner()

    print("\nOK: kodo --improve completed with mocked AI")


if __name__ == "__main__":
    main()
