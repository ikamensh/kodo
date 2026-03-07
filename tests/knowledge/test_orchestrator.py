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
