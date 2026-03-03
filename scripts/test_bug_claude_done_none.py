"""Test whether the Claude orchestrator crashes when the done tool gets summary=None.

The LLM may pass null for the summary parameter when invoking the done tool.
Verifies handle_done and the staged run flow (which uses cycle_result.summary[:1000]).

Usage:
    uv run python scripts/test_bug_claude_done_none.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kodo import log
from kodo.log import RunDir
from kodo.orchestrators.base import (
    CycleResult,
    DoneSignal,
    GoalPlan,
    GoalStage,
    OrchestratorBase,
    handle_done,
)
from tests.conftest import make_agent


class FakeOrchestrator(OrchestratorBase):
    """Returns a CycleResult with summary=None to simulate done(summary=null)."""

    def __init__(self):
        self.model = "test"
        self._orchestrator_name = "test"
        self._summarizer = MagicMock()

    def cycle(self, *args, **kwargs):
        return CycleResult(summary=None, finished=True, success=True)


def main() -> int:
    project_dir = Path(__file__).resolve().parent.parent / "tmp_done_none_test"
    project_dir.mkdir(exist_ok=True)

    log.init(RunDir.create(project_dir, "done_none_test"))

    # 1. handle_done with summary=None
    done_signal = DoneSignal()
    team = {
        "tester": make_agent("ALL CHECKS PASS"),
        "architect": make_agent("ALL CHECKS PASS"),
    }
    try:
        handle_done(
            summary=None,
            success=True,
            done_signal=done_signal,
            goal="Test goal",
            team=team,
            project_dir=project_dir,
            auto_commit=False,
        )
    except Exception as e:
        print(
            f"BUG: handle_done crashed on summary=None: {type(e).__name__}: {e}",
            file=sys.stderr,
        )
        return 1

    # 2. Staged run with cycle returning summary=None (triggers summary[:1000])
    plan = GoalPlan(
        context="Test",
        stages=[
            GoalStage(
                index=1,
                name="S1",
                description="Do it",
                acceptance_criteria="Done",
            )
        ],
    )
    orch = FakeOrchestrator()
    team = {"worker": make_agent()}

    try:
        with patch("kodo.orchestrators.base.open_viewer", create=True):
            result = orch.run(
                "Test goal",
                project_dir,
                team,
                max_cycles=2,
                plan=plan,
            )
    except Exception as e:
        print(f"BUG: Uncaught {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    # Check if a stage crashed due to summary=None (cycle_result.summary[:1000])
    for sr in result.stage_results:
        if "Stage crashed" in sr.summary and "NoneType" in sr.summary:
            print(
                "BUG: Staged run crashes on summary=None (cycle_result.summary[:1000])",
                file=sys.stderr,
            )
            print(f"  {sr.summary}", file=sys.stderr)
            return 1

    print("PASS: handle_done and staged run handle summary=None")
    return 0


if __name__ == "__main__":
    sys.exit(main())
