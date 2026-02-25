"""Tests for kodo.orchestrators.api.ApiOrchestrator."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from kodo import log
from kodo.log import RunDir
from kodo.orchestrators.api import ApiOrchestrator, _messages_to_text
from tests.conftest import FakeRunResult


def test_cycle_done_returns_finished(tmp_path: Path):
    log.init(RunDir.create(tmp_path, "api_done"))

    def fake_run_sync(prompt, *, usage_limits=None):
        # Find the done tool among the agent's tools and call it
        for tool in agent_tools:
            if tool.name == "done":
                tool.function(summary="all done", success=True)
                break
        return FakeRunResult()

    agent_tools = []

    def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
        nonlocal agent_tools
        agent_tools = tools or []
        self.run_sync = fake_run_sync

    team = _make_fake_team()

    with (
        patch("kodo.orchestrators.api.Agent.__init__", fake_agent_init),
        patch("kodo.orchestrators.base.verify_done", return_value=None),
    ):
        orch = ApiOrchestrator(model="claude-opus-4-6")
        result = orch.cycle("build feature", tmp_path, team, max_exchanges=10)

    assert result.finished is True
    assert result.summary == "all done"


def test_cycle_no_done_returns_summary(tmp_path: Path):
    log.init(RunDir.create(tmp_path, "api_nodone"))

    def fake_run_sync(prompt, *, usage_limits=None):
        return FakeRunResult(output="partial progress")

    def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
        self.run_sync = fake_run_sync

    team = _make_fake_team()

    with (
        patch("kodo.orchestrators.api.Agent.__init__", fake_agent_init),
        patch.object(ApiOrchestrator, "_summarize", return_value="summary of work"),
    ):
        orch = ApiOrchestrator(model="claude-opus-4-6")
        result = orch.cycle("build feature", tmp_path, team, max_exchanges=10)

    assert result.finished is False
    assert result.summary == "summary of work"


def test_usage_limit_exceeded(tmp_path: Path):
    log.init(RunDir.create(tmp_path, "api_limit"))
    from pydantic_ai.exceptions import UsageLimitExceeded

    def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
        def fake_run_sync(prompt, *, usage_limits=None):
            raise UsageLimitExceeded("limit hit")

        self.run_sync = fake_run_sync

    team = _make_fake_team()

    with patch("kodo.orchestrators.api.Agent.__init__", fake_agent_init):
        orch = ApiOrchestrator(model="claude-opus-4-6")
        result = orch.cycle("build feature", tmp_path, team, max_exchanges=5)

    assert result.finished is False


def test_529_fallback(tmp_path: Path):
    log.init(RunDir.create(tmp_path, "api_529"))
    from pydantic_ai.exceptions import ModelHTTPError

    call_count = [0]

    def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
        def fake_run_sync(prompt, *, usage_limits=None):
            nonlocal call_count
            call_count[0] += 1
            if call_count[0] == 1:
                raise ModelHTTPError(
                    status_code=529, model_name="test", body="overloaded"
                )
            return FakeRunResult()

        self.run_sync = fake_run_sync

    team = _make_fake_team()

    with (
        patch("kodo.orchestrators.api.Agent.__init__", fake_agent_init),
        patch.object(ApiOrchestrator, "_summarize", return_value="done"),
    ):
        orch = ApiOrchestrator(
            model="claude-opus-4-6",
            fallback_model="claude-sonnet-4-5-20250929",
        )
        orch.cycle("build feature", tmp_path, team, max_exchanges=10)

    # Should have retried with fallback and succeeded
    assert call_count[0] == 2


def test_build_tools_creates_agent_and_done_tools(tmp_path: Path):
    """_build_tools creates ask_<name> tools for each agent and a done tool."""
    from unittest.mock import MagicMock

    from kodo.orchestrators.api import _build_tools
    from kodo.orchestrators.base import DoneSignal

    team = _make_fake_team()
    team["tester"] = _make_fake_team()["worker"]  # add a second agent

    done_signal = DoneSignal()
    summarizer = MagicMock()

    tools = _build_tools(team, tmp_path, summarizer, done_signal, "test goal")

    tool_names = {t.name for t in tools}
    assert "ask_worker" in tool_names
    assert "ask_tester" in tool_names
    assert "done" in tool_names
    assert len(tools) == 3  # 2 agents + done


def test_build_tools_agent_handler_returns_string(tmp_path: Path):
    """Agent tool handlers return a string result (not raise)."""
    from unittest.mock import MagicMock

    from kodo.orchestrators.api import _build_tools
    from kodo.orchestrators.base import DoneSignal

    team = _make_fake_team()
    done_signal = DoneSignal()
    summarizer = MagicMock()

    tools = _build_tools(team, tmp_path, summarizer, done_signal, "test goal")
    ask_worker = next(t for t in tools if t.name == "ask_worker")

    log.init(RunDir.create(tmp_path, "tool_test"))
    result = ask_worker.function(task="do something")
    assert isinstance(result, str)


def test_build_tools_done_sets_signal(tmp_path: Path):
    """The done tool handler sets the DoneSignal when verification passes."""
    from unittest.mock import MagicMock

    from kodo.orchestrators.api import _build_tools
    from kodo.orchestrators.base import DoneSignal

    team = _make_fake_team()
    done_signal = DoneSignal()
    summarizer = MagicMock()

    tools = _build_tools(team, tmp_path, summarizer, done_signal, "test goal")
    done_tool = next(t for t in tools if t.name == "done")

    log.init(RunDir.create(tmp_path, "done_test"))
    with patch("kodo.orchestrators.base.verify_done", return_value=None):
        done_tool.function(summary="all done", success=True)

    assert done_signal.called is True
    assert done_signal.success is True
    assert done_signal.summary == "all done"


def test_messages_to_text():
    """Unit test the _messages_to_text helper with mock message objects."""
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        TextPart,
        UserPromptPart,
    )

    messages = [
        ModelRequest(
            parts=[UserPromptPart(content="hello", timestamp="2024-01-01T00:00:00Z")]
        ),
        ModelResponse(
            parts=[TextPart(part_kind="text", content="hi there")],
            model_name="test",
            timestamp="2024-01-01T00:00:00Z",
        ),
    ]
    text = _messages_to_text(messages)
    assert "[user] hello" in text
    assert "[assistant] hi there" in text


class TestSummarizeEmptyOutput:
    """_summarize should never return None or empty string."""

    def test_empty_output_returns_fallback(self, tmp_path: Path):
        """When summarizer agent returns empty string, fall back gracefully."""
        log.init(RunDir.create(tmp_path, "sum_empty"))

        def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
            self.run_sync = lambda prompt, **kw: FakeRunResult(output="")

        with patch("kodo.orchestrators.api.Agent.__init__", fake_agent_init):
            orch = ApiOrchestrator(model="claude-opus-4-6")
            result = orch._summarize([])

        assert result is not None
        assert isinstance(result, str)
        assert len(result) > 0
        assert "empty" in result.lower()

    def test_whitespace_output_returns_fallback(self, tmp_path: Path):
        """When summarizer agent returns only whitespace, fall back gracefully."""
        log.init(RunDir.create(tmp_path, "sum_ws"))

        def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
            self.run_sync = lambda prompt, **kw: FakeRunResult(output="   \n\t  ")

        with patch("kodo.orchestrators.api.Agent.__init__", fake_agent_init):
            orch = ApiOrchestrator(model="claude-opus-4-6")
            result = orch._summarize([])

        assert result is not None
        assert isinstance(result, str)
        assert len(result) > 0
        assert "empty" in result.lower()

    def test_none_output_returns_fallback(self, tmp_path: Path):
        """When summarizer agent returns None output, fall back gracefully."""
        log.init(RunDir.create(tmp_path, "sum_none"))

        def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
            self.run_sync = lambda prompt, **kw: FakeRunResult(output=None)

        with patch("kodo.orchestrators.api.Agent.__init__", fake_agent_init):
            orch = ApiOrchestrator(model="claude-opus-4-6")
            result = orch._summarize([])

        assert result is not None
        assert isinstance(result, str)
        assert len(result) > 0

    def test_valid_output_returned_as_is(self, tmp_path: Path):
        """When summarizer returns a real summary, use it directly."""
        log.init(RunDir.create(tmp_path, "sum_ok"))

        def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
            self.run_sync = lambda prompt, **kw: FakeRunResult(
                output="Completed task X, pending task Y."
            )

        with patch("kodo.orchestrators.api.Agent.__init__", fake_agent_init):
            orch = ApiOrchestrator(model="claude-opus-4-6")
            result = orch._summarize([])

        assert result == "Completed task X, pending task Y."


# ── close() tests ───────────────────────────────────────────────────────


def test_close_releases_http_client():
    """close() should aclose the _http_client if present, and no-op otherwise."""
    import asyncio
    from unittest.mock import AsyncMock

    orch = ApiOrchestrator(model="claude-opus-4-6")

    # No client — close is a no-op
    loop = asyncio.new_event_loop()
    loop.run_until_complete(orch.close())
    loop.close()
    assert orch._http_client is None

    # With a client — should call aclose and clear
    mock_client = AsyncMock()
    orch._http_client = mock_client

    loop = asyncio.new_event_loop()
    loop.run_until_complete(orch.close())
    loop.close()

    mock_client.aclose.assert_awaited_once()
    assert orch._http_client is None

    # Idempotent — second close is a no-op
    loop = asyncio.new_event_loop()
    loop.run_until_complete(orch.close())
    loop.close()
    # aclose still only called once
    mock_client.aclose.assert_awaited_once()


# ── shared helpers ───────────────────────────────────────────────────────


def _make_fake_team():
    """Create a minimal fake TeamConfig for orchestrator tests."""
    from kodo.agent import Agent
    from tests.conftest import FakeSession

    session = FakeSession(response_text="ok")
    agent = Agent(session, "test agent", max_turns=5)
    return {"worker": agent}
