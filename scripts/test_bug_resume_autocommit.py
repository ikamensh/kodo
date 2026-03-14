"""Test whether auto_commit is ignored on resume.

Starts a run with --no-auto-commit (simulated via cli_args in log), then resumes.
Verifies if auto_commit=False is passed to the orchestrator. If not, reports BUG.

Usage:
    uv run python scripts/test_bug_resume_autocommit.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
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
    repo_root = Path(__file__).resolve().parent.parent
    runs_tmp = repo_root / "tmp_autocommit_test" / "kodo_runs"
    runs_tmp.mkdir(parents=True, exist_ok=True)
    project_dir = repo_root / "tmp_autocommit_test" / "project"
    project_dir.mkdir(parents=True, exist_ok=True)

    saved = log._test_snapshot()
    log._test_redirect_runs(runs_tmp)

    try:
        # Create incomplete run that was started with --no-auto-commit
        run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        run_dir = runs_tmp / run_id
        run_dir.mkdir()
        log_file = run_dir / "log.jsonl"

        events = [
            {
                "ts": "2026-02-24T12:00:00Z",
                "t": 0,
                "event": "run_init",
                "project_dir": str(project_dir.resolve()),
                "version": "0.4.0",
            },
            {
                "ts": "2026-02-24T12:00:00Z",
                "t": 0.1,
                "event": "cli_args",
                "team": "quick",
                "orchestrator": "api",
                "orchestrator_model": "opus",
                "max_exchanges": 20,
                "max_cycles": 3,
                "auto_commit": False,  # Original run had --no-auto-commit
                "goal_text": "Resume auto_commit test",
                "has_plan": False,
            },
            {
                "ts": "2026-02-24T12:00:01Z",
                "t": 1.0,
                "event": "run_start",
                "orchestrator": "api",
                "model": "opus",
                "goal": "Resume auto_commit test",
                "project_dir": str(project_dir.resolve()),
                "max_exchanges": 20,
                "max_cycles": 3,
                "team": {
                    "worker_fast": {"backend": "FakeSession", "model": "fake"},
                    "worker_smart": {"backend": "FakeSession", "model": "fake"},
                },
                "resumed": False,
                "resume_from_cycle": None,
                "has_stages": False,
                "num_stages": 0,
            },
            {
                "ts": "2026-02-24T12:00:02Z",
                "t": 2.0,
                "event": "cycle_end",
                "summary": "Interrupted after cycle 1",
            },
            # No run_end — run is incomplete
        ]
        log_file.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")

        auto_commit_captured: list[bool] = []

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
        ):
            auto_commit_captured.append(auto_commit)
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
            log.emit("cycle_end", summary="Resume done.")
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
                    CycleResult(
                        exchanges=1, finished=True, success=True, summary="Done."
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
            patch("kodo.factory._build_team_mission", _fake_build_team),
            patch("kodo.factory._build_team_saga", _fake_build_team),
            patch("kodo.cli._launch.build_orchestrator", return_value=mock_orch),
        ):
            sys.argv = [
                "kodo",
                "--resume",
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

        if not auto_commit_captured:
            print("FAIL: Orchestrator run() was never called", file=sys.stderr)
            return 1

        actual = auto_commit_captured[0]
        if actual is False:
            print("PASS: auto_commit=False correctly passed on resume")
            return 0
        else:
            print(
                "BUG: auto_commit=True passed on resume (expected False from --no-auto-commit)",
                file=sys.stderr,
            )
            print(
                "  RunState does not store auto_commit; launch_resume defaults to True.",
                file=sys.stderr,
            )
            return 1
    finally:
        log._test_restore(saved)


if __name__ == "__main__":
    sys.exit(main())
