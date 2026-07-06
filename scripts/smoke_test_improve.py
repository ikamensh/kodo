"""Smoke test: run kodo --improve with mocked orchestrator and sessions.

Verifies detected project type, 7-stage improve plan, and a successful cycle.
All external deps mocked. No API keys or real backends required.

Usage:
    uv run python scripts/smoke_test_improve.py
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
from scripts.improve_mock_plan import build_mock_discovery_plan, detect_project_type
from tests.conftest import FakeSession


def _fake_make_session(backend: str, model: str, **kwargs) -> FakeSession:
    return FakeSession(response_text="Task completed.")


def _fake_build_team():
    session = FakeSession(response_text="ok")
    agent = Agent(session, "Test agent", max_turns=5)
    return {"worker_fast": agent, "worker_smart": agent}


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    project_dir = repo_root / "tmp_smoke_improve"
    project_dir.mkdir(exist_ok=True)

    # Create a minimal app project (pyproject.toml with [project.scripts])
    pyproject = project_dir / "pyproject.toml"
    pyproject.write_text(
        "[project]\nname = 'smoke-app'\nversion = '0.1.0'\n\n"
        "[project.scripts]\nsmoke-app = 'smoke_app:main'\n",
        encoding="utf-8",
    )
    project_type = detect_project_type(project_dir)
    discovery_plan = build_mock_discovery_plan(project_type)

    plan_captured: list = []
    orchestrator_run_called = []

    def fake_run(
        goal,
        project_dir_arg,
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
        if plan is not None:
            plan_captured.append(plan)
        orchestrator_run_called.append(True)
        log.emit(
            "run_start",
            orchestrator="api",
            model="mock",
            goal=goal,
            project_dir=str(project_dir_arg),
            max_exchanges=max_exchanges,
            max_cycles=max_cycles,
            team={n: {"backend": "FakeSession", "model": "fake"} for n in team},
            resumed=resume is not None,
            resume_from_cycle=(
                (resume.completed_cycles + 1) if resume is not None else None
            ),
            has_stages=plan is not None and len(plan.stages) > 0 if plan else False,
            num_stages=len(plan.stages) if plan and plan.stages else 0,
        )
        log.emit("cycle_end", summary="Improve smoke test cycle done.")
        log.emit(
            "run_end",
            orchestrator="api",
            total_cycles=1,
            finished=True,
            total_cost_usd=0.0,
            total_exchanges=1,
            summary="Improve complete.",
            stages_completed=0,
        )
        return RunResult(
            cycles=[
                CycleResult(
                    exchanges=1, finished=True, success=True, summary="Improve done."
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
        patch("kodo.cli._params.check_api_key", return_value=None),
        patch("kodo.factory._build_team_quick", _fake_build_team),
        patch("kodo.factory._build_team_full", _fake_build_team),
        patch("kodo.cli._launch.build_orchestrator", return_value=mock_orch),
        patch("kodo.cli._main.run_improve_discovery", return_value=discovery_plan),
    ):
        sys.argv = [
            "kodo",
            "--improve",
            "--yes",
            "--json",
            "--project",
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

    if not plan_captured:
        print("FAIL: No plan was passed to orchestrator", file=sys.stderr)
        return 1

    plan = plan_captured[0]
    if project_type != "app":
        print(
            f"FAIL: Expected project type 'app', got {project_type!r}",
            file=sys.stderr,
        )
        return 1

    if len(plan.stages) != 7:
        print(
            f"FAIL: Expected 7-stage improve plan, got {len(plan.stages)}",
            file=sys.stderr,
        )
        return 1

    if plan.context != "Detected project type: app":
        print(f"FAIL: Expected app plan context, got {plan.context!r}", file=sys.stderr)
        return 1

    if plan.stages[0].name != "App Entry Points & Workflows":
        print(
            "FAIL: Expected first stage 'App Entry Points & Workflows', "
            f"got {plan.stages[0].name!r}",
            file=sys.stderr,
        )
        return 1

    print("PASS: Improve smoke test completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
