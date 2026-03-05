"""Tests for knowledge API sessions."""

from kodo.knowledge.models import AgentRole
from kodo.knowledge.sessions import ApiSession, make_knowledge_session


class TestApiSession:
    def test_init(self):
        s = ApiSession(model="test-model", system_prompt="You are a tester")
        assert s.model == "test-model"
        assert s.cost_bucket == "api"
        assert s.stats.queries == 0
        assert s.session_id is None

    def test_reset(self):
        s = ApiSession(model="test-model")
        s._stats.queries = 5
        s.reset()
        assert s.stats.queries == 0

    def test_clone(self):
        s = ApiSession(model="test-model", system_prompt="prompt")
        c = s.clone()
        assert c.model == s.model
        assert c._system_prompt == s._system_prompt
        assert c is not s

    def test_terminate_and_close_are_noop(self):
        s = ApiSession(model="test-model")
        s.terminate()  # should not raise
        s.close()  # should not raise


class TestMakeKnowledgeSession:
    def test_creates_session_with_role_prompt(self):
        role = AgentRole(
            name="writer",
            system_prompt="You write excellent prose.",
            model_preference="best",
        )
        session = make_knowledge_session(role)
        assert isinstance(session, ApiSession)
        assert session._system_prompt == "You write excellent prose."

    def test_compute_tool_added(self):
        role = AgentRole(
            name="calculator",
            system_prompt="You compute things.",
            model_preference="fast",
            tools=["compute"],
        )
        session = make_knowledge_session(role)
        assert len(session._tools) == 1

    def test_no_tools_by_default(self):
        role = AgentRole(name="thinker", system_prompt="Think deeply.")
        session = make_knowledge_session(role)
        assert len(session._tools) == 0
