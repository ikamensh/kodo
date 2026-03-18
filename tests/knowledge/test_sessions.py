"""Tests for knowledge API sessions."""

from unittest.mock import MagicMock, patch

from pydantic_ai import Tool

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


class TestApiSessionQuery:
    """Test the query() method with various scenarios."""

    @patch("kodo.knowledge.sessions.run_in_thread", autospec=True)
    def test_query_successful(self, mock_run_in_thread, tmp_path):
        """Test successful query execution with token tracking."""
        # Mock successful agent result
        mock_result = MagicMock()
        mock_result.output = "This is the answer"
        mock_usage = MagicMock()
        mock_usage.input_tokens = 100
        mock_usage.output_tokens = 50
        mock_usage.requests = 1
        mock_result.usage.return_value = mock_usage
        mock_run_in_thread.return_value = mock_result

        session = ApiSession(model="test-model", system_prompt="Test prompt")
        result = session.query("What is 2+2?", tmp_path)

        # Verify result
        assert result.text == "This is the answer"
        assert result.input_tokens == 100
        assert result.output_tokens == 50
        assert result.turns == 1
        assert not result.is_error

        # Verify stats updated
        assert session.stats.queries == 1
        assert session.stats.total_input_tokens == 100
        assert session.stats.total_output_tokens == 50

        # Verify run_in_thread was called
        mock_run_in_thread.assert_called_once()

    @patch("kodo.knowledge.sessions.run_in_thread", autospec=True)
    def test_query_with_empty_output(self, mock_run_in_thread, tmp_path):
        """Test query when agent returns empty output."""
        mock_result = MagicMock()
        mock_result.output = None
        mock_usage = MagicMock()
        mock_usage.input_tokens = 10
        mock_usage.output_tokens = 5
        mock_usage.requests = 1
        mock_result.usage.return_value = mock_usage
        mock_run_in_thread.return_value = mock_result

        session = ApiSession(model="test-model")
        result = session.query("test", tmp_path)

        # Should return empty string for None output
        assert result.text == ""
        assert not result.is_error

    @patch("kodo.knowledge.sessions.run_in_thread", autospec=True)
    def test_query_unexpected_model_behavior(self, mock_run_in_thread, tmp_path):
        """Test that UnexpectedModelBehavior is treated as partial success."""

        # Create exception with UnexpectedModelBehavior in name
        class UnexpectedModelBehavior(Exception):
            pass

        mock_run_in_thread.side_effect = UnexpectedModelBehavior(
            "tool used but no text"
        )

        session = ApiSession(model="test-model")
        result = session.query("test", tmp_path)

        # Verify partial success behavior
        assert result.text == "(Agent completed tool work but output validation failed)"
        assert not result.is_error
        assert session.stats.queries == 1  # Still increments

    @patch("kodo.knowledge.sessions.run_in_thread", autospec=True)
    def test_query_http_error(self, mock_run_in_thread, tmp_path):
        """Test ModelHTTPError handling."""
        from pydantic_ai.exceptions import ModelHTTPError

        mock_run_in_thread.side_effect = ModelHTTPError(
            status_code=429,
            model_name="test-model",
            body="API rate limit exceeded",
        )

        session = ApiSession(model="test-model")
        result = session.query("test", tmp_path)

        assert "API error" in result.text
        assert result.is_error

    @patch("kodo.knowledge.sessions.run_in_thread", autospec=True)
    def test_query_timeout(self, mock_run_in_thread, tmp_path):
        """Test TimeoutError handling."""
        mock_run_in_thread.side_effect = TimeoutError("Thread timed out")

        session = ApiSession(model="test-model")
        result = session.query("test", tmp_path)

        assert "Agent timed out after 300s" in result.text
        assert result.is_error

    @patch("kodo.knowledge.sessions.run_in_thread", autospec=True)
    def test_query_generic_error(self, mock_run_in_thread, tmp_path):
        """Test generic exception handling."""
        mock_run_in_thread.side_effect = ValueError("Something went wrong")

        session = ApiSession(model="test-model")
        result = session.query("test", tmp_path)

        assert "Error: ValueError: Something went wrong" in result.text
        assert result.is_error

    @patch("kodo.knowledge.sessions.PydanticAgent", autospec=True)
    @patch("kodo.knowledge.sessions.make_fresh_model", autospec=True)
    def test_query_with_tools(self, mock_make_model, mock_agent_class, tmp_path):
        """Test that tools are correctly passed to the agent."""
        # Mock the agent and model
        mock_model = MagicMock()
        mock_make_model.return_value = mock_model

        mock_agent = MagicMock()
        mock_result = MagicMock()
        mock_result.output = "Result with tools"
        mock_usage = MagicMock(input_tokens=10, output_tokens=20, requests=1)
        mock_result.usage.return_value = mock_usage
        mock_agent.run_sync.return_value = mock_result
        mock_agent_class.return_value = mock_agent

        # Create session with tools
        test_tool = MagicMock(spec=Tool)
        session = ApiSession(model="test-model", tools=[test_tool])
        result = session.query("test", tmp_path)

        # Verify agent was created with tools
        mock_agent_class.assert_called_once()
        call_args = mock_agent_class.call_args
        assert call_args[1]["tools"] == [test_tool]

        # Verify result is correct
        assert result.text == "Result with tools"
        assert result.input_tokens == 10
        assert result.output_tokens == 20


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

    def test_read_artifact_tool_added(self):
        """Test that read_artifact tool is added when requested with workspace."""
        from kodo.knowledge.models import Workspace

        workspace = Workspace()
        role = AgentRole(
            name="reader",
            system_prompt="You read artifacts.",
            model_preference="fast",
            tools=["read_artifact"],
        )
        session = make_knowledge_session(role, workspace=workspace)
        assert len(session._tools) == 1
        # Verify it's the read_artifact tool
        assert session._tools[0].name == "read_artifact"

    def test_write_artifact_tool_added(self):
        """Test that write_artifact tool is added when requested with workspace."""
        from kodo.knowledge.models import Workspace

        workspace = Workspace()
        role = AgentRole(
            name="writer",
            system_prompt="You write artifacts.",
            model_preference="fast",
            tools=["write_artifact"],
        )
        session = make_knowledge_session(role, workspace=workspace)
        assert len(session._tools) == 1
        # Verify it's the write_artifact tool
        assert session._tools[0].name == "write_artifact"

    def test_multiple_tools_with_workspace(self):
        """Test that multiple tools can be added including workspace tools."""
        from kodo.knowledge.models import Workspace

        workspace = Workspace()
        role = AgentRole(
            name="multitool",
            system_prompt="You use many tools.",
            model_preference="fast",
            tools=["compute", "read_artifact", "write_artifact"],
        )
        session = make_knowledge_session(role, workspace=workspace)
        # Should have all 3 tools
        assert len(session._tools) == 3
        tool_names = {tool.name for tool in session._tools}
        assert tool_names == {"compute", "read_artifact", "write_artifact"}
