"""Tests for staged/parallel execution in OrchestratorBase.

Uses ApiOrchestrator with mocked pydantic-ai Agent to exercise
_run_staged, parallel groups, stage failure, and verify_done retry.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch


from kodo import log
from kodo.log import RunDir
from kodo.orchestrators.api import ApiOrchestrator
from kodo.orchestrators.base import GoalPlan, GoalStage
from tests.conftest import FakeRunResult, make_agent


def _make_fake_team():
    return {"worker": make_agent(response_text="Task completed.")}


def _make_sequential_plan(n: int = 2) -> GoalPlan:
    return GoalPlan(
        context="Test project",
        stages=[
            GoalStage(
                index=i + 1,
                name=f"Stage {i + 1}",
                description=f"Description {i + 1}",
                acceptance_criteria=f"Done when {i + 1}",
            )
            for i in range(n)
        ],
    )


def _make_parallel_plan() -> GoalPlan:
    """S1 sequential, S2+S3 parallel, S4 sequential."""
    return GoalPlan(
        context="Test parallel",
        stages=[
            GoalStage(
                index=1, name="Setup", description="d1", acceptance_criteria="c1"
            ),
            GoalStage(
                index=2,
                name="TestA",
                description="d2",
                acceptance_criteria="c2",
                parallel_group=1,
            ),
            GoalStage(
                index=3,
                name="TestB",
                description="d3",
                acceptance_criteria="c3",
                parallel_group=1,
            ),
            GoalStage(index=4, name="Fix", description="d4", acceptance_criteria="c4"),
        ],
    )


# ---------------------------------------------------------------------------
# Test 1: Simple staged run with 2 sequential stages
# ---------------------------------------------------------------------------


@patch("kodo.orchestrators.base.open_viewer", create=True)
def test_staged_run_two_sequential_stages(mock_viewer, tmp_path: Path):
    """ApiOrchestrator with plan: 2 stages execute in order, summaries propagated."""
    run_dir = RunDir.create(tmp_path, "staged_seq")
    log.init(run_dir)

    agent_tools = []
    cycle_count = [0]

    def fake_run_sync(prompt, *, usage_limits=None):
        cycle_count[0] += 1
        for t in agent_tools:
            if t.name == "done":
                summary = f"stage {cycle_count[0]} done"
                t.function(summary=summary, success=True)
                break
        return FakeRunResult()

    def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
        nonlocal agent_tools
        agent_tools = tools or []
        self.run_sync = fake_run_sync

    team = _make_fake_team()
    plan = _make_sequential_plan(2)

    with (
        patch("kodo.orchestrators.api.Agent.__init__", fake_agent_init),
        patch("kodo.orchestrators.base.verify_done", return_value=None),
    ):
        orch = ApiOrchestrator(model="claude-opus-4-6")
        result = orch.run(
            "Build feature",
            tmp_path,
            team,
            max_exchanges=10,
            max_cycles=5,
            plan=plan,
        )

    assert len(result.stage_results) == 2
    assert all(sr.finished for sr in result.stage_results)
    assert result.stage_results[0].summary == "stage 1 done"
    assert result.stage_results[1].summary == "stage 2 done"
    assert result.finished


# ---------------------------------------------------------------------------
# Test 2: Parallel stages with mocked worktrees
# ---------------------------------------------------------------------------


@patch("kodo.orchestrators.base.open_viewer", create=True)
def test_parallel_stages_with_mocked_worktrees(mock_viewer, tmp_path: Path):
    """Parallel stages run; create_worktree/remove_worktree mocked."""
    run_dir = RunDir.create(tmp_path, "staged_par")
    log.init(run_dir)

    worktrees_created = []
    worktrees_removed = []

    def fake_create_worktree(project_dir: Path, label: str):
        wt = tmp_path / f"wt-{label}-{len(worktrees_created)}"
        wt.mkdir(parents=True)
        branch = f"kodo-{label}-{len(worktrees_created)}"
        worktrees_created.append((str(wt), branch))
        return wt, branch

    def fake_remove_worktree(project_dir: Path, worktree_dir: Path, branch: str):
        worktrees_removed.append((str(worktree_dir), branch))
        if worktree_dir.exists():
            import shutil

            shutil.rmtree(worktree_dir, ignore_errors=True)

    agent_tools = []
    cycle_count = [0]

    def fake_run_sync(prompt, *, usage_limits=None):
        cycle_count[0] += 1
        for t in agent_tools:
            if t.name == "done":
                t.function(summary=f"cycle {cycle_count[0]} done", success=True)
                break
        return FakeRunResult()

    def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
        nonlocal agent_tools
        agent_tools = tools or []
        self.run_sync = fake_run_sync

    team = _make_fake_team()
    plan = _make_parallel_plan()

    with (
        patch("kodo.orchestrators.api.Agent.__init__", fake_agent_init),
        patch("kodo.orchestrators.base.verify_done", return_value=None),
        patch(
            "kodo.orchestrators.base.create_worktree", side_effect=fake_create_worktree
        ),
        patch(
            "kodo.orchestrators.base.remove_worktree", side_effect=fake_remove_worktree
        ),
    ):
        orch = ApiOrchestrator(model="claude-opus-4-6")
        result = orch.run(
            "Parallel goal",
            tmp_path,
            team,
            max_exchanges=10,
            max_cycles=10,
            plan=plan,
        )

    assert len(result.stage_results) == 4
    assert len(worktrees_created) >= 2
    assert len(worktrees_removed) >= 2
    summaries = [sr.summary for sr in result.stage_results]
    assert len(summaries) == 4


# ---------------------------------------------------------------------------
# Test 3: Stage failure doesn't crash the run
# ---------------------------------------------------------------------------


@patch("kodo.orchestrators.base.open_viewer", create=True)
def test_stage_failure_handled_gracefully(mock_viewer, tmp_path: Path):
    """When a stage raises, run catches it and records StageResult with error."""
    run_dir = RunDir.create(tmp_path, "staged_fail")
    log.init(run_dir)

    call_count = [0]

    def fake_run_sync(prompt, *, usage_limits=None):
        call_count[0] += 1
        if call_count[0] == 2:
            raise RuntimeError("Simulated stage crash")
        for t in agent_tools:
            if t.name == "done":
                t.function(summary="ok", success=True)
                break
        return FakeRunResult()

    agent_tools = []

    def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
        nonlocal agent_tools
        agent_tools = tools or []
        self.run_sync = fake_run_sync

    team = _make_fake_team()
    plan = _make_sequential_plan(3)

    with (
        patch("kodo.orchestrators.api.Agent.__init__", fake_agent_init),
        patch("kodo.orchestrators.base.verify_done", return_value=None),
    ):
        orch = ApiOrchestrator(model="claude-opus-4-6")
        result = orch.run(
            "Goal",
            tmp_path,
            team,
            max_exchanges=10,
            max_cycles=10,
            plan=plan,
        )

    assert len(result.stage_results) >= 2
    assert result.stage_results[0].finished
    assert "crashed" in result.stage_results[1].summary.lower()
    assert not result.stage_results[1].finished


# ---------------------------------------------------------------------------
# Test 4: verify_done rejection causes retry
# ---------------------------------------------------------------------------


@patch("kodo.orchestrators.base.open_viewer", create=True)
def test_verify_done_rejection_causes_retry(mock_viewer, tmp_path: Path):
    """verify_done returns rejection on first attempt; second attempt passes."""
    run_dir = RunDir.create(tmp_path, "staged_verify")
    log.init(run_dir)

    agent_tools = []

    def fake_run_sync(prompt, *, usage_limits=None):
        for t in agent_tools:
            if t.name == "done":
                t.function(summary="all done", success=True)
                break
        return FakeRunResult()

    def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
        nonlocal agent_tools
        agent_tools = tools or []
        self.run_sync = fake_run_sync

    verify_attempts = [0]

    def fake_verify_done(*args, **kwargs):
        verify_attempts[0] += 1
        if verify_attempts[0] == 1:
            return "DONE REJECTED — issues found"
        return None

    team = _make_fake_team()

    # Agent calls done twice in same "turn": first gets rejection, second passes
    def fake_run_sync_with_retry(prompt, *, usage_limits=None):
        for t in agent_tools:
            if t.name == "done":
                t.function(summary="all done", success=True)  # 1st: rejected
                t.function(summary="all done", success=True)  # 2nd: passes
                break
        return FakeRunResult()

    def fake_agent_init_verify(
        self, model, *, system_prompt=None, tools=None, **kwargs
    ):
        nonlocal agent_tools
        agent_tools = tools or []
        self.run_sync = fake_run_sync_with_retry

    with (
        patch("kodo.orchestrators.api.Agent.__init__", fake_agent_init_verify),
        patch("kodo.orchestrators.base.verify_done", side_effect=fake_verify_done),
    ):
        orch = ApiOrchestrator(model="claude-opus-4-6")
        result = orch.cycle(
            "Single cycle goal",
            tmp_path,
            team,
            max_exchanges=10,
        )

    assert verify_attempts[0] >= 2
    assert result.finished is True
