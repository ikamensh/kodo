"""Tests for the adaptive planning advisor."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch


from kodo.orchestrators.advisor import (
    Advisor,
    AdvisorDecision,
    SessionAdvisor,
    _build_assess_prompt,
    _build_session_assess_prompt,
    _parse_advisor_json,
)
from kodo.orchestrators.base import (
    CycleConfig,
    CycleResult,
    GoalPlan,
    GoalStage,
    OrchestratorBase,
    RunResult,
)
from kodo.summarizer import Summarizer
from tests.conftest import make_agent


# ---------------------------------------------------------------------------
# Unit tests for AdvisorDecision and make_stage
# ---------------------------------------------------------------------------


class TestMakeStage:
    def test_creates_valid_goalstage(self):
        decision = AdvisorDecision(
            action="next_stage",
            stage_name="Implement auth",
            stage_description="Add JWT authentication",
            acceptance_criteria="Tests pass for login/logout",
        )
        stage = Advisor.make_stage(decision, 3)

        assert stage.index == 3
        assert stage.name == "Implement auth"
        assert stage.description == "Add JWT authentication"
        assert stage.acceptance_criteria == "Tests pass for login/logout"
        assert stage.browser_testing is False

    def test_defaults_when_fields_missing(self):
        decision = AdvisorDecision(action="next_stage")
        stage = Advisor.make_stage(decision, 1)

        assert stage.index == 1
        assert stage.name == "Stage 1"
        assert stage.description == ""
        assert stage.acceptance_criteria == ""

    def test_browser_testing_passed_through(self):
        decision = AdvisorDecision(
            action="next_stage",
            stage_name="UI test",
            stage_description="Test the UI",
            acceptance_criteria="Page renders",
            browser_testing=True,
        )
        stage = Advisor.make_stage(decision, 1)
        assert stage.browser_testing is True


# ---------------------------------------------------------------------------
# Unit tests for _build_assess_prompt
# ---------------------------------------------------------------------------


class TestBuildAssessPrompt:
    def test_includes_goal_and_context(self):
        plan = GoalPlan(
            context="Python 3.13, FastAPI",
            stages=[
                GoalStage(1, "Setup", "Set up project", "Tests pass"),
            ],
        )
        prompt = _build_assess_prompt("Build API", plan, [], 0, 20)

        assert "Build API" in prompt
        assert "Python 3.13, FastAPI" in prompt

    def test_includes_completed_summaries(self):
        plan = GoalPlan(context="ctx", stages=[])
        summaries = ["Stage 1 done: auth implemented", "Stage 2 done: tests added"]
        prompt = _build_assess_prompt("Goal", plan, summaries, 2, 20)

        assert "Stage 1" in prompt
        assert "auth implemented" in prompt
        assert "Stage 2" in prompt
        assert "tests added" in prompt
        assert "2 stage(s) completed" in prompt

    def test_includes_remaining_original_stages(self):
        plan = GoalPlan(
            context="ctx",
            stages=[
                GoalStage(1, "Done", "Already done", "ok"),
                GoalStage(2, "Next", "Do this next", "criteria"),
                GoalStage(3, "Later", "Do this later", "criteria"),
            ],
        )
        prompt = _build_assess_prompt("Goal", plan, ["summary1"], 1, 20)

        assert "Next: Do this next" in prompt
        assert "Later: Do this later" in prompt
        assert "for reference" in prompt.lower()

    def test_no_remaining_when_all_completed(self):
        plan = GoalPlan(
            context="ctx",
            stages=[GoalStage(1, "Only", "The only stage", "ok")],
        )
        prompt = _build_assess_prompt("Goal", plan, ["done"], 1, 20)

        assert "for reference" not in prompt.lower()


# ---------------------------------------------------------------------------
# Unit tests for Advisor.assess with mocked pydantic-ai
# ---------------------------------------------------------------------------


class TestAdvisorAssess:
    def _mock_run_sync(self, decision: AdvisorDecision):
        """Create a mock pydantic-ai RunResult."""
        mock_result = MagicMock()
        mock_result.output = decision
        return mock_result

    def test_returns_next_stage_decision(self):
        decision = AdvisorDecision(
            action="next_stage",
            stage_name="Build API",
            stage_description="Create REST endpoints",
            acceptance_criteria="All endpoints return 200",
            reasoning="The project needs an API layer first",
        )
        plan = GoalPlan(context="ctx", stages=[])

        with patch("kodo.orchestrators.advisor.PydanticAgent", autospec=True) as MockAgent:
            instance = MockAgent.return_value
            instance.run_sync.return_value = self._mock_run_sync(decision)

            advisor = Advisor(model="test-model")
            result = advisor.assess("Build app", plan, [], 0)

            assert result.action == "next_stage"
            assert result.stage_name == "Build API"
            assert result.reasoning == "The project needs an API layer first"

    def test_returns_done_decision(self):
        decision = AdvisorDecision(
            action="done",
            summary="All features implemented and tested",
        )
        plan = GoalPlan(context="ctx", stages=[])

        with patch("kodo.orchestrators.advisor.PydanticAgent", autospec=True) as MockAgent:
            instance = MockAgent.return_value
            instance.run_sync.return_value = self._mock_run_sync(decision)

            advisor = Advisor(model="test-model")
            result = advisor.assess("Build app", plan, ["stage1 done"], 1)

            assert result.action == "done"
            assert result.summary == "All features implemented and tested"

    def test_agent_cached_across_calls(self):
        """PydanticAgent is created once in __init__, reused across assess() calls."""
        with patch("kodo.orchestrators.advisor.PydanticAgent", autospec=True) as MockAgent:
            instance = MockAgent.return_value
            result_mock = MagicMock()
            result_mock.output = AdvisorDecision(action="next_stage", stage_name="S1")
            instance.run_sync.return_value = result_mock

            advisor = Advisor(model="test-model")
            plan = GoalPlan(context="ctx", stages=[])

            # PydanticAgent constructed once (in __init__)
            assert MockAgent.call_count == 1

            advisor.assess("Goal", plan, [], 0)
            advisor.assess("Goal", plan, ["s1"], 1)

            # Still only 1 construction, but 2 run_sync calls
            assert MockAgent.call_count == 1
            assert instance.run_sync.call_count == 2


# ---------------------------------------------------------------------------
# Integration: _run_adaptive in OrchestratorBase
# ---------------------------------------------------------------------------


class _FakeOrchestrator(OrchestratorBase):
    """Minimal orchestrator for testing the run loop."""

    def __init__(self):
        self.model = "fake"
        self._orchestrator_name = "fake"
        self._summarizer = Summarizer()
        self._cycle_count = 0

    def cycle(self, goal, project_dir, team, *, max_exchanges=30,
              prior_summary="", config=None):
        self._cycle_count += 1
        return CycleResult(
            exchanges=1,
            finished=True,
            success=True,
            summary=f"Cycle {self._cycle_count} done: {goal[:50]}",
        )


class TestRunAdaptive:
    def _make_advisor(self, decisions: list[AdvisorDecision]) -> Advisor:
        """Create an advisor that returns canned decisions in sequence."""
        call_idx = 0

        with patch("kodo.orchestrators.advisor.PydanticAgent", autospec=True):
            advisor = Advisor(model="test-model", max_stages=20)

        def fake_assess(goal, plan, summaries, count):
            nonlocal call_idx
            decision = decisions[min(call_idx, len(decisions) - 1)]
            call_idx += 1
            return decision

        advisor.assess = fake_assess
        return advisor

    def test_two_stages_then_done(self, tmp_path: Path):
        """Advisor generates 2 stages, then says done."""
        advisor = self._make_advisor([
            AdvisorDecision(
                action="next_stage",
                stage_name="Setup",
                stage_description="Initialize project",
                acceptance_criteria="Project builds",
            ),
            AdvisorDecision(
                action="next_stage",
                stage_name="Build",
                stage_description="Implement features",
                acceptance_criteria="Tests pass",
            ),
            AdvisorDecision(
                action="done",
                summary="All done",
            ),
        ])

        orch = _FakeOrchestrator()
        plan = GoalPlan(context="Test project", stages=[
            GoalStage(1, "Original", "Original stage", "criteria"),
        ])
        team = {"worker": make_agent("ok")}
        result = RunResult()

        orch._run_adaptive(
            "Build app",
            tmp_path,
            team,
            plan,
            result,
            stage_summaries=[],
            max_exchanges=30,
            remaining_cycles=50,
            start_stage_idx=0,
            config=CycleConfig(),
            advisor=advisor,
        )

        assert len(result.stage_results) == 2
        assert result.stage_results[0].stage_name == "Setup"
        assert result.stage_results[1].stage_name == "Build"
        assert all(sr.finished for sr in result.stage_results)

    def test_advisor_stops_immediately(self, tmp_path: Path):
        """Advisor says done on first call — no stages run, synthetic result added."""
        advisor = self._make_advisor([
            AdvisorDecision(action="done", summary="Goal already met"),
        ])

        orch = _FakeOrchestrator()
        plan = GoalPlan(context="ctx", stages=[])
        team = {"worker": make_agent("ok")}
        result = RunResult()

        orch._run_adaptive(
            "Simple goal",
            tmp_path,
            team,
            plan,
            result,
            stage_summaries=[],
            max_exchanges=30,
            remaining_cycles=50,
            start_stage_idx=0,
            config=CycleConfig(),
            advisor=advisor,
        )

        # Verify synthetic stage result was added so RunResult.finished returns True
        assert len(result.stage_results) == 1
        assert result.stage_results[0].finished is True
        assert result.stage_results[0].stage_name == "(advisor confirmed done)"
        assert result.stage_results[0].summary == "Goal already met"
        assert result.finished is True

    def test_safety_limit_stops_run(self, tmp_path: Path):
        """Advisor always says next_stage — safety limit kicks in."""
        always_next = AdvisorDecision(
            action="next_stage",
            stage_name="More work",
            stage_description="Keep going",
            acceptance_criteria="Done",
        )
        advisor = self._make_advisor([always_next])
        advisor.max_stages = 3  # Low safety limit for testing

        orch = _FakeOrchestrator()
        plan = GoalPlan(context="ctx", stages=[])
        team = {"worker": make_agent("ok")}
        result = RunResult()

        orch._run_adaptive(
            "Endless goal",
            tmp_path,
            team,
            plan,
            result,
            stage_summaries=[],
            max_exchanges=30,
            remaining_cycles=50,
            start_stage_idx=0,
            config=CycleConfig(),
            advisor=advisor,
        )

        assert len(result.stage_results) == 3  # Capped at max_stages

    def test_cycle_limit_stops_run(self, tmp_path: Path):
        """Runs out of cycles before advisor says done."""
        always_next = AdvisorDecision(
            action="next_stage",
            stage_name="Work",
            stage_description="Do stuff",
            acceptance_criteria="OK",
        )
        advisor = self._make_advisor([always_next])

        orch = _FakeOrchestrator()
        plan = GoalPlan(context="ctx", stages=[])
        team = {"worker": make_agent("ok")}
        result = RunResult()

        orch._run_adaptive(
            "Goal",
            tmp_path,
            team,
            plan,
            result,
            stage_summaries=[],
            max_exchanges=30,
            remaining_cycles=2,  # Only 2 cycles
            start_stage_idx=0,
            config=CycleConfig(),
            advisor=advisor,
        )

        assert len(result.stage_results) == 2

    def test_waterfall_when_no_advisor(self, tmp_path: Path):
        """With advisor=None, _run_staged uses waterfall path."""
        orch = _FakeOrchestrator()
        plan = GoalPlan(
            context="ctx",
            stages=[
                GoalStage(1, "Stage A", "Do A", "A done"),
                GoalStage(2, "Stage B", "Do B", "B done"),
            ],
        )
        team = {"worker": make_agent("ok")}
        result = RunResult()

        orch._run_staged(
            "Goal",
            tmp_path,
            team,
            plan,
            result,
            max_exchanges=30,
            max_cycles=10,
            config=CycleConfig(),
            advisor=None,  # No advisor → waterfall
        )

        assert len(result.stage_results) == 2
        assert result.stage_results[0].stage_name == "Stage A"
        assert result.stage_results[1].stage_name == "Stage B"

    def test_stage_summaries_accumulate(self, tmp_path: Path):
        """Completed stage summaries are passed to advisor on each call."""
        received_summaries = []

        def capture_assess(goal, plan, summaries, count):
            received_summaries.append(list(summaries))
            if count >= 2:
                return AdvisorDecision(action="done", summary="done")
            return AdvisorDecision(
                action="next_stage",
                stage_name=f"Stage {count + 1}",
                stage_description="work",
                acceptance_criteria="ok",
            )

        with patch("kodo.orchestrators.advisor.PydanticAgent", autospec=True):
            advisor = Advisor(model="test-model")
        advisor.assess = capture_assess

        orch = _FakeOrchestrator()
        plan = GoalPlan(context="ctx", stages=[])
        team = {"worker": make_agent("ok")}
        result = RunResult()

        orch._run_adaptive(
            "Goal",
            tmp_path,
            team,
            plan,
            result,
            stage_summaries=[],
            max_exchanges=30,
            remaining_cycles=50,
            start_stage_idx=0,
            config=CycleConfig(),
            advisor=advisor,
        )

        # First call: no summaries
        assert received_summaries[0] == []
        # Second call: one summary from stage 1
        assert len(received_summaries[1]) == 1
        # Third call (done): two summaries
        assert len(received_summaries[2]) == 2

    def test_advisor_assess_crash_stops_gracefully(self, tmp_path: Path):
        """If advisor.assess() raises, the run stops with stages completed so far."""
        call_count = [0]

        def crashing_assess(goal, plan, summaries, count):
            call_count[0] += 1
            if call_count[0] == 1:
                return AdvisorDecision(
                    action="next_stage",
                    stage_name="Setup",
                    stage_description="Initialize",
                    acceptance_criteria="Built",
                )
            # Second call crashes
            raise RuntimeError("API timeout in advisor")

        with patch("kodo.orchestrators.advisor.PydanticAgent", autospec=True):
            advisor = Advisor(model="test-model", max_stages=20)
        advisor.assess = crashing_assess

        orch = _FakeOrchestrator()
        plan = GoalPlan(context="ctx", stages=[])
        team = {"worker": make_agent("ok")}
        result = RunResult()

        # Should NOT raise — the crash is caught and logged
        orch._run_adaptive(
            "Build app",
            tmp_path,
            team,
            plan,
            result,
            stage_summaries=[],
            max_exchanges=30,
            remaining_cycles=50,
            start_stage_idx=0,
            config=CycleConfig(),
            advisor=advisor,
        )

        # Stage 1 completed before the advisor crashed
        assert len(result.stage_results) == 1
        assert result.stage_results[0].finished is True
        assert result.stage_results[0].stage_name == "Setup"

    def test_advisor_assess_crash_on_first_call(self, tmp_path: Path):
        """If advisor.assess() raises on the very first call, no stages run."""
        def always_crash(goal, plan, summaries, count):
            raise ConnectionError("Network down")

        with patch("kodo.orchestrators.advisor.PydanticAgent", autospec=True):
            advisor = Advisor(model="test-model", max_stages=20)
        advisor.assess = always_crash

        orch = _FakeOrchestrator()
        plan = GoalPlan(context="ctx", stages=[])
        team = {"worker": make_agent("ok")}
        result = RunResult()

        orch._run_adaptive(
            "Goal",
            tmp_path,
            team,
            plan,
            result,
            stage_summaries=[],
            max_exchanges=30,
            remaining_cycles=50,
            start_stage_idx=0,
            config=CycleConfig(),
            advisor=advisor,
        )

        assert len(result.stage_results) == 0
        assert len(result.cycles) == 0


# ---------------------------------------------------------------------------
# _build_advisor helper
# ---------------------------------------------------------------------------


class TestBuildAdvisor:
    def test_returns_advisor_with_gemini_key(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
        from kodo.cli._launch import _build_advisor

        advisor = _build_advisor({"orchestrator_model": "gemini-flash"})
        assert advisor is not None
        assert "google-gla:" in advisor.model

    def test_returns_none_without_api_keys(self, monkeypatch):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        from kodo.cli._launch import _build_advisor

        advisor = _build_advisor({})
        assert advisor is None


# ---------------------------------------------------------------------------
# SessionAdvisor tests
# ---------------------------------------------------------------------------


def _mock_query_result(text: str, is_error: bool = False):
    """Create a mock QueryResult."""
    result = MagicMock()
    result.text = text
    result.is_error = is_error
    return result


class TestSessionAdvisor:
    def _make_advisor(self, tmp_path: Path, query_results: list) -> SessionAdvisor:
        session = MagicMock()
        session.query.side_effect = query_results
        return SessionAdvisor(session, tmp_path)

    def test_next_stage(self, tmp_path: Path):
        """Session returns valid next_stage JSON."""
        decision_json = json.dumps({
            "action": "next_stage",
            "stage_name": "Setup DB",
            "stage_description": "Create database schema",
            "acceptance_criteria": "Migrations run",
            "reasoning": "Need DB first",
        })
        advisor = self._make_advisor(tmp_path, [_mock_query_result(decision_json)])
        plan = GoalPlan(context="ctx", stages=[])

        result = advisor.assess("Build app", plan, [], 0)

        assert result.action == "next_stage"
        assert result.stage_name == "Setup DB"
        assert result.acceptance_criteria == "Migrations run"

    def test_done(self, tmp_path: Path):
        """Session returns done decision."""
        decision_json = json.dumps({
            "action": "done",
            "summary": "Everything is complete",
        })
        advisor = self._make_advisor(tmp_path, [_mock_query_result(decision_json)])
        plan = GoalPlan(context="ctx", stages=[])

        result = advisor.assess("Goal", plan, ["s1 done"], 1)

        assert result.action == "done"
        assert result.summary == "Everything is complete"

    def test_handles_error(self, tmp_path: Path):
        """Session error → conservative done."""
        advisor = self._make_advisor(
            tmp_path, [_mock_query_result("Connection lost", is_error=True)],
        )
        plan = GoalPlan(context="ctx", stages=[])

        result = advisor.assess("Goal", plan, [], 0)

        assert result.action == "done"
        assert "error" in result.summary.lower()

    def test_code_fence_json(self, tmp_path: Path):
        """JSON wrapped in ```json``` fences is extracted."""
        text = "Here's what I think:\n```json\n" + json.dumps({
            "action": "next_stage",
            "stage_name": "Tests",
            "stage_description": "Write unit tests",
            "acceptance_criteria": "All pass",
        }) + "\n```\nLet me know!"
        advisor = self._make_advisor(tmp_path, [_mock_query_result(text)])
        plan = GoalPlan(context="ctx", stages=[])

        result = advisor.assess("Goal", plan, [], 0)

        assert result.action == "next_stage"
        assert result.stage_name == "Tests"

    def test_close(self, tmp_path: Path):
        """close() terminates and closes the backing session."""
        session = MagicMock()
        advisor = SessionAdvisor(session, tmp_path)

        advisor.close()

        session.terminate.assert_called_once()
        session.close.assert_called_once()

    def test_transition_prompt(self, tmp_path: Path):
        """First assess call uses transition prompt with advisor role setup."""
        decision_json = json.dumps({"action": "done", "summary": "ok"})
        session = MagicMock()
        session.query.return_value = _mock_query_result(decision_json)
        advisor = SessionAdvisor(session, tmp_path)
        plan = GoalPlan(context="ctx", stages=[])

        advisor.assess("Goal", plan, [], 0)

        prompt_sent = session.query.call_args[0][0]
        assert "advisor" in prompt_sent.lower()
        assert "first" in prompt_sent.lower() or "planning" in prompt_sent.lower()

    def test_subsequent_prompt(self, tmp_path: Path):
        """Second+ assess calls use lightweight stage-result prompt."""
        decision_json = json.dumps({
            "action": "next_stage",
            "stage_name": "S2",
            "stage_description": "d",
            "acceptance_criteria": "c",
        })
        done_json = json.dumps({"action": "done", "summary": "ok"})
        session = MagicMock()
        session.query.side_effect = [
            _mock_query_result(decision_json),
            _mock_query_result(done_json),
        ]
        advisor = SessionAdvisor(session, tmp_path)
        plan = GoalPlan(context="ctx", stages=[])

        advisor.assess("Goal", plan, [], 0)
        advisor.assess("Goal", plan, ["Stage 1 results here"], 1)

        second_prompt = session.query.call_args_list[1][0][0]
        assert "Stage 1 results here" in second_prompt
        assert "completed" in second_prompt.lower()


# ---------------------------------------------------------------------------
# _parse_advisor_json tests
# ---------------------------------------------------------------------------


class TestParseAdvisorJson:
    def test_raw_json(self):
        text = json.dumps({"action": "next_stage", "stage_name": "X"})
        result = _parse_advisor_json(text)
        assert result.action == "next_stage"
        assert result.stage_name == "X"

    def test_code_fence(self):
        text = "```json\n" + json.dumps({"action": "done", "summary": "fin"}) + "\n```"
        result = _parse_advisor_json(text)
        assert result.action == "done"
        assert result.summary == "fin"

    def test_embedded_json(self):
        text = 'I think we should continue. {"action": "next_stage", "stage_name": "Y"} That is my recommendation.'
        result = _parse_advisor_json(text)
        assert result.action == "next_stage"
        assert result.stage_name == "Y"

    def test_fallback_on_garbage(self):
        result = _parse_advisor_json("This is not JSON at all")
        assert result.action == "done"
        assert "could not parse" in result.summary.lower()

    def test_empty_text(self):
        result = _parse_advisor_json("")
        assert result.action == "done"

    def test_none_text(self):
        result = _parse_advisor_json(None)
        assert result.action == "done"


# ---------------------------------------------------------------------------
# _build_session_assess_prompt tests
# ---------------------------------------------------------------------------


class TestBuildSessionAssessPrompt:
    def test_first_call_is_transition(self):
        prompt = _build_session_assess_prompt([], 0, started=False)
        assert "advisor" in prompt.lower()
        assert "json" in prompt.lower()

    def test_subsequent_includes_summary(self):
        prompt = _build_session_assess_prompt(["Auth was implemented"], 1, started=True)
        assert "Auth was implemented" in prompt
        assert "Stage 1" in prompt

    def test_truncates_long_summary(self):
        long_summary = "x" * 3000
        prompt = _build_session_assess_prompt([long_summary], 1, started=True)
        # Should be truncated to ~2000 chars
        assert len(prompt) < 3000
