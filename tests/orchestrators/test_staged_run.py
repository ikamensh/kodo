"""Tests for staged goal execution (GoalPlan, compose_stage_goal, staged run)."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kodo import log
from kodo.log import RunDir
from kodo.orchestrators.base import (
    CycleConfig,
    CycleResult,
    GoalPlan,
    GoalStage,
    OrchestratorBase,
    ResumeState,
    _remove_worktree_keep_branch,
    commit_worktree_changes,
    compose_stage_goal,
    create_worktree,
    execution_groups,
    merge_worktree_branch,
    remove_worktree,
)
from tests.conftest import make_agent


# ── Helpers ───────────────────────────────────────────────────────────────


@pytest.fixture()
def mock_worktrees():
    """Patch create/remove_worktree so parallel stage tests work without git."""
    _dirs: list[Path] = []

    def _fake_create(project_dir, label):
        d = Path(tempfile.mkdtemp(prefix=f"kodo-{label}-"))
        branch = f"kodo-{label}-fake"
        _dirs.append(d)
        return d, branch

    def _fake_remove(project_dir, wt_dir, branch):
        pass  # cleanup handled by tmp

    with (
        patch(
            "kodo.orchestrators.parallel.create_worktree",
            autospec=True,
            side_effect=_fake_create,
        ),
        patch(
            "kodo.orchestrators.parallel.remove_worktree",
            autospec=True,
            side_effect=_fake_remove,
        ),
    ):
        yield
    import shutil

    for d in _dirs:
        shutil.rmtree(d, ignore_errors=True)


# ── compose_stage_goal tests ─────────────────────────────────────────────


def _make_plan(num_stages: int = 3) -> GoalPlan:
    return GoalPlan(
        context="Python web app using Flask",
        stages=[
            GoalStage(
                index=i + 1,
                name=f"Stage {i + 1}",
                description=f"Description for stage {i + 1}",
                acceptance_criteria=f"Tests pass for stage {i + 1}",
            )
            for i in range(num_stages)
        ],
    )


def test_compose_stage_goal_first_stage():
    plan = _make_plan()
    goal = compose_stage_goal(plan, 1, [])

    assert "Python web app using Flask" in goal
    assert "Stage 1" in goal
    assert "Description for stage 1" in goal
    assert "Tests pass for stage 1" in goal
    # Should have next stage preview
    assert "Stage 2" in goal
    # No completed stages section
    assert "Completed Stages" not in goal


def test_compose_stage_goal_middle_stage_with_summaries():
    plan = _make_plan()
    summaries = ["Stage 1 is done: built the models"]
    goal = compose_stage_goal(plan, 2, summaries)

    assert "Completed Stages" in goal
    assert "Stage 1 is done: built the models" in goal
    assert "Stage 2" in goal
    assert "Description for stage 2" in goal
    # Next stage preview
    assert "Stage 3" in goal


def test_compose_stage_goal_last_stage_no_next():
    plan = _make_plan()
    summaries = ["s1 done", "s2 done"]
    goal = compose_stage_goal(plan, 3, summaries)

    assert "Description for stage 3" in goal
    # No next stage preview for last stage
    assert "Next Stage Preview" not in goal


def test_compose_stage_goal_single_stage():
    plan = GoalPlan(
        context="Simple script",
        stages=[
            GoalStage(
                index=1,
                name="Do it",
                description="Build the thing",
                acceptance_criteria="It works",
            ),
        ],
    )
    goal = compose_stage_goal(plan, 1, [])
    assert "Simple script" in goal
    assert "Do it" in goal
    assert "Next Stage Preview" not in goal


# ── Staged run() tests ──────────────────────────────────────────────────


class FakeOrchestrator(OrchestratorBase):
    """Minimal orchestrator for testing staged run() logic."""

    def __init__(self, cycle_results: list[CycleResult] | None = None):
        self.model = "test-model"
        self._orchestrator_name = "test"
        self._summarizer = MagicMock()
        self._cycle_results = list(cycle_results or [])
        self._cycle_calls: list[dict] = []

    def cycle(
        self,
        goal: str,
        project_dir: Path,
        team,
        *,
        max_exchanges: int = 30,
        prior_summary: str = "",
        config=None,
    ) -> CycleResult:
        self._cycle_calls.append(
            {
                "goal": goal,
                "prior_summary": prior_summary,
            }
        )
        if self._cycle_results:
            return self._cycle_results.pop(0)
        return CycleResult(summary="cycle done")


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    log.init(RunDir.create(tmp_path))
    return tmp_path


@patch("kodo.orchestrators.base.open_viewer", create=True)  # noqa: autospec
def test_staged_run_all_stages_complete(mock_viewer, tmp_project):
    """Each stage finishes in 1 cycle; all 3 stages should complete."""
    plan = _make_plan(3)
    orch = FakeOrchestrator(
        cycle_results=[
            CycleResult(summary="stage 1 done", finished=True),
            CycleResult(summary="stage 2 done", finished=True),
            CycleResult(summary="stage 3 done", finished=True),
        ]
    )
    team = {"worker": make_agent()}

    with patch("kodo.viewer.open_viewer", create=True):  # noqa: autospec
        result = orch.run(
            "overall goal",
            tmp_project,
            team,
            max_cycles=10,
            plan=plan,
        )

    assert len(result.stage_results) == 3
    assert all(sr.finished for sr in result.stage_results)
    assert len(result.cycles) == 3
    assert result.finished  # last cycle was finished


@patch("kodo.orchestrators.base.open_viewer", create=True)  # noqa: autospec
def test_staged_run_stage_takes_multiple_cycles(mock_viewer, tmp_project):
    """Stage 1 takes 2 cycles; stage 2 takes 1 cycle."""
    plan = _make_plan(2)
    orch = FakeOrchestrator(
        cycle_results=[
            CycleResult(summary="partial s1"),
            CycleResult(summary="stage 1 done", finished=True),
            CycleResult(summary="stage 2 done", finished=True),
        ]
    )
    team = {"worker": make_agent()}

    with patch("kodo.viewer.open_viewer", create=True):  # noqa: autospec
        result = orch.run(
            "goal",
            tmp_project,
            team,
            max_cycles=10,
            plan=plan,
        )

    assert len(result.stage_results) == 2
    assert result.stage_results[0].finished
    assert len(result.stage_results[0].cycles) == 2
    assert result.stage_results[1].finished
    assert len(result.stage_results[1].cycles) == 1


@patch("kodo.orchestrators.base.open_viewer", create=True)  # noqa: autospec
def test_staged_run_budget_exhausted(mock_viewer, tmp_project):
    """With max_cycles=2 and 3 stages, run stops when budget exhausted."""
    plan = _make_plan(3)
    orch = FakeOrchestrator(
        cycle_results=[
            CycleResult(summary="stage 1 done", finished=True),
            CycleResult(summary="partial s2"),
            # No more cycles available
        ]
    )
    team = {"worker": make_agent()}

    with patch("kodo.viewer.open_viewer", create=True):  # noqa: autospec
        result = orch.run(
            "goal",
            tmp_project,
            team,
            max_cycles=2,
            plan=plan,
        )

    assert len(result.stage_results) == 2
    assert result.stage_results[0].finished
    assert not result.stage_results[1].finished  # budget ran out


@patch("kodo.orchestrators.base.open_viewer", create=True)  # noqa: autospec
def test_staged_run_stage_failure_stops_run(mock_viewer, tmp_project):
    """If stage 1 uses all budget without finishing, run stops."""
    plan = _make_plan(2)
    orch = FakeOrchestrator(
        cycle_results=[
            CycleResult(summary="partial"),
            CycleResult(summary="still partial"),
        ]
    )
    team = {"worker": make_agent()}

    with patch("kodo.viewer.open_viewer", create=True):  # noqa: autospec
        result = orch.run(
            "goal",
            tmp_project,
            team,
            max_cycles=2,
            plan=plan,
        )

    # Only stage 1 was attempted, and it didn't finish
    assert len(result.stage_results) == 1
    assert not result.stage_results[0].finished


@patch("kodo.orchestrators.base.open_viewer", create=True)  # noqa: autospec
def test_staged_run_no_plan_uses_single_mode(mock_viewer, tmp_project):
    """With plan=None, staged run is not used (backward compat)."""
    orch = FakeOrchestrator(
        cycle_results=[
            CycleResult(summary="done", finished=True),
        ]
    )
    team = {"worker": make_agent()}

    with patch("kodo.viewer.open_viewer", create=True):  # noqa: autospec
        result = orch.run(
            "goal",
            tmp_project,
            team,
            max_cycles=5,
            plan=None,
        )

    assert len(result.cycles) == 1
    assert result.finished
    assert result.stage_results == []


@patch("kodo.orchestrators.base.open_viewer", create=True)  # noqa: autospec
def test_staged_run_cycle_has_stage_index(mock_viewer, tmp_project):
    """Cycle results from staged runs should have stage_index set."""
    plan = _make_plan(2)
    orch = FakeOrchestrator(
        cycle_results=[
            CycleResult(summary="s1 done", finished=True),
            CycleResult(summary="s2 done", finished=True),
        ]
    )
    team = {"worker": make_agent()}

    with patch("kodo.viewer.open_viewer", create=True):  # noqa: autospec
        result = orch.run(
            "goal",
            tmp_project,
            team,
            max_cycles=10,
            plan=plan,
        )

    assert result.cycles[0].stage_index == 1
    assert result.cycles[1].stage_index == 2


@patch("kodo.orchestrators.base.open_viewer", create=True)  # noqa: autospec
def test_staged_run_goal_includes_completed_summaries(mock_viewer, tmp_project):
    """After stage 1 completes, stage 2's goal should include stage 1's summary."""
    plan = _make_plan(2)
    orch = FakeOrchestrator(
        cycle_results=[
            CycleResult(summary="built the models", finished=True),
            CycleResult(summary="added the API", finished=True),
        ]
    )
    team = {"worker": make_agent()}

    with patch("kodo.viewer.open_viewer", create=True):  # noqa: autospec
        orch.run("goal", tmp_project, team, max_cycles=10, plan=plan)

    # Second cycle's goal should mention stage 1's summary
    stage2_goal = orch._cycle_calls[1]["goal"]
    assert "built the models" in stage2_goal


# ── Staged resume tests ─────────────────────────────────────────────────


@patch("kodo.orchestrators.base.open_viewer", create=True)  # noqa: autospec
def test_staged_resume_skips_completed_stages(mock_viewer, tmp_project):
    """Resuming after stage 1 completed should start at stage 2."""
    plan = _make_plan(3)
    orch = FakeOrchestrator(
        cycle_results=[
            CycleResult(summary="stage 2 done", finished=True),
            CycleResult(summary="stage 3 done", finished=True),
        ]
    )
    team = {"worker": make_agent()}

    resume = ResumeState(
        completed_cycles=1,
        prior_summary="",
        agent_session_ids={},
        completed_stages=[1],
        stage_summaries=["stage 1 was done"],
        current_stage_cycles=0,
    )

    with patch("kodo.viewer.open_viewer", create=True):  # noqa: autospec
        result = orch.run(
            "goal",
            tmp_project,
            team,
            max_cycles=5,
            plan=plan,
            resume=resume,
        )

    # Should have started at stage 2, completed stages 2 and 3
    assert len(result.stage_results) == 2
    assert result.stage_results[0].stage_index == 2
    assert result.stage_results[1].stage_index == 3
    # Stage 2's goal should include stage 1's summary from resume
    assert "stage 1 was done" in orch._cycle_calls[0]["goal"]


# ── Log parsing tests ────────────────────────────────────────────────────


def _cli_args_event(**overrides):
    return {
        "ts": "t",
        "t": 0.1,
        "event": "cli_args",
        "team": "full",
        **overrides,
    }


def test_log_parse_stages(tmp_path):
    """parse_run() should extract stage tracking info from log events."""
    log_file = tmp_path / "test.jsonl"
    events = [
        {
            "ts": "t",
            "t": 0,
            "event": "run_start",
            "goal": "g",
            "orchestrator": "api",
            "model": "m",
            "project_dir": "/p",
            "max_exchanges": 10,
            "max_cycles": 5,
            "team": [],
            "has_stages": True,
            "num_stages": 2,
        },
        _cli_args_event(),
        {
            "ts": "t",
            "t": 1,
            "event": "stage_start",
            "stage_index": 1,
            "stage_name": "S1",
        },
        {"ts": "t", "t": 2, "event": "cycle_end", "summary": "partial"},
        {
            "ts": "t",
            "t": 3,
            "event": "stage_end",
            "stage_index": 1,
            "stage_name": "S1",
            "finished": True,
            "summary": "s1 done",
        },
        {
            "ts": "t",
            "t": 4,
            "event": "stage_start",
            "stage_index": 2,
            "stage_name": "S2",
        },
        {"ts": "t", "t": 5, "event": "cycle_end", "summary": "partial s2"},
    ]
    log_file.write_text("\n".join(json.dumps(e) for e in events))

    state = log.parse_run(log_file)
    assert state is not None
    assert state.has_stages is True
    assert state.completed_stages == [1]
    assert state.stage_summaries == ["s1 done"]
    assert state.completed_cycles == 2


def test_log_parse_no_stages(tmp_path):
    """parse_run() with no stage events should have empty stage fields."""
    log_file = tmp_path / "test.jsonl"
    events = [
        {
            "ts": "t",
            "t": 0,
            "event": "run_start",
            "goal": "g",
            "orchestrator": "api",
            "model": "m",
            "project_dir": "/p",
            "max_exchanges": 10,
            "max_cycles": 5,
            "team": [],
        },
        _cli_args_event(),
        {"ts": "t", "t": 1, "event": "cycle_end", "summary": "done"},
        {"ts": "t", "t": 2, "event": "run_end"},
    ]
    log_file.write_text("\n".join(json.dumps(e) for e in events))

    state = log.parse_run(log_file)
    assert state is not None
    assert state.has_stages is False
    assert state.completed_stages == []
    assert state.stage_summaries == []


# ── execution_groups() tests ─────────────────────────────────────────────


class TestExecutionGroups:
    """Tests for execution_groups() — pure function grouping stages."""

    def test_all_sequential(self):
        """Stages with no parallel_group are each their own group."""
        plan = _make_plan(3)
        groups = execution_groups(plan)
        assert len(groups) == 3
        assert all(len(g) == 1 for g in groups)

    def test_parallel_group_collapses(self):
        """Stages with the same parallel_group end up in one group."""
        plan = GoalPlan(
            context="test",
            stages=[
                GoalStage(index=1, name="S1", description="d", acceptance_criteria="c"),
                GoalStage(
                    index=2,
                    name="S2",
                    description="d",
                    acceptance_criteria="c",
                    parallel_group=1,
                ),
                GoalStage(
                    index=3,
                    name="S3",
                    description="d",
                    acceptance_criteria="c",
                    parallel_group=1,
                ),
                GoalStage(index=4, name="S4", description="d", acceptance_criteria="c"),
            ],
        )
        groups = execution_groups(plan)
        assert len(groups) == 3  # S1, [S2,S3], S4
        assert len(groups[0]) == 1
        assert len(groups[1]) == 2
        assert len(groups[2]) == 1

    def test_parallel_group_ordering(self):
        """Parallel group is inserted at position of first member."""
        plan = GoalPlan(
            context="test",
            stages=[
                GoalStage(index=1, name="A", description="d", acceptance_criteria="c"),
                GoalStage(
                    index=2,
                    name="B",
                    description="d",
                    acceptance_criteria="c",
                    parallel_group=1,
                ),
                GoalStage(
                    index=3,
                    name="C",
                    description="d",
                    acceptance_criteria="c",
                    parallel_group=1,
                ),
                GoalStage(index=4, name="D", description="d", acceptance_criteria="c"),
            ],
        )
        groups = execution_groups(plan)
        assert groups[0][0].name == "A"
        assert {s.name for s in groups[1]} == {"B", "C"}
        assert groups[2][0].name == "D"

    def test_multiple_parallel_groups(self):
        """Multiple distinct parallel groups."""
        plan = GoalPlan(
            context="test",
            stages=[
                GoalStage(
                    index=1,
                    name="S1",
                    description="d",
                    acceptance_criteria="c",
                    parallel_group=1,
                ),
                GoalStage(
                    index=2,
                    name="S2",
                    description="d",
                    acceptance_criteria="c",
                    parallel_group=1,
                ),
                GoalStage(index=3, name="S3", description="d", acceptance_criteria="c"),
                GoalStage(
                    index=4,
                    name="S4",
                    description="d",
                    acceptance_criteria="c",
                    parallel_group=2,
                ),
                GoalStage(
                    index=5,
                    name="S5",
                    description="d",
                    acceptance_criteria="c",
                    parallel_group=2,
                ),
            ],
        )
        groups = execution_groups(plan)
        assert len(groups) == 3  # [S1,S2], S3, [S4,S5]
        assert len(groups[0]) == 2
        assert len(groups[1]) == 1
        assert len(groups[2]) == 2

    def test_empty_plan(self):
        plan = GoalPlan(context="test", stages=[])
        assert execution_groups(plan) == []


# ── Parallel staged run tests ────────────────────────────────────────────


def _make_parallel_plan() -> GoalPlan:
    """Plan with S1 sequential, S2+S3 parallel, S4 sequential."""
    return GoalPlan(
        context="test parallel",
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


@patch("kodo.orchestrators.base.open_viewer", create=True)  # noqa: autospec
def test_parallel_stages_both_run(mock_viewer, tmp_project, mock_worktrees):
    """Both parallel stages should execute and produce stage results."""
    plan = _make_parallel_plan()
    orch = FakeOrchestrator(
        cycle_results=[
            CycleResult(summary="setup done", finished=True),
            # Parallel group: S2 and S3 each get one cycle
            CycleResult(summary="testA findings", finished=True),
            CycleResult(summary="testB findings", finished=True),
            # S4
            CycleResult(summary="fixes done", finished=True),
        ]
    )
    team = {"worker": make_agent()}

    with patch("kodo.viewer.open_viewer", create=True):  # noqa: autospec
        result = orch.run("goal", tmp_project, team, max_cycles=10, plan=plan)

    assert len(result.stage_results) == 4
    assert all(sr.finished for sr in result.stage_results)


@patch("kodo.orchestrators.base.open_viewer", create=True)  # noqa: autospec
def test_parallel_stages_summaries_feed_next(mock_viewer, tmp_project, mock_worktrees):
    """After parallel group, subsequent stage should see both summaries."""
    plan = _make_parallel_plan()
    orch = FakeOrchestrator(
        cycle_results=[
            CycleResult(summary="setup done", finished=True),
            CycleResult(summary="FINDINGS_FROM_A", finished=True),
            CycleResult(summary="FINDINGS_FROM_B", finished=True),
            CycleResult(summary="fixes done", finished=True),
        ]
    )
    team = {"worker": make_agent()}

    with patch("kodo.viewer.open_viewer", create=True):  # noqa: autospec
        orch.run("goal", tmp_project, team, max_cycles=10, plan=plan)

    # The last cycle (stage 4) should have both parallel summaries in its goal
    fix_goal = orch._cycle_calls[-1]["goal"]
    assert "FINDINGS_FROM_A" in fix_goal
    assert "FINDINGS_FROM_B" in fix_goal


@patch("kodo.orchestrators.base.open_viewer", create=True)  # noqa: autospec
def test_parallel_stages_share_snapshot(mock_viewer, tmp_project, mock_worktrees):
    """Parallel stages should see the same prior summaries, not each other's."""
    plan = _make_parallel_plan()
    orch = FakeOrchestrator(
        cycle_results=[
            CycleResult(summary="setup done", finished=True),
            CycleResult(summary="A result", finished=True),
            CycleResult(summary="B result", finished=True),
            CycleResult(summary="fix done", finished=True),
        ]
    )
    team = {"worker": make_agent()}

    with patch("kodo.viewer.open_viewer", create=True):  # noqa: autospec
        orch.run("goal", tmp_project, team, max_cycles=10, plan=plan)

    # Both parallel stages should see "setup done" but not each other
    # Calls 1 and 2 are the parallel stages (call 0 is stage 1)
    parallel_goals = [orch._cycle_calls[1]["goal"], orch._cycle_calls[2]["goal"]]
    for g in parallel_goals:
        assert "setup done" in g


@patch("kodo.orchestrators.base.open_viewer", create=True)  # noqa: autospec
def test_parallel_orchestrator_close_called(mock_viewer, tmp_project, mock_worktrees):
    """_run_in_own_loop should call close() on each parallel orchestrator copy."""
    plan = _make_parallel_plan()  # S1 seq, S2+S3 parallel, S4 seq
    close_calls: list[bool] = []

    class CloseTrackingOrchestrator(FakeOrchestrator):
        async def close(self):
            close_calls.append(True)

    orch = CloseTrackingOrchestrator(
        cycle_results=[
            CycleResult(summary="s1", finished=True),
            CycleResult(summary="s2", finished=True),
            CycleResult(summary="s3", finished=True),
            CycleResult(summary="s4", finished=True),
        ]
    )
    team = {"worker": make_agent()}

    with patch("kodo.viewer.open_viewer", create=True):  # noqa: autospec
        orch.run("goal", tmp_project, team, max_cycles=10, plan=plan)

    # Two parallel stages => two close() calls (one per for_parallel() copy)
    assert len(close_calls) == 2


@patch("kodo.orchestrators.base.open_viewer", create=True)  # noqa: autospec
def test_parallel_stages_disable_auto_commit(mock_viewer, tmp_project, mock_worktrees):
    """Parallel stages should not auto-commit (changes are discarded anyway)."""
    plan = _make_parallel_plan()

    auto_commit_per_call = []

    class TrackingOrchestrator(FakeOrchestrator):
        def cycle(
            self,
            goal,
            project_dir,
            team,
            *,
            max_exchanges=30,
            prior_summary="",
            config=None,
        ):
            auto_commit_per_call.append(config.auto_commit if config else False)
            return super().cycle(
                goal,
                project_dir,
                team,
                max_exchanges=max_exchanges,
                prior_summary=prior_summary,
                config=config,
            )

    orch = TrackingOrchestrator(
        cycle_results=[
            CycleResult(summary="s1", finished=True),
            CycleResult(summary="s2", finished=True),
            CycleResult(summary="s3", finished=True),
            CycleResult(summary="s4", finished=True),
        ]
    )
    team = {"worker": make_agent()}

    with patch("kodo.viewer.open_viewer", create=True):  # noqa: autospec
        orch.run("goal", tmp_project, team, max_cycles=10, plan=plan, auto_commit=True)

    # Stage 1: auto_commit=True, stages 2+3: False (parallel/worktree), stage 4: True
    assert auto_commit_per_call[0] is True  # stage 1
    # Parallel stages (order may vary due to threading)
    parallel_commits = sorted(auto_commit_per_call[1:3])
    assert parallel_commits == [False, False]
    assert auto_commit_per_call[3] is True  # stage 4


@patch("kodo.orchestrators.base.open_viewer", create=True)  # noqa: autospec
def test_parallel_group_complete_failure_stops_run(
    mock_viewer,
    tmp_project,
    mock_worktrees,
):
    """When every stage in a parallel group fails, subsequent stages should not run."""
    plan = _make_parallel_plan()  # S1 seq, S2+S3 parallel, S4 seq
    # Each parallel stage will consume up to remaining_cycles (9 after S1)
    # of cycle results, so provide enough "not finished" results for both
    # branches to exhaust their budgets.
    orch = FakeOrchestrator(
        cycle_results=[
            CycleResult(summary="setup done", finished=True),
        ]
        # Remaining results default to CycleResult(summary="cycle done",
        # finished=False) via FakeOrchestrator — both parallel stages
        # will exhaust their cycle budgets without finishing.
    )
    team = {"worker": make_agent()}

    with patch("kodo.viewer.open_viewer", create=True):  # noqa: autospec
        result = orch.run("goal", tmp_project, team, max_cycles=10, plan=plan)

    # S4 must not have run — only 3 stages (S1, S2, S3)
    assert len(result.stage_results) == 3
    assert result.stage_results[0].finished is True  # S1
    assert not result.finished  # overall run not finished


@patch("kodo.orchestrators.base.open_viewer", create=True)  # noqa: autospec
def test_parallel_stage_results_sorted_by_index(
    mock_viewer,
    tmp_project,
    mock_worktrees,
):
    """result.stage_results for parallel stages are sorted by stage_index."""
    plan = _make_parallel_plan()  # S1 seq, S2+S3 parallel, S4 seq
    orch = FakeOrchestrator(
        cycle_results=[
            CycleResult(summary="setup done", finished=True),
            CycleResult(summary="s2", finished=True),
            CycleResult(summary="s3", finished=True),
            CycleResult(summary="s4", finished=True),
        ]
    )
    team = {"worker": make_agent()}

    with patch("kodo.viewer.open_viewer", create=True):  # noqa: autospec
        result = orch.run("goal", tmp_project, team, max_cycles=10, plan=plan)

    indices = [sr.stage_index for sr in result.stage_results]
    assert indices == sorted(indices), f"stage_results not sorted by index: {indices}"


# ── Worktree helper tests ───────────────────────────────────────────────


@pytest.mark.slow
class TestWorktreeHelpers:
    def test_create_worktree(self, git_project):
        wt, branch = create_worktree(git_project, "test-label")
        assert wt.exists()
        assert (wt / ".git").exists()  # worktree has a .git file (not dir)
        # Branch was created with unique suffix
        import subprocess

        branches = subprocess.run(
            ["git", "branch"], cwd=git_project, capture_output=True, text=True
        ).stdout
        assert branch in branches
        assert branch.startswith("kodo-test-label-")
        # Clean up
        remove_worktree(git_project, wt, branch)

    def test_no_branch_collision(self, git_project):
        """Multiple calls with same label produce unique branches."""
        wt1, b1 = create_worktree(git_project, "same")
        wt2, b2 = create_worktree(git_project, "same")
        assert b1 != b2
        assert wt1 != wt2
        remove_worktree(git_project, wt1, b1)
        remove_worktree(git_project, wt2, b2)

    def test_worktree_is_isolated(self, git_project):
        """Files written in worktree don't appear in main repo."""
        wt, branch = create_worktree(git_project, "isolated")
        (wt / "new_file.txt").write_text("hello from worktree")
        assert not (git_project / "new_file.txt").exists()
        remove_worktree(git_project, wt, branch)

    def test_remove_worktree(self, git_project):
        wt, branch = create_worktree(git_project, "to-remove")
        assert wt.exists()
        remove_worktree(git_project, wt, branch)
        assert not wt.exists()
        # Branch should be deleted too
        import subprocess

        branches = subprocess.run(
            ["git", "branch"], cwd=git_project, capture_output=True, text=True
        ).stdout
        assert branch not in branches

    def test_remove_worktree_discards_changes(self, git_project):
        """Source modifications in worktree are lost after removal."""
        # Add a file to main repo first
        (git_project / "src.py").write_text("original")
        import subprocess

        subprocess.run(["git", "add", "."], cwd=git_project, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "add src"],
            cwd=git_project,
            capture_output=True,
            env={
                **os.environ,
                "GIT_AUTHOR_NAME": "test",
                "GIT_AUTHOR_EMAIL": "t@t",
                "GIT_COMMITTER_NAME": "test",
                "GIT_COMMITTER_EMAIL": "t@t",
            },
        )

        wt, branch = create_worktree(git_project, "modify-test")
        # Modify file in worktree
        (wt / "src.py").write_text("modified in worktree")
        # Remove worktree
        remove_worktree(git_project, wt, branch)
        # Main repo is untouched
        assert (git_project / "src.py").read_text() == "original"


# ── Parallel stages with worktree isolation tests ────────────────────────


@pytest.mark.slow
@patch("kodo.orchestrators.base.open_viewer", create=True)  # noqa: autospec
def test_parallel_stages_use_worktrees(mock_viewer, git_project, tmp_path):
    """Parallel stages should receive worktree paths, not the main project dir."""
    project = git_project
    log.init(RunDir.create(tmp_path))
    plan = _make_parallel_plan()

    project_dirs_seen: list[str] = []

    class DirTrackingOrchestrator(FakeOrchestrator):
        def cycle(
            self,
            goal,
            project_dir,
            team,
            *,
            max_exchanges=30,
            prior_summary="",
            config=None,
        ):
            project_dirs_seen.append(str(project_dir))
            return super().cycle(
                goal,
                project_dir,
                team,
                max_exchanges=max_exchanges,
                prior_summary=prior_summary,
                config=config,
            )

    orch = DirTrackingOrchestrator(
        cycle_results=[
            CycleResult(summary="s1", finished=True),
            CycleResult(summary="s2", finished=True),
            CycleResult(summary="s3", finished=True),
            CycleResult(summary="s4", finished=True),
        ]
    )
    team = {"worker": make_agent()}

    with patch("kodo.viewer.open_viewer", create=True):  # noqa: autospec
        result = orch.run("goal", project, team, max_cycles=10, plan=plan)

    assert len(result.stage_results) == 4

    # Stage 1 and 4 should use the main project dir
    assert project_dirs_seen[0] == str(project)
    assert project_dirs_seen[3] == str(project)

    # Stages 2 and 3 (parallel) should use worktree paths, not main project
    parallel_dirs = set(project_dirs_seen[1:3])
    for d in parallel_dirs:
        assert d != str(project), "Parallel stage should run in worktree"
        assert "kodo-stage-" in d

    # Worktrees should be cleaned up after parallel group finishes
    for d in parallel_dirs:
        assert not Path(d).exists(), f"Worktree {d} should be cleaned up"


@patch("kodo.orchestrators.base.open_viewer", create=True)  # noqa: autospec
def test_parallel_falls_back_without_git(mock_viewer, tmp_path):
    """If git worktree creation fails, fall back to project_dir."""
    # tmp_path is NOT a git repo — worktree creation will fail
    log.init(RunDir.create(tmp_path))
    plan = _make_parallel_plan()

    project_dirs_seen: list[str] = []

    class DirTrackingOrchestrator(FakeOrchestrator):
        def cycle(
            self,
            goal,
            project_dir,
            team,
            *,
            max_exchanges=30,
            prior_summary="",
            config=None,
        ):
            project_dirs_seen.append(str(project_dir))
            return super().cycle(
                goal,
                project_dir,
                team,
                max_exchanges=max_exchanges,
                prior_summary=prior_summary,
                config=config,
            )

    orch = DirTrackingOrchestrator(
        cycle_results=[
            CycleResult(summary="s1", finished=True),
            CycleResult(summary="s2", finished=True),
            CycleResult(summary="s3", finished=True),
            CycleResult(summary="s4", finished=True),
        ]
    )
    team = {"worker": make_agent()}

    with patch("kodo.viewer.open_viewer", create=True):  # noqa: autospec
        result = orch.run("goal", tmp_path, team, max_cycles=10, plan=plan)

    # Should still complete — all stages fall back to project_dir
    assert len(result.stage_results) == 4
    assert all(d == str(tmp_path) for d in project_dirs_seen)


@patch("kodo.orchestrators.base.open_viewer", create=True)  # noqa: autospec
def test_sequential_stage_crash_after_parallel_is_caught(mock_viewer, tmp_project):
    """If a sequential stage after a parallel group crashes, it should be
    caught and logged rather than silently aborting with 'finished: true'."""
    plan = _make_parallel_plan()  # S1 seq, S2+S3 parallel, S4 seq
    call_count = 0

    class CrashOnStage4(FakeOrchestrator):
        def cycle(self, goal, project_dir, team, **kwargs):
            nonlocal call_count
            call_count += 1
            # Crash on the 4th cycle call (stage 4)
            if call_count == 4:
                raise RuntimeError("simulated stage 4 crash")
            return super().cycle(goal, project_dir, team, **kwargs)

    orch = CrashOnStage4(
        cycle_results=[
            CycleResult(summary="s1 done", finished=True),
            CycleResult(summary="s2 findings", finished=True),
            CycleResult(summary="s3 findings", finished=True),
            # S4 will crash before consuming a result
        ]
    )
    team = {"worker": make_agent()}

    with patch("kodo.viewer.open_viewer", create=True):  # noqa: autospec
        result = orch.run("goal", tmp_project, team, max_cycles=10, plan=plan)

    # Stage 4 should appear as a failed stage result, not crash the run
    assert len(result.stage_results) == 4
    s4 = [sr for sr in result.stage_results if sr.stage_index == 4][0]
    assert not s4.finished
    assert "simulated stage 4 crash" in s4.summary
    # The run should NOT report as finished (stage 4 didn't complete)
    assert not result.finished


@patch("kodo.orchestrators.base.open_viewer", create=True)  # noqa: autospec
def test_sequential_stage_crash_before_parallel_is_caught(mock_viewer, tmp_project):
    """If a sequential stage crashes, it should be caught and stop the run."""
    plan = _make_plan(3)
    call_count = 0

    class CrashOnStage2(FakeOrchestrator):
        def cycle(self, goal, project_dir, team, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("simulated stage 2 crash")
            return super().cycle(goal, project_dir, team, **kwargs)

    orch = CrashOnStage2(
        cycle_results=[
            CycleResult(summary="s1 done", finished=True),
        ]
    )
    team = {"worker": make_agent()}

    with patch("kodo.viewer.open_viewer", create=True):  # noqa: autospec
        result = orch.run("goal", tmp_project, team, max_cycles=10, plan=plan)

    # Stage 2 crashed — should be recorded and run should stop
    assert len(result.stage_results) == 2
    s2 = result.stage_results[1]
    assert not s2.finished
    assert "simulated stage 2 crash" in s2.summary
    # Stage 3 should not have been attempted
    assert not any(sr.stage_index == 3 for sr in result.stage_results)


# ── Worktree cleanup on interrupt ────────────────────────────────────────


@pytest.mark.slow
@patch("kodo.orchestrators.base.open_viewer", create=True)  # noqa: autospec
def test_worktree_cleanup_on_interrupt_during_creation(mock_viewer, tmp_path):
    """If KeyboardInterrupt fires during worktree creation, already-created
    worktrees must still be cleaned up (no leak)."""
    # Need a real git repo so the first create_worktree succeeds
    import subprocess

    project = tmp_path / "repo"
    project.mkdir()
    subprocess.run(["git", "init"], cwd=project, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=project,
        capture_output=True,
        check=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
    )
    log.init(RunDir.create(tmp_path))

    plan = _make_parallel_plan()  # S1 seq, S2+S3 parallel, S4 seq
    orch = FakeOrchestrator(
        cycle_results=[
            CycleResult(summary="s1 done", finished=True),
        ]
    )
    team = {"worker": make_agent()}

    call_count = 0
    original_create = create_worktree

    def create_then_interrupt(proj_dir, label):
        """First call succeeds; second raises KeyboardInterrupt."""
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return original_create(proj_dir, label)
        raise KeyboardInterrupt("simulated interrupt during worktree creation")

    with (
        patch(
            "kodo.orchestrators.base.create_worktree",
            autospec=True,
            side_effect=create_then_interrupt,
        ),
        patch("kodo.orchestrators.base.remove_worktree", autospec=True) as mock_remove,
        patch("kodo.viewer.open_viewer", create=True),  # noqa: autospec
    ):
        # The KeyboardInterrupt should propagate but cleanup should happen first
        with pytest.raises(KeyboardInterrupt):
            orch.run("goal", project, team, max_cycles=10, plan=plan)

        # The first worktree was successfully created — verify it was cleaned up
        assert mock_remove.call_count == 1


# ── persist_changes helper tests ──────────────────────────────────────────


_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "t@t",
}


def _git(project, *args):
    """Run a git command in project dir."""
    import subprocess

    return subprocess.run(
        ["git", *args], cwd=project, capture_output=True, text=True, env=_GIT_ENV
    )


@pytest.mark.slow
class TestCommitWorktreeChanges:
    def test_commits_unstaged_files(self, git_project):
        wt, branch = create_worktree(git_project, "commit-test")
        (wt / "new.py").write_text("print('hello')")
        assert commit_worktree_changes(wt, "TestStage")
        # Verify commit exists on worktree branch
        log_out = _git(wt, "log", "--oneline", "-1")
        assert "parallel stage 'TestStage'" in log_out.stdout
        remove_worktree(git_project, wt, branch)

    def test_no_changes_returns_false(self, git_project):
        wt, branch = create_worktree(git_project, "noop-test")
        assert not commit_worktree_changes(wt, "TestStage")
        remove_worktree(git_project, wt, branch)


@pytest.mark.slow
class TestRemoveWorktreeKeepBranch:
    def test_dir_removed_branch_survives(self, git_project):
        import subprocess

        wt, branch = create_worktree(git_project, "keep-branch")
        assert wt.exists()
        _remove_worktree_keep_branch(git_project, wt)
        assert not wt.exists()
        # Branch should still exist
        branches = subprocess.run(
            ["git", "branch"], cwd=git_project, capture_output=True, text=True
        ).stdout
        assert branch in branches
        # Clean up branch
        subprocess.run(
            ["git", "branch", "-D", branch],
            cwd=git_project,
            capture_output=True,
        )


@pytest.fixture(scope="session")
def _conflict_repo_template(
    _git_repo_template: Path, tmp_path_factory
) -> tuple[Path, str]:
    """Pre-build a repo with a conflict branch (session-scoped, copied per test)."""
    import shutil

    tpl = tmp_path_factory.mktemp("conflict_tpl") / "repo"
    shutil.copytree(_git_repo_template, tpl)

    (tpl / "shared.py").write_text("original")
    _git(tpl, "add", "-A")
    _git(tpl, "commit", "-m", "add shared")

    wt, branch = create_worktree(tpl, "merge-conflict")
    (wt / "shared.py").write_text("worktree version")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-m", "worktree change")
    _remove_worktree_keep_branch(tpl, wt)

    (tpl / "shared.py").write_text("main version")
    _git(tpl, "add", "-A")
    _git(tpl, "commit", "-m", "main change")

    return tpl, branch


@pytest.fixture
def conflict_project(
    _conflict_repo_template: tuple[Path, str], tmp_path: Path
) -> tuple[Path, str]:
    """Per-test copy of the conflict repo template."""
    import shutil

    tpl, branch = _conflict_repo_template
    dest = tmp_path / "conflict_repo"
    shutil.copytree(tpl, dest)
    return dest, branch


@pytest.mark.slow
class TestMergeWorktreeBranch:
    def test_clean_merge(self, git_project):
        wt, branch = create_worktree(git_project, "merge-clean")
        (wt / "feature.py").write_text("# new feature")
        _git(wt, "add", "-A")
        _git(wt, "commit", "-m", "add feature")
        _remove_worktree_keep_branch(git_project, wt)

        result = merge_worktree_branch(git_project, branch, "MergeStage")
        assert result.success
        assert result.had_changes
        assert not result.conflict
        assert (git_project / "feature.py").exists()

        # Clean up branch
        _git(git_project, "branch", "-D", branch)

    def test_no_commits_ahead(self, git_project):
        wt, branch = create_worktree(git_project, "merge-noop")
        _remove_worktree_keep_branch(git_project, wt)

        result = merge_worktree_branch(git_project, branch, "NoopStage")
        assert result.success
        assert not result.had_changes
        _git(git_project, "branch", "-D", branch)

    def test_conflict_resolved_by_agent(self, conflict_project):
        """When merge conflicts occur, an agent resolves them."""
        project, branch = conflict_project

        def _fake_resolve(project_dir, branch_name, stage_name):
            """Simulate agent resolving conflicts: pick a side, add, commit."""
            (project_dir / "shared.py").write_text("resolved version")
            _git(project_dir, "add", "shared.py")
            _git(project_dir, "commit", "--no-edit")
            return True

        with patch(
            "kodo.orchestrators.base._resolve_conflicts_with_agent",
            autospec=True,
            side_effect=_fake_resolve,
        ):
            result = merge_worktree_branch(project, branch, "ConflictStage")

        # Agent resolves the conflict
        assert result.success
        assert result.had_changes

        content = (project / "shared.py").read_text()
        assert "<<<<<<<" not in content

    def test_conflict_aborts_when_agent_fails(self, conflict_project):
        """If agent can't resolve conflicts, merge aborts cleanly."""
        from unittest.mock import patch

        project, branch = conflict_project

        with patch(
            "kodo.orchestrators.base._resolve_conflicts_with_agent",
            autospec=True,
            return_value=False,
        ):
            result = merge_worktree_branch(project, branch, "ConflictStage")

        assert not result.success
        assert result.had_changes
        assert result.conflict

        status = _git(project, "status", "--porcelain")
        assert not status.stdout.strip()


# ── persist_changes integration tests ─────────────────────────────────────


def _make_persist_plan(persist_a=False, persist_b=False) -> GoalPlan:
    """Plan with S1 sequential, S2+S3 parallel (configurable persist), S4 sequential."""
    return GoalPlan(
        context="test persist",
        stages=[
            GoalStage(
                index=1, name="Setup", description="d1", acceptance_criteria="c1"
            ),
            GoalStage(
                index=2,
                name="WorkA",
                description="d2",
                acceptance_criteria="c2",
                parallel_group=1,
                persist_changes=persist_a,
            ),
            GoalStage(
                index=3,
                name="WorkB",
                description="d3",
                acceptance_criteria="c3",
                parallel_group=1,
                persist_changes=persist_b,
            ),
            GoalStage(
                index=4, name="Final", description="d4", acceptance_criteria="c4"
            ),
        ],
    )


@pytest.mark.slow
@patch("kodo.orchestrators.base.open_viewer", create=True)  # noqa: autospec
def test_persist_changes_merges_to_main(mock_viewer, git_project, tmp_path):
    """Parallel stage with persist_changes=True should merge files back."""
    project = git_project
    log.init(RunDir.create(tmp_path))

    plan = _make_persist_plan(persist_a=True, persist_b=False)

    class FileWritingOrchestrator(FakeOrchestrator):
        def cycle(self, goal, project_dir, team, **kwargs):
            # Write a file only in worktree-based (parallel) stages
            if project_dir != project:
                # Use "stage-2" vs "stage-3" from worktree path to distinguish
                if "stage-2" in str(project_dir):
                    (project_dir / "from_a.py").write_text("# from A")
                else:
                    (project_dir / "from_b.py").write_text("# from B")
            return CycleResult(summary="done", finished=True)

    orch = FileWritingOrchestrator()
    team = {"worker": make_agent()}

    with patch("kodo.viewer.open_viewer", create=True):  # noqa: autospec
        orch.run("goal", project, team, max_cycles=10, plan=plan, auto_commit=True)

    # WorkA (stage 2) had persist_changes=True — its file should be on main
    assert (project / "from_a.py").exists(), "Persist stage A file should be merged"
    # WorkB (stage 3) had persist_changes=False — its file should NOT be on main
    assert not (project / "from_b.py").exists(), (
        "Non-persist stage B file should be discarded"
    )


@pytest.mark.slow
@patch("kodo.orchestrators.base.open_viewer", create=True)  # noqa: autospec
def test_persist_changes_false_discards(mock_viewer, git_project, tmp_path):
    """Default persist_changes=False should discard worktree changes (regression)."""
    project = git_project
    log.init(RunDir.create(tmp_path))

    plan = _make_persist_plan(persist_a=False, persist_b=False)

    class FileWritingOrchestrator(FakeOrchestrator):
        def cycle(self, goal, project_dir, team, **kwargs):
            # Write file only in worktree (parallel stage), not main dir
            if project_dir != project:
                (project_dir / "should_vanish.py").write_text("# gone")
            return CycleResult(summary="done", finished=True)

    orch = FileWritingOrchestrator()
    team = {"worker": make_agent()}

    with patch("kodo.viewer.open_viewer", create=True):  # noqa: autospec
        orch.run("goal", project, team, max_cycles=10, plan=plan)

    assert not (project / "should_vanish.py").exists()


@patch("kodo.orchestrators.base.open_viewer", create=True)  # noqa: autospec
def test_persist_changes_enables_auto_commit(mock_viewer, tmp_project):
    """persist_changes=True should pass auto_commit=True to parallel cycle."""
    plan = _make_persist_plan(persist_a=True, persist_b=False)

    auto_commit_per_call = []

    class TrackingOrchestrator(FakeOrchestrator):
        def cycle(
            self,
            goal,
            project_dir,
            team,
            *,
            max_exchanges=30,
            prior_summary="",
            config=None,
        ):
            auto_commit_per_call.append(config.auto_commit if config else False)
            return super().cycle(
                goal,
                project_dir,
                team,
                max_exchanges=max_exchanges,
                prior_summary=prior_summary,
                config=config,
            )

    orch = TrackingOrchestrator(
        cycle_results=[
            CycleResult(summary="s1", finished=True),
            CycleResult(summary="s2", finished=True),
            CycleResult(summary="s3", finished=True),
            CycleResult(summary="s4", finished=True),
        ]
    )
    team = {"worker": make_agent()}

    with patch("kodo.viewer.open_viewer", create=True):  # noqa: autospec
        orch.run("goal", tmp_project, team, max_cycles=10, plan=plan, auto_commit=True)

    # Stage 1: True, WorkA (persist): True, WorkB (no persist): False, Stage 4: True
    assert auto_commit_per_call[0] is True  # stage 1
    parallel_commits = auto_commit_per_call[1:3]
    assert True in parallel_commits  # WorkA (persist_changes=True)
    assert False in parallel_commits  # WorkB (persist_changes=False)
    assert auto_commit_per_call[3] is True  # stage 4


@patch("kodo.orchestrators.base.open_viewer", create=True)  # noqa: autospec
def test_done_mode_propagates_to_stage_config(mock_viewer, tmp_project):
    """done_mode from the run-level CycleConfig is passed through _run_one_stage."""
    plan = _make_plan(2)

    done_modes_seen: list[str] = []

    class TrackingOrchestrator(FakeOrchestrator):
        def cycle(
            self,
            goal,
            project_dir,
            team,
            *,
            max_exchanges=30,
            prior_summary="",
            config=None,
        ):
            done_modes_seen.append(config.done_mode if config else "unknown")
            return super().cycle(
                goal,
                project_dir,
                team,
                max_exchanges=max_exchanges,
                prior_summary=prior_summary,
                config=config,
            )

    orch = TrackingOrchestrator(
        cycle_results=[
            CycleResult(summary="s1 done", finished=True),
            CycleResult(summary="s2 done", finished=True),
        ]
    )
    team = {"worker": make_agent()}

    with patch("kodo.viewer.open_viewer", create=True):  # noqa: autospec
        orch.run(
            "goal",
            tmp_project,
            team,
            max_cycles=10,
            plan=plan,
            config=CycleConfig(done_mode="legacy"),
        )

    # Both stages should receive the "legacy" done_mode
    assert done_modes_seen == ["legacy", "legacy"]


@patch("kodo.orchestrators.base.open_viewer", create=True)  # noqa: autospec
def test_done_mode_default_is_new(mock_viewer, tmp_project):
    """Without explicit done_mode, stages should receive the default 'new' mode."""
    plan = _make_plan(1)

    done_modes_seen: list[str] = []

    class TrackingOrchestrator(FakeOrchestrator):
        def cycle(
            self,
            goal,
            project_dir,
            team,
            *,
            max_exchanges=30,
            prior_summary="",
            config=None,
        ):
            done_modes_seen.append(config.done_mode if config else "unknown")
            return super().cycle(
                goal,
                project_dir,
                team,
                max_exchanges=max_exchanges,
                prior_summary=prior_summary,
                config=config,
            )

    orch = TrackingOrchestrator(
        cycle_results=[CycleResult(summary="done", finished=True)]
    )
    team = {"worker": make_agent()}

    with patch("kodo.viewer.open_viewer", create=True):  # noqa: autospec
        orch.run("goal", tmp_project, team, max_cycles=5, plan=plan)

    assert done_modes_seen == ["new"]
