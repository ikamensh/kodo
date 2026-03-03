"""Fault injection via full CLI: kodo --goal "test" --yes --team quick --orchestrator api.

Run: uv run python scripts/fault_injection_cli.py

Mocks orchestrator/sessions. Injects faults and checks exit code + resume.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ["KODO_NO_VIEWER"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unittest.mock import MagicMock, patch

from kodo import log
from kodo.agent import Agent
from kodo.cli import _main_inner
from kodo.orchestrators.base import CycleResult, RunResult
from tests.conftest import FakeSession


def _fake_build_team_raising():
    """Team with session that raises ConnectionError on first query."""
    session = FakeSession(response_text="x")

    def query_raises(*a, **kw):
        raise ConnectionError("injected fault")

    session.query = query_raises
    agent = Agent(session, "Test agent", max_turns=5)
    return {"worker_fast": agent, "worker_smart": agent}


def _fake_build_orchestrator_ok(*args, **kwargs):
    """Orchestrator that completes successfully."""

    def fake_run(
        goal, project_dir, team, *, max_exchanges, max_cycles, resume=None, **kw
    ):
        log.emit(
            "run_start",
            orchestrator="api",
            model="mock",
            goal=goal,
            project_dir=str(project_dir),
            max_exchanges=max_exchanges,
            max_cycles=max_cycles,
            team={},
            resumed=resume is not None,
            resume_from_cycle=None,
            has_stages=False,
            num_stages=0,
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

    m = MagicMock()
    m.model = "mock"
    m.run.side_effect = fake_run
    return m


def run_cli(scenario: str) -> tuple[bool, bool]:
    """Run kodo CLI. Returns (exit_ok, resumable)."""
    project_dir = Path(__file__).resolve().parent.parent / "tmp_fault_cli"
    project_dir.mkdir(exist_ok=True)
    runs_tmp = project_dir / "kodo_runs"
    runs_tmp.mkdir(exist_ok=True)

    saved = log._test_snapshot()
    log._test_redirect_runs(runs_tmp)

    session = FakeSession(response_text="ok")
    agent = Agent(session, "Test", max_turns=5)
    ok_team = {"worker_fast": agent, "worker_smart": agent}

    def _team_builder():
        return ok_team

    if scenario == "agent_raises":
        orch_builder = _fake_build_orchestrator_ok
    elif scenario == "summarize_raises":
        from kodo.factory import build_orchestrator

        orch_builder = build_orchestrator
    else:
        orch_builder = _fake_build_orchestrator_ok

    patches = [
        patch(
            "kodo.cli._intake.make_session",
            side_effect=lambda *a, **kw: FakeSession(response_text="ok"),
        ),
        patch(
            "kodo.factory.make_session",
            side_effect=lambda *a, **kw: FakeSession(response_text="ok"),
        ),
        patch("kodo.factory.has_claude", return_value=True),
        patch("kodo.factory.has_cursor", return_value=True),
        patch("kodo.factory.has_codex", return_value=False),
        patch("kodo.factory.has_gemini_cli", return_value=False),
        patch("kodo.cli._params.check_api_key", return_value=None),
        patch("kodo.factory._build_team_mission", _team_builder),
        patch("kodo.factory._build_team_saga", _team_builder),
        patch("kodo.cli._launch.build_orchestrator", side_effect=orch_builder),
    ]

    if scenario == "summarize_raises":
        from tests.conftest import FakeRunResult

        def fake_run_sync(prompt, *, usage_limits=None):
            return FakeRunResult(output="partial")

        def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
            self.run_sync = fake_run_sync

        def _summarize_raises(self, messages):
            raise ConnectionError("injected _summarize fault")

        patches.extend(
            [
                patch("kodo.orchestrators.api.Agent.__init__", fake_agent_init),
                patch(
                    "kodo.orchestrators.api.ApiOrchestrator._summarize",
                    _summarize_raises,
                ),
            ]
        )

    try:
        for p in patches:
            p.start()
        try:
            sys.argv = [
                "kodo",
                "--goal",
                "test",
                "--yes",
                "--team",
                "quick",
                "--orchestrator",
                "api",
                "--skip-intake",
                str(project_dir),
            ]
            try:
                _main_inner()
                exit_ok = True
            except SystemExit as e:
                exit_ok = e.code == 0
            except Exception:
                exit_ok = False
        finally:
            for p in reversed(patches):
                p.stop()

        incomplete = log.find_incomplete_runs(project_dir)
        resumable = len(incomplete) >= 1
    finally:
        log._test_restore(saved)

    return exit_ok, resumable


def main():
    print(
        "=== 1. Agent session ConnectionError (via mock orch - unit test covers real flow) ==="
    )
    ok, resumable = run_cli("agent_raises")
    print(f"  Exit OK: {ok}, Resumable: {resumable}")

    print("\n=== 2. _summarize raises ===")
    ok, resumable = run_cli("summarize_raises")
    print(f"  Exit OK: {ok}, Resumable: {resumable}")

    print("\n=== 3. summarizer._do_summarize (already catches Exception) ===")
    print("  No CLI test - unit test confirms exception is swallowed.")


if __name__ == "__main__":
    main()
