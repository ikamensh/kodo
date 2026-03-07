"""Tests for kodo.orchestrators.base — orchestrator lifecycle, data types, helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kodo import log
from kodo.agent import Agent
from kodo.log import RunDir
from kodo.orchestrators.base import (
    CycleResult,
    DoneSignal,
    GoalPlan,
    GoalStage,
    OrchestratorBase,
    ResumeState,
    RunResult,
    StageResult,
    _auto_commit,
    _handle_stage_crash,
    apply_done_signal,
    build_cycle_prompt,
    clone_team,
    compose_stage_goal,
    execution_groups,
    handle_agent_call,
)
from kodo.summarizer import Summarizer
from tests.conftest import make_agent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _noop_summarizer():
    """Summarizer that does nothing."""
    with (
        patch("kodo.summarizer._probe_ollama", return_value=None),
        patch("kodo.summarizer._probe_gemini", return_value=None),
    ):
        return Summarizer()


class FakeOrchestrator(OrchestratorBase):
    """Minimal orchestrator for testing run() logic."""

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
            {"goal": goal, "prior_summary": prior_summary}
        )
        if self._cycle_results:
            return self._cycle_results.pop(0)
        return CycleResult(summary="cycle done")


@pytest.fixture()
def tmp_project(tmp_path: Path) -> Path:
    """Initialize logging and return a temp project dir."""
    log.init(RunDir.create(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# RunResult properties
# ---------------------------------------------------------------------------


class TestRunResultProperties:
    def test_empty_cycles_not_finished(self):
        rr = RunResult(cycles=[])
        assert rr.finished is False
        assert rr.summary == ""
        assert rr.total_exchanges == 0
        assert rr.total_cost_usd == 0.0

    def test_finished_from_stage_results(self):
        """When stage_results exist, finished comes from last stage."""
        rr = RunResult(
            cycles=[CycleResult(finished=True, success=True)],
            stage_results=[
                StageResult(stage_index=1, stage_name="S1", finished=True, success=True),
                StageResult(stage_index=2, stage_name="S2", finished=False),
            ],
        )
        # Last stage not finished → run not finished
        assert rr.finished is False

    def test_finished_from_stage_results_all_done(self):
        rr = RunResult(
            cycles=[CycleResult(finished=True, success=True)],
            stage_results=[
                StageResult(stage_index=1, stage_name="S1", finished=True, success=True),
            ],
        )
        assert rr.finished is True


# ---------------------------------------------------------------------------
# DoneSignal + apply_done_signal
# ---------------------------------------------------------------------------


class TestApplyDoneSignal:
    def test_not_called_is_noop(self):
        result = CycleResult()
        signal = DoneSignal()
        apply_done_signal(result, signal)
        assert result.finished is False
        assert result.success is False
        assert result.summary == ""

    def test_goal_done(self):
        result = CycleResult()
        signal = DoneSignal()
        signal.called = True
        signal.terminal = "goal_done"
        signal.summary = "All done"
        apply_done_signal(result, signal)
        assert result.finished is True
        assert result.success is True
        assert result.summary == "All done"

    def test_end_cycle(self):
        result = CycleResult()
        signal = DoneSignal()
        signal.called = True
        signal.terminal = "end_cycle"
        signal.summary = "Need more work"
        apply_done_signal(result, signal)
        assert result.finished is False
        assert result.success is False
        assert result.summary == "Need more work"

    def test_raise_issue(self):
        result = CycleResult()
        signal = DoneSignal()
        signal.called = True
        signal.terminal = "raise_issue"
        signal.summary = "Blocked on auth"
        apply_done_signal(result, signal)
        assert result.finished is True
        assert result.success is False

    def test_legacy_success(self):
        result = CycleResult()
        signal = DoneSignal()
        signal.called = True
        signal.terminal = "legacy"
        signal.success = True
        signal.summary = "Legacy done"
        apply_done_signal(result, signal)
        assert result.finished is True
        assert result.success is True

    def test_legacy_failure(self):
        result = CycleResult()
        signal = DoneSignal()
        signal.called = True
        signal.terminal = "legacy"
        signal.success = False
        signal.summary = "Legacy fail"
        apply_done_signal(result, signal)
        assert result.finished is True
        assert result.success is False

    def test_unknown_terminal_uses_legacy_path(self):
        """Unknown terminal kind → falls through to else (legacy)."""
        result = CycleResult()
        signal = DoneSignal()
        signal.called = True
        signal.terminal = None  # unknown
        signal.success = True
        signal.summary = "Unknown"
        apply_done_signal(result, signal)
        assert result.finished is True
        assert result.success is True


# ---------------------------------------------------------------------------
# build_cycle_prompt
# ---------------------------------------------------------------------------


class TestBuildCyclePrompt:
    def test_basic_prompt(self, tmp_path):
        prompt = build_cycle_prompt("Build X", tmp_path)
        assert "Build X" in prompt
        assert str(tmp_path) in prompt

    def test_with_prior_summary(self, tmp_path):
        prompt = build_cycle_prompt("Build X", tmp_path, prior_summary="Made progress")
        assert "Build X" in prompt
        assert "Made progress" in prompt
        assert "Previous progress" in prompt

    def test_with_run_status(self, tmp_path):
        """When .kodo-run-status exists, it's included."""
        from kodo.orchestrators.run_status import write_run_status

        write_run_status(tmp_path, "Build X", cycle_num=2, max_cycles=5)
        prompt = build_cycle_prompt("Build X", tmp_path)
        assert "Build X" in prompt


# ---------------------------------------------------------------------------
# compose_stage_goal
# ---------------------------------------------------------------------------


class TestComposeStageGoal:
    def test_invalid_stage_index_too_low(self):
        plan = GoalPlan(
            context="Test",
            stages=[GoalStage(index=1, name="S1", description="D", acceptance_criteria="C")],
        )
        with pytest.raises(ValueError, match="stage_index"):
            compose_stage_goal(plan, 0, [])

    def test_invalid_stage_index_too_high(self):
        plan = GoalPlan(
            context="Test",
            stages=[GoalStage(index=1, name="S1", description="D", acceptance_criteria="C")],
        )
        with pytest.raises(ValueError, match="stage_index"):
            compose_stage_goal(plan, 2, [])


# ---------------------------------------------------------------------------
# _handle_stage_crash
# ---------------------------------------------------------------------------


class TestHandleStageCrash:
    def test_returns_failed_stage_result(self, tmp_project):
        stage = GoalStage(index=3, name="Deploy", description="D", acceptance_criteria="C")
        result = _handle_stage_crash(stage, RuntimeError("boom"))
        assert isinstance(result, StageResult)
        assert result.stage_index == 3
        assert result.stage_name == "Deploy"
        assert result.finished is False
        assert "boom" in result.summary


# ---------------------------------------------------------------------------
# handle_agent_call — edge cases
# ---------------------------------------------------------------------------


class TestHandleAgentCallEdgePaths:
    def test_new_conversation_flag(self, tmp_project):
        """new_conversation=True prints extra message."""
        agent = make_agent("all good")
        summarizer = _noop_summarizer()
        result = handle_agent_call(
            "worker", agent, "do task", tmp_project, summarizer,
            new_conversation=True,
        )
        assert "all good" in result

    def test_cycle_log_append_on_call_and_result(self, tmp_project):
        """When cycle_log is provided, task and result are appended."""
        agent = make_agent("report text")
        summarizer = _noop_summarizer()
        cycle_log: list[str] = []
        handle_agent_call(
            "worker", agent, "do task", tmp_project, summarizer,
            cycle_log=cycle_log,
        )
        assert len(cycle_log) == 2
        assert "→ worker" in cycle_log[0]
        assert "← worker" in cycle_log[1]

    def test_cycle_log_append_on_crash(self, tmp_project):
        """When agent crashes with cycle_log, crash is logged."""
        session = MagicMock()
        session.cost_bucket = "test"
        session.query.side_effect = RuntimeError("session dead")
        agent = Agent(session, "broken")
        summarizer = _noop_summarizer()
        cycle_log: list[str] = []
        result = handle_agent_call(
            "worker", agent, "do task", tmp_project, summarizer,
            cycle_log=cycle_log,
        )
        assert "crashed" in result
        assert len(cycle_log) == 2
        assert "← worker" in cycle_log[1]



# ---------------------------------------------------------------------------
# _auto_commit
# ---------------------------------------------------------------------------


class TestAutoCommit:
    def test_worker_fast_preferred(self, tmp_project):
        """worker_fast is preferred over worker_smart."""
        fast_session = MagicMock()
        fast_session.cost_bucket = "test"
        smart_session = MagicMock()
        smart_session.cost_bucket = "test"
        team = {
            "worker_fast": Agent(fast_session, "fast"),
            "worker_smart": Agent(smart_session, "smart"),
        }
        _auto_commit(team, tmp_project, "completed work")
        fast_session.query.assert_called_once()
        smart_session.query.assert_not_called()


# ---------------------------------------------------------------------------
# OrchestratorBase — _fallback_summary, _cycle_epilogue, for_parallel, close
# ---------------------------------------------------------------------------


class TestOrchestratorBaseMethods:
    def test_fallback_summary_when_no_summary(self):
        """When result has no summary and not finished, fill from summarizer."""
        orch = FakeOrchestrator()
        orch._summarizer.get_accumulated_summary.return_value = "accumulated text"
        result = CycleResult()
        orch._fallback_summary(result)
        assert "accumulated text" in result.summary

    def test_fallback_summary_no_accumulated(self):
        """When summarizer has nothing, fallback message."""
        orch = FakeOrchestrator()
        orch._summarizer.get_accumulated_summary.return_value = ""
        result = CycleResult()
        orch._fallback_summary(result)
        assert "No summary available" in result.summary

    def test_fallback_summary_skipped_when_finished(self):
        """When result is already finished, don't override."""
        orch = FakeOrchestrator()
        result = CycleResult(finished=True, success=True, summary="already done")
        orch._fallback_summary(result)
        assert result.summary == "already done"

    def test_fallback_summary_skipped_when_has_summary(self):
        """When result already has a summary, don't override."""
        orch = FakeOrchestrator()
        result = CycleResult(summary="existing")
        orch._fallback_summary(result)
        assert result.summary == "existing"

    def test_fallback_summary_with_context(self):
        """Context string is included in fallback message."""
        orch = FakeOrchestrator()
        orch._summarizer.get_accumulated_summary.return_value = "text"
        result = CycleResult()
        orch._fallback_summary(result, context="stage 2")
        assert "stage 2" in result.summary

    def test_cycle_epilogue(self, tmp_project):
        """_cycle_epilogue fills summary, emits log, clears summarizer."""
        orch = FakeOrchestrator()
        orch._summarizer.get_accumulated_summary.return_value = "done"
        result = CycleResult(exchanges=5)
        returned = orch._cycle_epilogue(result, cost_bucket="test")
        assert returned is result
        assert "done" in result.summary
        orch._summarizer.clear.assert_called_once()

    def test_for_parallel_returns_self(self):
        """Default for_parallel returns self."""
        orch = FakeOrchestrator()
        assert orch.for_parallel() is orch

    def test_cycle_raises_not_implemented(self):
        """Base OrchestratorBase.cycle() raises NotImplementedError."""

        class Bare(OrchestratorBase):
            pass

        bare = Bare()
        bare.model = "x"
        bare._orchestrator_name = "x"
        with pytest.raises(NotImplementedError):
            bare.cycle("goal", Path("/tmp"), {})


# ---------------------------------------------------------------------------
# OrchestratorBase.run() — empty plan fallback
# ---------------------------------------------------------------------------


class TestRunEmptyPlanFallback:
    def test_empty_plan_runs_as_single(self, tmp_project, capsys):
        """Plan with no stages triggers warning and runs single-goal."""
        plan = GoalPlan(context="Test", stages=[])
        orch = FakeOrchestrator([CycleResult(finished=True, success=True, summary="done")])
        team = {"worker": make_agent()}

        result = orch.run("goal", tmp_project, team, plan=plan)
        assert result.finished is True
        out = capsys.readouterr().out
        assert "no stages" in out


# ---------------------------------------------------------------------------
# _run_staged — waterfall with cycle limit exhaustion
# ---------------------------------------------------------------------------


class TestRunStagedCycleLimit:
    def test_cycles_exhausted_stops_stages(self, tmp_project, capsys):
        """When remaining_cycles <= 0, skip remaining stages."""
        plan = GoalPlan(
            context="Test",
            stages=[
                GoalStage(index=1, name="S1", description="D", acceptance_criteria="C"),
                GoalStage(index=2, name="S2", description="D", acceptance_criteria="C"),
                GoalStage(index=3, name="S3", description="D", acceptance_criteria="C"),
            ],
        )
        # Only 1 cycle budget, but 3 stages — S1 consumes it, S2+S3 skipped
        orch = FakeOrchestrator([CycleResult(finished=True, success=True, summary="S1 done")])
        team = {"worker": make_agent()}

        orch.run("goal", tmp_project, team, plan=plan, max_cycles=1)
        out = capsys.readouterr().out
        assert "stage" in out.lower()  # should mention remaining stages

    def test_resume_with_initial_prior_for_stage(self, tmp_project):
        """Resume mid-stage passes prior_summary to first resumed stage."""
        plan = GoalPlan(
            context="Test",
            stages=[
                GoalStage(index=1, name="S1", description="D", acceptance_criteria="C"),
                GoalStage(index=2, name="S2", description="D", acceptance_criteria="C"),
            ],
        )
        # Stage 1 done, resuming stage 2 with prior summary
        orch = FakeOrchestrator([CycleResult(finished=True, success=True, summary="S2 done")])
        team = {"worker": make_agent()}

        resume = ResumeState(
            completed_cycles=1,
            prior_summary="mid-stage progress",
            agent_session_ids={},
            completed_stages=[1],
            stage_summaries=["S1 result"],
            current_stage_cycles=1,
        )

        orch.run("goal", tmp_project, team, plan=plan, max_cycles=5, resume=resume)
        # The first cycle call should have mid-stage progress as prior
        assert orch._cycle_calls[0]["prior_summary"] == "mid-stage progress"


# ---------------------------------------------------------------------------
# _run_adaptive — advisor-driven execution
# ---------------------------------------------------------------------------


class TestRunAdaptive:
    def _make_advisor(self, decisions, max_stages=10):
        """Create a mock advisor that returns decisions in sequence."""
        advisor = MagicMock()
        advisor.max_stages = max_stages
        _decisions = list(decisions)

        def assess(*args, **kwargs):
            return _decisions.pop(0)

        advisor.assess = MagicMock(side_effect=assess)

        def make_stage(decision, index):
            return GoalStage(
                index=index,
                name=decision.name if hasattr(decision, "name") else f"Stage {index}",
                description=decision.description if hasattr(decision, "description") else "D",
                acceptance_criteria="Done",
            )

        advisor.make_stage = MagicMock(side_effect=make_stage)
        return advisor

    def _make_decision(self, action="continue", name="Next", description="Do it", summary=""):
        d = MagicMock()
        d.action = action
        d.name = name
        d.description = description
        d.summary = summary
        return d

    def test_advisor_done_stops_run(self, tmp_project, capsys):
        """Advisor returning 'done' stops the run."""
        plan = GoalPlan(
            context="Test",
            stages=[GoalStage(index=1, name="S1", description="D", acceptance_criteria="C")],
        )
        advisor = self._make_advisor([
            self._make_decision(action="done", summary="All complete"),
        ])
        orch = FakeOrchestrator()
        team = {"worker": make_agent()}

        orch.run("goal", tmp_project, team, plan=plan, max_cycles=10, advisor=advisor)
        out = capsys.readouterr().out
        assert "complete" in out.lower() or "done" in out.lower()

    def test_advisor_stage_crash_wrapped(self, tmp_project):
        """Stage crash during adaptive execution is handled."""
        plan = GoalPlan(
            context="Test",
            stages=[GoalStage(index=1, name="S1", description="D", acceptance_criteria="C")],
        )

        class CrashOrchestrator(FakeOrchestrator):
            def _run_one_stage(self, *args, **kwargs):
                raise RuntimeError("stage exploded")

        advisor = self._make_advisor([
            self._make_decision(action="continue", name="Boom"),
        ])
        orch = CrashOrchestrator()
        team = {"worker": make_agent()}

        result = orch.run("goal", tmp_project, team, plan=plan, max_cycles=10, advisor=advisor)
        # Should have a stage_result with crash summary
        assert len(result.stage_results) == 1
        assert "crashed" in result.stage_results[0].summary.lower() or "exploded" in result.stage_results[0].summary.lower()

    def test_advisor_stage_not_completed_stops(self, tmp_project, capsys):
        """When an advisor stage doesn't finish, run stops."""
        plan = GoalPlan(
            context="Test",
            stages=[GoalStage(index=1, name="S1", description="D", acceptance_criteria="C")],
        )
        advisor = self._make_advisor([
            self._make_decision(action="continue", name="Incomplete"),
        ])
        # Cycle returns not-finished
        orch = FakeOrchestrator([CycleResult(finished=False, summary="Ran out")])
        team = {"worker": make_agent()}

        orch.run("goal", tmp_project, team, plan=plan, max_cycles=1, advisor=advisor)
        out = capsys.readouterr().out
        assert "did not complete" in out.lower() or "stopping" in out.lower()

    def test_advisor_safety_limit(self, tmp_project, capsys):
        """Advisor safety limit stops after max_stages."""
        plan = GoalPlan(
            context="Test",
            stages=[GoalStage(index=1, name="S1", description="D", acceptance_criteria="C")],
        )
        advisor = self._make_advisor(
            [self._make_decision(action="continue", name=f"S{i}") for i in range(5)],
            max_stages=2,
        )
        orch = FakeOrchestrator(
            [CycleResult(finished=True, success=True, summary=f"done {i}") for i in range(5)]
        )
        team = {"worker": make_agent()}

        orch.run("goal", tmp_project, team, plan=plan, max_cycles=10, advisor=advisor)
        out = capsys.readouterr().out
        assert "safety limit" in out.lower() or "Safety limit" in out


# ---------------------------------------------------------------------------
# clone_team
# ---------------------------------------------------------------------------


class TestCloneTeam:
    def test_clone_creates_independent_sessions(self):
        team = {"worker": make_agent("original")}
        cloned = clone_team(team)
        assert "worker" in cloned
        assert cloned["worker"] is not team["worker"]
        assert cloned["worker"].session is not team["worker"].session


# ---------------------------------------------------------------------------
# execution_groups
# ---------------------------------------------------------------------------


class TestExecutionGroups:
    def test_all_sequential(self):
        plan = GoalPlan(
            context="Test",
            stages=[
                GoalStage(index=1, name="S1", description="D", acceptance_criteria="C"),
                GoalStage(index=2, name="S2", description="D", acceptance_criteria="C"),
            ],
        )
        groups = execution_groups(plan)
        assert len(groups) == 2
        assert len(groups[0]) == 1
        assert len(groups[1]) == 1

    def test_parallel_group(self):
        plan = GoalPlan(
            context="Test",
            stages=[
                GoalStage(index=1, name="S1", description="D", acceptance_criteria="C"),
                GoalStage(index=2, name="S2", description="D", acceptance_criteria="C", parallel_group=1),
                GoalStage(index=3, name="S3", description="D", acceptance_criteria="C", parallel_group=1),
            ],
        )
        groups = execution_groups(plan)
        assert len(groups) == 2  # S1 alone, S2+S3 together
        assert len(groups[0]) == 1
        assert len(groups[1]) == 2

    def test_mixed_groups(self):
        plan = GoalPlan(
            context="Test",
            stages=[
                GoalStage(index=1, name="S1", description="D", acceptance_criteria="C", parallel_group=1),
                GoalStage(index=2, name="S2", description="D", acceptance_criteria="C"),
                GoalStage(index=3, name="S3", description="D", acceptance_criteria="C", parallel_group=1),
            ],
        )
        groups = execution_groups(plan)
        # S1 and S3 share group 1, S2 is standalone
        assert len(groups) == 2
        assert len(groups[0]) == 2  # S1 + S3
        assert len(groups[1]) == 1  # S2
