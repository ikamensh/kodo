"""Tests for knowledge orchestrator."""

from pathlib import Path

from kodo.knowledge.models import KnowledgeGoal, Workspace
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
