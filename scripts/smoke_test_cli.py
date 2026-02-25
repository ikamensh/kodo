"""Smoke test: run kodo CLI non-interactively with all external deps mocked.

Verifies the CLI starts, runs a cycle via mocked orchestrator, and completes
without errors. No API keys or real backends required.

Usage:
    uv run python scripts/smoke_test_cli.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unittest.mock import MagicMock, patch

from kodo import log
from kodo.agent import Agent
from kodo.cli import _main_inner
from kodo.orchestrators.base import CycleResult, RunResult
from tests.conftest import FakeSession


def _fake_make_session(backend: str, model: str, **kwargs) -> FakeSession:
    return FakeSession(response_text="Task completed.")


def _fake_build_team():
    session = FakeSession(response_text="ok")
    agent = Agent(session, "Test agent", max_turns=5)
    return {"worker_fast": agent, "worker_smart": agent}


def main() -> int:
    project_dir = Path(__file__).resolve().parent.parent / "tmp_smoke_test"
    project_dir.mkdir(exist_ok=True)

    orchestrator_run_called = []

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
        orchestrator_run_called.append(True)
        log.emit(
            "run_start",
            orchestrator="api",
            model="mock",
            goal=goal,
            project_dir=str(project_dir),
            max_exchanges=max_exchanges,
            max_cycles=max_cycles,
            team={n: {"backend": "FakeSession", "model": "fake"} for n in team},
            resumed=resume is not None,
            resume_from_cycle=resume.completed_cycles if resume is not None else None,
            has_stages=plan is not None and len(plan.stages) > 0 if plan else False,
            num_stages=len(plan.stages) if plan and plan.stages else 0,
        )
        log.emit("cycle_end", summary="Smoke test cycle done.")
        log.emit(
            "run_end",
            orchestrator="api",
            total_cycles=1,
            finished=True,
            total_cost_usd=0.0,
            total_exchanges=1,
            summary="Smoke test complete.",
            stages_completed=0,
        )
        return RunResult(
            cycles=[
                CycleResult(
                    exchanges=1, finished=True, success=True, summary="Smoke test done."
                )
            ]
        )

    mock_orch = MagicMock()
    mock_orch.model = "mock"
    mock_orch.run.side_effect = fake_run

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
        patch("kodo.cli._launch.build_orchestrator", return_value=mock_orch),
    ):
        sys.argv = [
            "kodo",
            "--goal",
            "Smoke test goal",
            "--yes",
            "--team",
            "quick",
            "--skip-intake",
            "--orchestrator",
            "api",
            "--orchestrator-model",
            "opus",
            "--json",
            str(project_dir),
        ]
        try:
            _main_inner()
        except SystemExit as e:
            if e.code != 0:
                print(f"FAIL: CLI exited with code {e.code}", file=sys.stderr)
                return 1
        except Exception as exc:
            print(f"FAIL: {exc}", file=sys.stderr)
            return 1

    if not orchestrator_run_called:
        print("FAIL: Orchestrator run() was never called", file=sys.stderr)
        return 1

    print("PASS: CLI smoke test completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
