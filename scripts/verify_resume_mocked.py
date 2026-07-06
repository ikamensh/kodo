"""Verify resume flow with mocked orchestrator/sessions.

Run after: uv run python scripts/create_mock_interrupted_run.py

Steps:
1. kodo runs /tmp/kodo_resume_test
2. Resume by project dir (latest incomplete)
3. Re-create mock, then resume by run ID
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unittest.mock import MagicMock, patch

from kodo import log
from kodo.agent import Agent
from kodo.cli import _main_inner
from kodo.orchestrators.base import CycleResult, RunResult
from tests.conftest import FakeSession


def _make_fake_session(*, response_text: str = "Task completed successfully."):
    return FakeSession(response_text=response_text)


def _fake_make_session(backend: str, model: str, **kwargs) -> FakeSession:
    return _make_fake_session()


def _fake_build_team():
    session = _make_fake_session()
    agent = Agent(session, "Test agent", max_turns=5)
    return {"worker_fast": agent, "worker_smart": agent}


def _fake_build_orchestrator(*args, **kwargs):
    """Orchestrator mock that emits resume-aware events."""

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
        **kwargs,
    ):
        start_cycle = (resume.completed_cycles + 1) if resume else 1
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
            resume_from_cycle=start_cycle if resume else None,
            has_stages=plan is not None and len(plan.stages) > 0 if plan else False,
            num_stages=len(plan.stages) if plan and plan.stages else 0,
        )
        log.emit("cycle_end", summary="Resumed cycle done.")
        total = (resume.completed_cycles if resume else 0) + 1
        log.emit(
            "run_end",
            orchestrator="api",
            total_cycles=total,
            finished=True,
            total_cost_usd=0.0,
            total_exchanges=1,
            summary="Done.",
            stages_completed=0,
        )
        return RunResult(
            cycles=[
                CycleResult(
                    exchanges=1,
                    finished=True,
                    success=True,
                    summary="Resumed cycle done.",
                )
            ]
        )

    mock = MagicMock()
    mock.model = "mock"
    mock.run.side_effect = fake_run
    return mock


def run_resume(project_dir: str, run_id: str | None = "__latest__") -> None:
    """Run kodo --resume with mocked orchestrator."""
    if run_id == "__latest__":
        argv = [
            "kodo",
            "--resume",
            "--yes",
            "--team",
            "quick",
            "--orchestrator",
            "api",
            "--project",
            str(project_dir),
        ]
    else:
        argv = [
            "kodo",
            "--resume",
            run_id,
            "--yes",
            "--team",
            "quick",
            "--orchestrator",
            "api",
            "--project",
            str(project_dir),
        ]

    with (
        patch("kodo.cli._intake.make_session", side_effect=_fake_make_session),
        patch("kodo.factory.make_session", side_effect=_fake_make_session),
        patch("kodo.factory.has_claude", return_value=True),
        patch("kodo.factory.has_cursor", return_value=True),
        patch("kodo.factory.has_codex", return_value=False),
        patch("kodo.factory.has_gemini_cli", return_value=False),
        patch("kodo.cli._params.check_api_key", return_value=None),
        patch("kodo.factory._build_team_quick", _fake_build_team),
        patch("kodo.factory._build_team_full", _fake_build_team),
        patch(
            "kodo.cli._launch.build_orchestrator", side_effect=_fake_build_orchestrator
        ),
    ):
        sys.argv = argv
        _main_inner()


def main():
    project_dir = "/tmp/kodo_resume_test"

    # Recreate fresh interrupted run so we have an incomplete run to resume
    script_dir = Path(__file__).resolve().parent
    subprocess.run(
        [sys.executable, str(script_dir / "create_mock_interrupted_run.py")],
        cwd=script_dir.parent,
        check=True,
    )

    print("=== Resume by project dir (latest incomplete) ===\n")
    run_resume(project_dir, "__latest__")

    # Re-create mock for second test (resume by run ID)
    script_dir = Path(__file__).resolve().parent
    subprocess.run(
        [sys.executable, str(script_dir / "create_mock_interrupted_run.py")],
        cwd=script_dir.parent,
        check=True,
    )

    print("\n=== Resume by run ID ===\n")
    run_resume(project_dir, "interrupted_run")


if __name__ == "__main__":
    main()
