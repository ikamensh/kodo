"""Tests for knowledge orchestrator."""

from unittest import mock

import pytest

from kodo.knowledge.models import (
    AgentRole,
    ConvergenceState,
    KnowledgeGoal,
    PatternType,
    QuestionType,
    TeamDesign,
    Workspace,
)
from kodo.knowledge.orchestrator import KnowledgeOrchestrator


class TestSeedReferences:
    def test_loads_existing_file(self, tmp_path):
        ref = tmp_path / "article.md"
        ref.write_text("# My Article\nContent here")

        goal = KnowledgeGoal(
            goal="test",
            reference_files=[str(ref)],
        )
        ws = Workspace()
        KnowledgeOrchestrator._seed_references(goal, ws)

        assert ws.read("ref_article") == "# My Article\nContent here"

    def test_multiple_references(self, tmp_path):
        (tmp_path / "a.md").write_text("doc A")
        (tmp_path / "b.txt").write_text("doc B")

        goal = KnowledgeGoal(
            goal="test",
            reference_files=[str(tmp_path / "a.md"), str(tmp_path / "b.txt")],
        )
        ws = Workspace()
        KnowledgeOrchestrator._seed_references(goal, ws)

        assert ws.read("ref_a") == "doc A"
        assert ws.read("ref_b") == "doc B"

    def test_missing_file_skipped(self, tmp_path):
        goal = KnowledgeGoal(
            goal="test",
            reference_files=[str(tmp_path / "nonexistent.md")],
        )
        ws = Workspace()
        KnowledgeOrchestrator._seed_references(goal, ws)

        assert ws.list_artifacts() == []

    def test_no_references(self):
        goal = KnowledgeGoal(goal="test")
        ws = Workspace()
        KnowledgeOrchestrator._seed_references(goal, ws)
        assert ws.list_artifacts() == []


class TestSessionWithWorkspaceTools:
    def test_agent_gets_read_artifact_tool(self):
        from kodo.knowledge.models import AgentRole
        from kodo.knowledge.sessions import make_knowledge_session

        ws = Workspace()
        ws.write("ref_doc", "hello")

        role = AgentRole(
            name="reader",
            system_prompt="Read things",
            tools=["read_artifact"],
        )
        session = make_knowledge_session(role, workspace=ws)
        assert len(session._tools) == 1

    def test_agent_gets_write_artifact_tool(self):
        from kodo.knowledge.models import AgentRole
        from kodo.knowledge.sessions import make_knowledge_session

        ws = Workspace()
        role = AgentRole(
            name="writer",
            system_prompt="Write things",
            tools=["write_artifact"],
        )
        session = make_knowledge_session(role, workspace=ws)
        assert len(session._tools) == 1

    def test_no_workspace_no_artifact_tools(self):
        from kodo.knowledge.models import AgentRole
        from kodo.knowledge.sessions import make_knowledge_session

        role = AgentRole(
            name="reader",
            system_prompt="Read things",
            tools=["read_artifact", "write_artifact"],
        )
        session = make_knowledge_session(role, workspace=None)
        assert len(session._tools) == 0


# ── Tier 1: Initialization and Team Building ──────────────────────────


class TestOrchestratorInit:
    def test_default_model_mapping(self):
        """PYDANTIC_MODEL_MAP lookup works for known models."""
        from kodo.models import CLAUDE_OPUS_FULL

        orch = KnowledgeOrchestrator(model=CLAUDE_OPUS_FULL)

        # Should map to "anthropic:claude-opus-4-6"
        assert orch._pydantic_model.startswith("anthropic:")
        assert CLAUDE_OPUS_FULL in orch._pydantic_model

    def test_custom_designer_and_agent_model(self):
        """Separate designer_model and agent_model are honored."""
        from kodo.models import CLAUDE_SONNET_FULL, GEMINI_API_FLASH

        orch = KnowledgeOrchestrator(
            model="claude-opus-4-6",
            designer_model=GEMINI_API_FLASH,
            agent_model=CLAUDE_SONNET_FULL,
        )

        # Designer uses flash (cheap)
        assert "flash" in orch._designer_model.lower()

        # Agents use sonnet
        assert orch._agent_model == CLAUDE_SONNET_FULL

    def test_unknown_model_passthrough(self):
        """Raw string is used if not in PYDANTIC_MODEL_MAP."""
        orch = KnowledgeOrchestrator(model="unknown-model-xyz")

        # Should pass through as-is
        assert orch._pydantic_model == "unknown-model-xyz"


class TestBuildTeam:
    def test_single_role_creates_one_agent(self):
        """Team dict has one entry for single-role design."""
        design = TeamDesign(
            pattern=PatternType.DEEPENING,
            question_type=QuestionType.RESEARCH,
            rationale="Test",
            roles=[
                AgentRole(
                    name="analyzer",
                    system_prompt="Analyze things",
                    model_preference="best",
                    tools=[],
                )
            ],
        )
        workspace = Workspace()
        orch = KnowledgeOrchestrator()

        team = orch._build_team(design, workspace)

        assert len(team) == 1
        assert "analyzer" in team
        assert team["analyzer"].description.startswith("Analyze")

    def test_multiple_roles(self):
        """Team dict is keyed by role name for multi-role design."""
        design = TeamDesign(
            pattern=PatternType.ADVERSARIAL,
            question_type=QuestionType.RESEARCH,
            rationale="Test",
            roles=[
                AgentRole(
                    name="prover",
                    system_prompt="Prove things",
                    model_preference="best",
                    tools=[],
                ),
                AgentRole(
                    name="critic",
                    system_prompt="Critique things",
                    model_preference="fast",
                    tools=[],
                ),
            ],
        )
        workspace = Workspace()
        orch = KnowledgeOrchestrator()

        team = orch._build_team(design, workspace)

        assert len(team) == 2
        assert "prover" in team
        assert "critic" in team

    def test_session_receives_correct_params(self):
        """make_knowledge_session is called with correct arguments."""
        design = TeamDesign(
            pattern=PatternType.DEEPENING,
            question_type=QuestionType.RESEARCH,
            rationale="Test",
            roles=[
                AgentRole(
                    name="tester",
                    system_prompt="Test prompt",
                    model_preference="fast",
                    tools=["read_artifact"],
                )
            ],
        )
        workspace = Workspace()
        workspace.write("test_artifact", "content")
        orch = KnowledgeOrchestrator(agent_model="claude-sonnet-4-6")

        with mock.patch(
            "kodo.knowledge.sessions.make_knowledge_session"
        ) as mock_make:
            # Create a fake session that the Agent constructor can use
            from kodo.sessions.base import QueryResult, SessionStats

            class FakeSession:
                @property
                def stats(self):
                    return SessionStats()

                @property
                def cost_bucket(self):
                    return "api"

                @property
                def session_id(self):
                    return None

                def query(self, *args, **kwargs):
                    return QueryResult(text="done", elapsed_s=0.1)

                def reset(self):
                    pass

                def terminate(self):
                    pass

                def close(self):
                    pass

                def clone(self):
                    return FakeSession()

            mock_make.return_value = FakeSession()

            team = orch._build_team(design, workspace)

            # Verify make_knowledge_session was called correctly
            assert mock_make.call_count == 1
            call_kwargs = mock_make.call_args.kwargs
            assert call_kwargs["role"] == design.roles[0]
            assert call_kwargs["default_model"] == "claude-sonnet-4-6"
            assert call_kwargs["workspace"] is workspace


# ── Tier 2: Convergence Assessment ────────────────────────────────────


class TestAutoAssess:
    def test_first_round_empty_previous(self):
        """First round has empty previous answer."""
        goal = KnowledgeGoal(goal="Test question")
        workspace = Workspace()
        workspace.write("answer", "Initial answer")
        convergence = ConvergenceState()
        convergence.round_number = 1
        orch = KnowledgeOrchestrator()

        with mock.patch("kodo.knowledge.convergence.assess") as mock_assess:
            mock_assess.return_value = {
                "confidence": 0.5,
                "stability": 0.3,
                "agreement": 0.4,
                "completeness": 0.6,
            }

            orch._auto_assess_convergence(goal, workspace, convergence)

            # Verify assess was called with empty previous
            call_args = mock_assess.call_args.kwargs
            assert call_args["previous_answer"] == ""
            assert call_args["current_answer"] == "Initial answer"

    def test_subsequent_round_uses_history(self):
        """Subsequent rounds use previous answer from history."""
        goal = KnowledgeGoal(goal="Test question")
        workspace = Workspace()
        workspace.write("answer", "Updated answer")
        convergence = ConvergenceState()
        convergence.round_number = 2
        convergence.history = [
            {
                "round": 1,
                "confidence": 0.5,
                "answer_snapshot": "Previous answer text",
            }
        ]
        orch = KnowledgeOrchestrator()

        with mock.patch("kodo.knowledge.convergence.assess") as mock_assess:
            mock_assess.return_value = {
                "confidence": 0.7,
                "stability": 0.6,
                "agreement": 0.8,
                "completeness": 0.7,
            }

            orch._auto_assess_convergence(goal, workspace, convergence)

            # Verify assess was called with previous from history
            call_args = mock_assess.call_args.kwargs
            assert call_args["previous_answer"] == "Previous answer text"
            assert call_args["current_answer"] == "Updated answer"

    def test_assessment_updates_convergence(self):
        """Convergence state fields are updated from assessment."""
        goal = KnowledgeGoal(goal="Test question")
        workspace = Workspace()
        workspace.write("answer", "Answer text")
        convergence = ConvergenceState()
        convergence.round_number = 1
        orch = KnowledgeOrchestrator()

        with mock.patch("kodo.knowledge.convergence.assess") as mock_assess:
            mock_assess.return_value = {
                "confidence": 0.85,
                "stability": 0.90,
                "agreement": 0.75,
                "completeness": 0.80,
            }

            orch._auto_assess_convergence(goal, workspace, convergence)

            # Verify convergence fields were updated
            assert convergence.confidence == 0.85
            assert convergence.stability == 0.90
            assert convergence.agreement == 0.75
            assert convergence.completeness == 0.80

    def test_history_entry_appended(self):
        """History grows and answer snapshot is truncated."""
        goal = KnowledgeGoal(goal="Test question")
        workspace = Workspace()
        # Create a long answer that should be truncated
        long_answer = "x" * 3000
        workspace.write("answer", long_answer)
        convergence = ConvergenceState()
        convergence.round_number = 1
        convergence.history = []
        orch = KnowledgeOrchestrator()

        with mock.patch("kodo.knowledge.convergence.assess") as mock_assess:
            mock_assess.return_value = {
                "confidence": 0.6,
                "stability": 0.5,
                "agreement": 0.7,
                "completeness": 0.6,
            }

            orch._auto_assess_convergence(goal, workspace, convergence)

            # Verify history was appended
            assert len(convergence.history) == 1
            entry = convergence.history[0]
            assert entry["round"] == 1
            assert entry["confidence"] == 0.6
            assert entry["stability"] == 0.5
            assert entry["agreement"] == 0.7
            assert entry["completeness"] == 0.6

            # Verify snapshot was truncated to 2000 chars
            assert len(entry["answer_snapshot"]) == 2000
            assert entry["answer_snapshot"] == "x" * 2000


# ── Tier 3: Run Loop ───────────────────────────────────────────────────


class TestRunLoop:
    def test_single_round_finished(self):
        """Loop breaks immediately when finished=True in first round."""
        from kodo.orchestrators.base import CycleResult

        goal = KnowledgeGoal(goal="Test question")
        design = TeamDesign(
            pattern=PatternType.DEEPENING,
            question_type=QuestionType.RESEARCH,
            rationale="Test",
            roles=[
                AgentRole(
                    name="worker",
                    system_prompt="Work on things",
                    model_preference="best",
                    tools=[],
                )
            ],
        )
        workspace = Workspace()
        workspace.write("answer", "Final answer")
        team = {}
        orch = KnowledgeOrchestrator()

        # Mock _run_round to return finished=True immediately
        with mock.patch.object(orch, "_run_round") as mock_round:
            cycle_result = CycleResult()
            cycle_result.finished = True
            cycle_result.total_cost_usd = 0.5
            mock_round.return_value = cycle_result

            with mock.patch.object(orch, "_auto_assess_convergence"):
                result = orch._run_loop(goal, design, team, workspace)

            # Should only run once
            assert mock_round.call_count == 1
            assert result.rounds_used == 1

    def test_convergence_breaks_loop(self):
        """Loop breaks when converged=True after assessment."""
        from kodo.orchestrators.base import CycleResult

        goal = KnowledgeGoal(goal="Test question")
        design = TeamDesign(
            pattern=PatternType.DEEPENING,
            question_type=QuestionType.RESEARCH,
            rationale="Test",
            roles=[
                AgentRole(
                    name="worker",
                    system_prompt="Work",
                    model_preference="best",
                    tools=[],
                )
            ],
        )
        workspace = Workspace()
        workspace.write("answer", "Converged answer")
        team = {}
        orch = KnowledgeOrchestrator()

        # Mock _run_round to return not finished
        with mock.patch.object(orch, "_run_round") as mock_round:
            cycle_result = CycleResult()
            cycle_result.finished = False
            cycle_result.total_cost_usd = 0.3
            mock_round.return_value = cycle_result

            # Mock assessment to set low values round 1, high round 2 (triggers convergence)
            def mock_assess(goal, workspace, convergence):
                if convergence.round_number == 1:
                    convergence.confidence = 0.70
                    convergence.stability = 0.65
                else:
                    convergence.confidence = 0.95
                    convergence.stability = 0.90

            with mock.patch.object(orch, "_auto_assess_convergence", side_effect=mock_assess):
                result = orch._run_loop(goal, design, team, workspace)

            # Should run 2 rounds then converge
            assert mock_round.call_count == 2
            assert result.rounds_used == 2

    def test_max_rounds_exhausted(self):
        """Loop runs all max_rounds when not converged."""
        from kodo.orchestrators.base import CycleResult

        goal = KnowledgeGoal(goal="Test question", effort="standard")  # standard has max_rounds=3
        design = TeamDesign(
            pattern=PatternType.DEEPENING,
            question_type=QuestionType.RESEARCH,
            rationale="Test",
            roles=[
                AgentRole(
                    name="worker",
                    system_prompt="Work",
                    model_preference="best",
                    tools=[],
                )
            ],
        )
        workspace = Workspace()
        workspace.write("answer", "Partial answer")
        team = {}
        orch = KnowledgeOrchestrator()

        # Mock _run_round to never finish
        with mock.patch.object(orch, "_run_round") as mock_round:
            cycle_result = CycleResult()
            cycle_result.finished = False
            cycle_result.total_cost_usd = 0.2
            mock_round.return_value = cycle_result

            # Mock assessment to never converge (confidence/stability stay low)
            def mock_assess(goal, workspace, convergence):
                convergence.confidence = 0.5
                convergence.stability = 0.4
                # converged is a computed property based on these values

            with mock.patch.object(orch, "_auto_assess_convergence", side_effect=mock_assess):
                result = orch._run_loop(goal, design, team, workspace)

            # Should run all 3 rounds for "standard" effort
            assert mock_round.call_count == 3
            assert result.rounds_used == 3

    def test_multiple_rounds_accumulate_cost(self):
        """Total cost accumulates across rounds correctly."""
        from kodo.orchestrators.base import CycleResult

        goal = KnowledgeGoal(goal="Test question", effort="standard")
        design = TeamDesign(
            pattern=PatternType.DEEPENING,
            question_type=QuestionType.RESEARCH,
            rationale="Test",
            roles=[
                AgentRole(
                    name="worker",
                    system_prompt="Work",
                    model_preference="best",
                    tools=[],
                )
            ],
        )
        workspace = Workspace()
        workspace.write("answer", "Answer")
        team = {}
        orch = KnowledgeOrchestrator()

        # Mock _run_round to return different costs each round
        costs = [0.25, 0.35, 0.40]
        call_count = [0]

        def mock_run_round(*args, **kwargs):
            idx = call_count[0]
            call_count[0] += 1
            result = CycleResult()
            result.finished = False
            result.total_cost_usd = costs[idx] if idx < len(costs) else 0
            return result

        # Mock assessment to never converge
        def mock_assess(goal, workspace, convergence):
            convergence.confidence = 0.5
            convergence.stability = 0.4

        with mock.patch.object(orch, "_run_round", side_effect=mock_run_round):
            with mock.patch.object(orch, "_auto_assess_convergence", side_effect=mock_assess):
                result = orch._run_loop(goal, design, team, workspace)

            # Total cost should be sum of all rounds
            assert result.total_cost_usd == sum(costs)

    def test_answer_from_workspace(self):
        """Result uses answer from workspace artifact."""
        from kodo.orchestrators.base import CycleResult

        goal = KnowledgeGoal(goal="Test question")
        design = TeamDesign(
            pattern=PatternType.DEEPENING,
            question_type=QuestionType.RESEARCH,
            rationale="Test",
            roles=[
                AgentRole(
                    name="worker",
                    system_prompt="Work",
                    model_preference="best",
                    tools=[],
                )
            ],
        )
        workspace = Workspace()
        workspace.write("answer", "This is the final answer from workspace")
        team = {}
        orch = KnowledgeOrchestrator()

        with mock.patch.object(orch, "_run_round") as mock_round:
            cycle_result = CycleResult()
            cycle_result.finished = True
            cycle_result.summary = "Different summary"
            mock_round.return_value = cycle_result

            with mock.patch.object(orch, "_auto_assess_convergence"):
                result = orch._run_loop(goal, design, team, workspace)

            # Should use workspace answer, not summary
            assert result.answer == "This is the final answer from workspace"

    def test_fallback_to_cycle_summary(self):
        """Result falls back to cycle summary when no answer artifact."""
        from kodo.orchestrators.base import CycleResult

        goal = KnowledgeGoal(goal="Test question")
        design = TeamDesign(
            pattern=PatternType.DEEPENING,
            question_type=QuestionType.RESEARCH,
            rationale="Test",
            roles=[
                AgentRole(
                    name="worker",
                    system_prompt="Work",
                    model_preference="best",
                    tools=[],
                )
            ],
        )
        workspace = Workspace()
        # No "answer" artifact
        team = {}
        orch = KnowledgeOrchestrator()

        with mock.patch.object(orch, "_run_round") as mock_round:
            cycle_result = CycleResult()
            cycle_result.finished = True
            cycle_result.summary = "Summary from cycle"
            mock_round.return_value = cycle_result

            with mock.patch.object(orch, "_auto_assess_convergence"):
                result = orch._run_loop(goal, design, team, workspace)

            # Should use summary
            assert result.answer == "Summary from cycle"

    def test_fallback_to_no_answer_produced(self):
        """Result shows placeholder when neither answer nor summary exist."""
        from kodo.orchestrators.base import CycleResult

        goal = KnowledgeGoal(goal="Test question")
        design = TeamDesign(
            pattern=PatternType.DEEPENING,
            question_type=QuestionType.RESEARCH,
            rationale="Test",
            roles=[
                AgentRole(
                    name="worker",
                    system_prompt="Work",
                    model_preference="best",
                    tools=[],
                )
            ],
        )
        workspace = Workspace()
        # No answer artifact
        team = {}
        orch = KnowledgeOrchestrator()

        with mock.patch.object(orch, "_run_round") as mock_round:
            cycle_result = CycleResult()
            cycle_result.finished = True
            cycle_result.summary = None  # No summary either
            mock_round.return_value = cycle_result

            with mock.patch.object(orch, "_auto_assess_convergence"):
                result = orch._run_loop(goal, design, team, workspace)

            # Should show placeholder
            assert result.answer == "(no answer produced)"

    def test_result_fields_propagated(self):
        """Result has correct verdict_type, confidence, rounds_used."""
        from kodo.orchestrators.base import CycleResult

        goal = KnowledgeGoal(goal="Test question")
        design = TeamDesign(
            pattern=PatternType.DEEPENING,
            question_type=QuestionType.RESEARCH,
            rationale="Test",
            roles=[
                AgentRole(
                    name="worker",
                    system_prompt="Work",
                    model_preference="best",
                    tools=[],
                )
            ],
        )
        workspace = Workspace()
        workspace.write("answer", "Answer")
        workspace.write("reasoning_trace", "Reasoning")
        workspace.write("open_questions", "Questions")
        team = {}
        orch = KnowledgeOrchestrator()

        with mock.patch.object(orch, "_run_round") as mock_round:
            cycle_result = CycleResult()
            cycle_result.finished = True
            cycle_result.total_cost_usd = 1.25
            mock_round.return_value = cycle_result

            def mock_assess(goal, workspace, convergence):
                convergence.confidence = 0.92
                convergence.stability = 0.92
                # verdict_type is computed from confidence

            with mock.patch.object(orch, "_auto_assess_convergence", side_effect=mock_assess):
                result = orch._run_loop(goal, design, team, workspace)

            # Verify all fields propagated
            # verdict_type is computed: 0.92 confidence -> "strong_conclusion" (>= 0.9)
            assert result.verdict_type == "strong_conclusion"
            assert result.confidence == 0.92
            assert result.rounds_used == 1
            assert result.total_cost_usd == 1.25
            assert result.reasoning_trace == "Reasoning"
            assert result.open_questions == "Questions"


# ── Tier 4: Prompt Construction ───────────────────────────────────────


class TestPromptConstruction:
    def test_basic_goal_in_prompt(self):
        """Goal text always appears in user prompt."""
        from kodo.orchestrators.base import CycleResult

        goal = KnowledgeGoal(goal="What is the capital of France?")
        design = TeamDesign(
            pattern=PatternType.DEEPENING,
            question_type=QuestionType.RESEARCH,
            rationale="Test",
            roles=[
                AgentRole(
                    name="worker",
                    system_prompt="Work",
                    model_preference="best",
                    tools=[],
                )
            ],
        )
        workspace = Workspace()
        convergence = ConvergenceState()
        convergence.round_number = 1
        team = {}
        orch = KnowledgeOrchestrator()

        captured_prompt = []

        def capture_agent_init(model, **kwargs):
            # Capture the agent initialization
            class FakeAgent:
                def run_sync(self, user_prompt, **kwargs):
                    captured_prompt.append(user_prompt)
                    result = mock.MagicMock()
                    result.usage.return_value = mock.MagicMock(
                        input_tokens=100, output_tokens=50, requests=1
                    )
                    result.output = "done"
                    return result

            return FakeAgent()

        with mock.patch("kodo.knowledge.orchestrator.PydanticAgent", side_effect=capture_agent_init):
            orch._run_round(goal, design, team, workspace, convergence, max_exchanges=10)

        assert len(captured_prompt) == 1
        assert "What is the capital of France?" in captured_prompt[0]

    def test_constraints_appended(self):
        """Constraints appear in user prompt when present."""
        from kodo.orchestrators.base import CycleResult

        goal = KnowledgeGoal(
            goal="Analyze the data",
            constraints=["Use only primary sources", "Cite all references"],
        )
        design = TeamDesign(
            pattern=PatternType.DEEPENING,
            question_type=QuestionType.RESEARCH,
            rationale="Test",
            roles=[
                AgentRole(
                    name="worker",
                    system_prompt="Work",
                    model_preference="best",
                    tools=[],
                )
            ],
        )
        workspace = Workspace()
        convergence = ConvergenceState()
        convergence.round_number = 1
        team = {}
        orch = KnowledgeOrchestrator()

        captured_prompt = []

        def capture_agent_init(model, **kwargs):
            class FakeAgent:
                def run_sync(self, user_prompt, **kwargs):
                    captured_prompt.append(user_prompt)
                    result = mock.MagicMock()
                    result.usage.return_value = mock.MagicMock(
                        input_tokens=100, output_tokens=50, requests=1
                    )
                    result.output = "done"
                    return result

            return FakeAgent()

        with mock.patch("kodo.knowledge.orchestrator.PydanticAgent", side_effect=capture_agent_init):
            orch._run_round(goal, design, team, workspace, convergence, max_exchanges=10)

        prompt = captured_prompt[0]
        assert "Constraints" in prompt
        assert "Use only primary sources" in prompt
        assert "Cite all references" in prompt

    def test_output_format_appended(self):
        """Output format appears in user prompt when specified."""
        goal = KnowledgeGoal(
            goal="Write a report",
            output_format="# Title\n## Section 1\n## Section 2",
        )
        design = TeamDesign(
            pattern=PatternType.DEEPENING,
            question_type=QuestionType.RESEARCH,
            rationale="Test",
            roles=[
                AgentRole(
                    name="worker",
                    system_prompt="Work",
                    model_preference="best",
                    tools=[],
                )
            ],
        )
        workspace = Workspace()
        convergence = ConvergenceState()
        convergence.round_number = 1
        team = {}
        orch = KnowledgeOrchestrator()

        captured_prompt = []

        def capture_agent_init(model, **kwargs):
            class FakeAgent:
                def run_sync(self, user_prompt, **kwargs):
                    captured_prompt.append(user_prompt)
                    result = mock.MagicMock()
                    result.usage.return_value = mock.MagicMock(
                        input_tokens=100, output_tokens=50, requests=1
                    )
                    result.output = "done"
                    return result

            return FakeAgent()

        with mock.patch("kodo.knowledge.orchestrator.PydanticAgent", side_effect=capture_agent_init):
            orch._run_round(goal, design, team, workspace, convergence, max_exchanges=10)

        prompt = captured_prompt[0]
        assert "Output format" in prompt
        assert "# Title" in prompt
        assert "MUST be in this format" in prompt

    def test_reference_artifacts_listed(self):
        """Reference artifacts (ref_*) are listed in prompt."""
        goal = KnowledgeGoal(goal="Summarize the articles")
        design = TeamDesign(
            pattern=PatternType.DEEPENING,
            question_type=QuestionType.RESEARCH,
            rationale="Test",
            roles=[
                AgentRole(
                    name="worker",
                    system_prompt="Work",
                    model_preference="best",
                    tools=[],
                )
            ],
        )
        workspace = Workspace()
        workspace.write("ref_article1", "Article 1 content")
        workspace.write("ref_article2", "Article 2 content")
        workspace.write("other_data", "Not a reference")
        convergence = ConvergenceState()
        convergence.round_number = 1
        team = {}
        orch = KnowledgeOrchestrator()

        captured_prompt = []

        def capture_agent_init(model, **kwargs):
            class FakeAgent:
                def run_sync(self, user_prompt, **kwargs):
                    captured_prompt.append(user_prompt)
                    result = mock.MagicMock()
                    result.usage.return_value = mock.MagicMock(
                        input_tokens=100, output_tokens=50, requests=1
                    )
                    result.output = "done"
                    return result

            return FakeAgent()

        with mock.patch("kodo.knowledge.orchestrator.PydanticAgent", side_effect=capture_agent_init):
            orch._run_round(goal, design, team, workspace, convergence, max_exchanges=10)

        prompt = captured_prompt[0]
        assert "Reference materials" in prompt
        # Verify ref_* artifacts are listed in reference section
        ref_section_start = prompt.index("Reference materials")
        workspace_section_start = prompt.index("Current workspace") if "Current workspace" in prompt else len(prompt)
        ref_section = prompt[ref_section_start:workspace_section_start]
        assert "ref_article1" in ref_section
        assert "ref_article2" in ref_section
        # other_data should NOT be in reference section (it's not a ref_* artifact)
        assert "other_data" not in ref_section

    def test_workspace_state_included(self):
        """Non-empty workspace snapshot appears in prompt."""
        goal = KnowledgeGoal(goal="Continue work")
        design = TeamDesign(
            pattern=PatternType.DEEPENING,
            question_type=QuestionType.RESEARCH,
            rationale="Test",
            roles=[
                AgentRole(
                    name="worker",
                    system_prompt="Work",
                    model_preference="best",
                    tools=[],
                )
            ],
        )
        workspace = Workspace()
        workspace.write("draft", "Draft content here")
        workspace.write("notes", "Some notes")
        convergence = ConvergenceState()
        convergence.round_number = 1
        team = {}
        orch = KnowledgeOrchestrator()

        captured_prompt = []

        def capture_agent_init(model, **kwargs):
            class FakeAgent:
                def run_sync(self, user_prompt, **kwargs):
                    captured_prompt.append(user_prompt)
                    result = mock.MagicMock()
                    result.usage.return_value = mock.MagicMock(
                        input_tokens=100, output_tokens=50, requests=1
                    )
                    result.output = "done"
                    return result

            return FakeAgent()

        with mock.patch("kodo.knowledge.orchestrator.PydanticAgent", side_effect=capture_agent_init):
            orch._run_round(goal, design, team, workspace, convergence, max_exchanges=10)

        prompt = captured_prompt[0]
        assert "Current workspace" in prompt
        assert "draft" in prompt or "notes" in prompt  # Snapshot may truncate

    def test_previous_assessment_in_round_2(self):
        """Round 2+ shows previous confidence/stability from history."""
        goal = KnowledgeGoal(goal="Continue improving")
        design = TeamDesign(
            pattern=PatternType.DEEPENING,
            question_type=QuestionType.RESEARCH,
            rationale="Test",
            roles=[
                AgentRole(
                    name="worker",
                    system_prompt="Work",
                    model_preference="best",
                    tools=[],
                )
            ],
        )
        workspace = Workspace()
        workspace.write("answer", "Previous answer")
        convergence = ConvergenceState()
        convergence.round_number = 2
        convergence.history = [
            {
                "round": 1,
                "confidence": 0.65,
                "stability": 0.58,
            }
        ]
        team = {}
        orch = KnowledgeOrchestrator()

        captured_prompt = []

        def capture_agent_init(model, **kwargs):
            class FakeAgent:
                def run_sync(self, user_prompt, **kwargs):
                    captured_prompt.append(user_prompt)
                    result = mock.MagicMock()
                    result.usage.return_value = mock.MagicMock(
                        input_tokens=100, output_tokens=50, requests=1
                    )
                    result.output = "done"
                    return result

            return FakeAgent()

        with mock.patch("kodo.knowledge.orchestrator.PydanticAgent", side_effect=capture_agent_init):
            orch._run_round(goal, design, team, workspace, convergence, max_exchanges=10)

        prompt = captured_prompt[0]
        assert "Previous round assessment" in prompt
        assert "0.65" in prompt  # confidence
        assert "0.58" in prompt  # stability
