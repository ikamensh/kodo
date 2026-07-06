"""Smoke test: simulate interactive CLI session with mocked input/questionary.

Happy path: enter goal, select full team, api orchestrator (gemini-flash),
skip refinement, confirm. Verifies the run completes successfully.
All external deps mocked. No API keys or real backends required.

Usage:
    uv run python scripts/smoke_test_interactive.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unittest.mock import MagicMock, patch

from kodo import log
from kodo.agent import Agent
from kodo.cli import _main_inner
from kodo.factory import TEAMS
from kodo.orchestrators.base import CycleResult, RunResult
from tests.conftest import FakeSession


def _fake_make_session(backend: str, model: str, **kwargs) -> FakeSession:
    return FakeSession(response_text="Task completed.")


def _fake_build_team():
    session = FakeSession(response_text="ok")
    agent = Agent(session, "Test agent", max_turns=5)
    return {"worker_fast": agent, "worker_smart": agent}


def main() -> int:
    project_dir = Path(__file__).resolve().parent.parent / "tmp_smoke_interactive"
    project_dir.mkdir(exist_ok=True)
    # Remove config so we always get full select_params flow (not "Reuse?")
    kodo_dir = project_dir / ".kodo"
    if kodo_dir.exists():
        for f in kodo_dir.glob("config*.json"):
            f.unlink()

    # Build full-team option string (matches select_params format)
    team_preset = TEAMS["full"]
    team_option = f"{team_preset.name} — {team_preset.description}"

    # Ordered responses for questionary.select().ask()
    # Order: Team, Orchestrator, Model, Max exchanges, Max cycles, Refine goal
    select_responses = iter(
        [
            team_option,
            "api (recommended — delegates cleanly, pay-per-token)",
            "gemini-flash",
            "30",
            "5",
            "Skip",
        ]
    )

    # Ordered responses for input(): goal line 1, goal line 2 (empty), Proceed
    input_responses = iter(
        [
            "Build a REST API for todo management",
            "",  # empty line ends goal input
            "y",  # Proceed? [Y/n]
        ]
    )

    def mock_select(title, choices=None, **kwargs):
        try:
            return next(select_responses)
        except StopIteration:
            raise AssertionError(
                f"questionary.select called too many times (title={title!r})"
            )

    def mock_input(prompt=""):
        try:
            return next(input_responses)
        except StopIteration:
            raise AssertionError(f"input() called too many times (prompt={prompt!r})")

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
            resume_from_cycle=(resume.completed_cycles + 1)
            if resume is not None
            else None,
            has_stages=plan is not None and len(plan.stages) > 0 if plan else False,
            num_stages=len(plan.stages) if plan and plan.stages else 0,
        )
        log.emit("cycle_end", summary="Interactive smoke test done.")
        log.emit(
            "run_end",
            orchestrator="api",
            total_cycles=1,
            finished=True,
            total_cost_usd=0.0,
            total_exchanges=1,
            summary="Complete.",
            stages_completed=0,
        )
        return RunResult(
            cycles=[
                CycleResult(exchanges=1, finished=True, success=True, summary="Done.")
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
        patch("kodo.cli._intake._stdin_has_data", return_value=False),
        patch("builtins.input", side_effect=mock_input),
        patch(
            "questionary.select",
            side_effect=lambda title, **kw: MagicMock(ask=lambda: mock_select(title)),
        ),
        patch(
            "questionary.text", side_effect=lambda *a, **kw: MagicMock(ask=lambda: "30")
        ),
    ):
        sys.argv = ["kodo", "--project", str(project_dir)]
        try:
            _main_inner()
        except SystemExit as e:
            if e.code != 0:
                print(f"FAIL: CLI exited with code {e.code}", file=sys.stderr)
                return 1
        except Exception as exc:
            print(f"FAIL: {exc}", file=sys.stderr)
            return 1

    if not mock_orch.run.called:
        print("FAIL: Orchestrator run() was never called", file=sys.stderr)
        return 1

    print("PASS: Interactive smoke test completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
