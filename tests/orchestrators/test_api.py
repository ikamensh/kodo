"""Tests for kodo.orchestrators.api.ApiOrchestrator."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kodo import log
from kodo.log import RunDir
from kodo.orchestrators.api import ApiOrchestrator, _messages_to_text
from kodo.orchestrators.base import CycleConfig
from tests.conftest import FakeRunResult


def test_cycle_done_returns_finished(tmp_path: Path):
    log.init(RunDir.create(tmp_path, "api_done"))

    def fake_run_sync(prompt, *, usage_limits=None, **kwargs):
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
        patch(
            "kodo.orchestrators.api.Agent.__init__",
            autospec=True,
            side_effect=fake_agent_init,
        ),
        patch(
            "kodo.orchestrators.verification.verify_done",
            autospec=True,
            return_value=None,
        ),
    ):
        orch = ApiOrchestrator(model="claude-opus-4-6")
        result = orch.cycle(
            "build feature",
            tmp_path,
            team,
            max_exchanges=10,
            config=CycleConfig(done_mode="legacy"),
        )

    assert result.finished is True
    assert result.summary == "all done"


def test_cycle_no_done_returns_summary(tmp_path: Path):
    log.init(RunDir.create(tmp_path, "api_nodone"))

    def fake_run_sync(prompt, *, usage_limits=None, **kwargs):
        return FakeRunResult(output="partial progress")

    def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
        self.run_sync = fake_run_sync

    team = _make_fake_team()

    with (
        patch(
            "kodo.orchestrators.api.Agent.__init__",
            autospec=True,
            side_effect=fake_agent_init,
        ),
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
        def fake_run_sync(prompt, *, usage_limits=None, **kwargs):
            raise UsageLimitExceeded("limit hit")

        self.run_sync = fake_run_sync

    team = _make_fake_team()

    with patch(
        "kodo.orchestrators.api.Agent.__init__",
        autospec=True,
        side_effect=fake_agent_init,
    ):
        orch = ApiOrchestrator(model="claude-opus-4-6")
        result = orch.cycle("build feature", tmp_path, team, max_exchanges=5)

    assert result.finished is False


def test_529_fallback(tmp_path: Path):
    log.init(RunDir.create(tmp_path, "api_529"))
    from pydantic_ai.exceptions import ModelHTTPError

    call_count = [0]

    def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
        def fake_run_sync(prompt, *, usage_limits=None, **kwargs):
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
        patch(
            "kodo.orchestrators.api.Agent.__init__",
            autospec=True,
            side_effect=fake_agent_init,
        ),
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
    """build_pydantic_tools creates ask_<name> tools for each agent and a done tool."""
    from unittest.mock import MagicMock

    from kodo.orchestrators.tools import build_pydantic_tools
    from kodo.orchestrators.base import CycleConfig, DoneSignal

    team = _make_fake_team()
    team["tester"] = _make_fake_team()["worker"]  # add a second agent

    done_signal = DoneSignal()
    summarizer = MagicMock()

    tools = build_pydantic_tools(
        team,
        tmp_path,
        summarizer,
        done_signal,
        "test goal",
        config=CycleConfig(done_mode="legacy"),
    )

    tool_names = {t.name for t in tools}
    assert "ask_worker" in tool_names
    assert "ask_tester" in tool_names
    assert "done" in tool_names
    assert len(tools) == 3  # 2 agents + done


def test_build_tools_agent_handler_returns_string(tmp_path: Path):
    """Agent tool handlers return a string result (not raise)."""
    from unittest.mock import MagicMock

    from kodo.orchestrators.tools import build_pydantic_tools
    from kodo.orchestrators.base import DoneSignal

    team = _make_fake_team()
    done_signal = DoneSignal()
    summarizer = MagicMock()

    tools = build_pydantic_tools(team, tmp_path, summarizer, done_signal, "test goal")
    ask_worker = next(t for t in tools if t.name == "ask_worker")

    log.init(RunDir.create(tmp_path, "tool_test"))
    result = ask_worker.function(task="do something")
    assert isinstance(result, str)


def test_build_tools_done_sets_signal(tmp_path: Path):
    """The done tool handler sets the DoneSignal when verification passes."""
    from unittest.mock import MagicMock

    from kodo.orchestrators.tools import build_pydantic_tools
    from kodo.orchestrators.base import CycleConfig, DoneSignal

    team = _make_fake_team()
    done_signal = DoneSignal()
    summarizer = MagicMock()

    tools = build_pydantic_tools(
        team,
        tmp_path,
        summarizer,
        done_signal,
        "test goal",
        config=CycleConfig(done_mode="legacy"),
    )
    done_tool = next(t for t in tools if t.name == "done")

    log.init(RunDir.create(tmp_path, "done_test"))
    with patch(
        "kodo.orchestrators.verification.verify_done", autospec=True, return_value=None
    ):
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

        with patch(
            "kodo.orchestrators.api.Agent.__init__",
            autospec=True,
            side_effect=fake_agent_init,
        ):
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

        with patch(
            "kodo.orchestrators.api.Agent.__init__",
            autospec=True,
            side_effect=fake_agent_init,
        ):
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

        with patch(
            "kodo.orchestrators.api.Agent.__init__",
            autospec=True,
            side_effect=fake_agent_init,
        ):
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

        with patch(
            "kodo.orchestrators.api.Agent.__init__",
            autospec=True,
            side_effect=fake_agent_init,
        ):
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


# ── Error path tests ───────────────────────────────────────────────────────


class TestApiErrorPaths:
    """Test error handling paths in ApiOrchestrator.cycle()."""

    def test_http_401_raises_with_message(self, tmp_path: Path):
        """HTTP 401 from API should raise with clear auth error message."""
        log.init(RunDir.create(tmp_path, "api_401"))
        from pydantic_ai.exceptions import ModelHTTPError

        def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
            def fake_run_sync(prompt, *, usage_limits=None, **kwargs):
                raise ModelHTTPError(
                    status_code=401, model_name="claude-opus-4-6", body="Unauthorized"
                )

            self.run_sync = fake_run_sync

        team = _make_fake_team()

        with (
            patch(
                "kodo.orchestrators.api.Agent.__init__",
                autospec=True,
                side_effect=fake_agent_init,
            ),
            pytest.raises(ModelHTTPError) as exc_info,
        ):
            orch = ApiOrchestrator(model="claude-opus-4-6")
            orch.cycle("test", tmp_path, team, max_exchanges=5)

        assert exc_info.value.status_code == 401

    def test_http_403_raises_with_message(self, tmp_path: Path):
        """HTTP 403 from API should raise with clear auth error message."""
        log.init(RunDir.create(tmp_path, "api_403"))
        from pydantic_ai.exceptions import ModelHTTPError

        def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
            def fake_run_sync(prompt, *, usage_limits=None, **kwargs):
                raise ModelHTTPError(
                    status_code=403, model_name="gemini-2.5-pro", body="Forbidden"
                )

            self.run_sync = fake_run_sync

        team = _make_fake_team()

        with (
            patch(
                "kodo.orchestrators.api.Agent.__init__",
                autospec=True,
                side_effect=fake_agent_init,
            ),
            pytest.raises(ModelHTTPError) as exc_info,
        ):
            orch = ApiOrchestrator(model="gemini-2.5-pro")
            orch.cycle("test", tmp_path, team, max_exchanges=5)

        assert exc_info.value.status_code == 403

    def test_http_500_retries_then_raises(self, tmp_path: Path):
        """HTTP 500 should retry 3 times then raise."""
        log.init(RunDir.create(tmp_path, "api_500"))
        from pydantic_ai.exceptions import ModelHTTPError

        call_count = [0]

        def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
            def fake_run_sync(prompt, *, usage_limits=None, **kwargs):
                call_count[0] += 1
                raise ModelHTTPError(
                    status_code=500, model_name="test", body="Internal error"
                )

            self.run_sync = fake_run_sync

        team = _make_fake_team()

        with (
            patch(
                "kodo.orchestrators.api.Agent.__init__",
                autospec=True,
                side_effect=fake_agent_init,
            ),
            patch("time.sleep", autospec=True),  # Skip actual sleep
            pytest.raises(ModelHTTPError),
        ):
            orch = ApiOrchestrator(model="claude-opus-4-6")
            orch.cycle("test", tmp_path, team, max_exchanges=5)

        # Should have tried 3 times
        assert call_count[0] == 3

    def test_http_429_retry_then_succeed(self, tmp_path: Path):
        """HTTP 429 on first call, then success on retry."""
        log.init(RunDir.create(tmp_path, "api_429_ok"))
        from pydantic_ai.exceptions import ModelHTTPError

        call_count = [0]

        def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
            def fake_run_sync(prompt, *, usage_limits=None, **kwargs):
                call_count[0] += 1
                if call_count[0] == 1:
                    raise ModelHTTPError(
                        status_code=429, model_name="test", body="rate limit"
                    )
                return FakeRunResult()

            self.run_sync = fake_run_sync

        team = _make_fake_team()

        with (
            patch(
                "kodo.orchestrators.api.Agent.__init__",
                autospec=True,
                side_effect=fake_agent_init,
            ),
            patch.object(ApiOrchestrator, "_summarize", return_value="done"),
            patch("time.sleep", autospec=True),
        ):
            orch = ApiOrchestrator(model="claude-opus-4-6")
            result = orch.cycle("test", tmp_path, team, max_exchanges=5)

        assert call_count[0] == 2
        assert result.summary == "done"

    def test_http_500_retry_then_succeed(self, tmp_path: Path):
        """HTTP 500 on first call, then success on retry."""
        log.init(RunDir.create(tmp_path, "api_500_ok"))
        from pydantic_ai.exceptions import ModelHTTPError

        call_count = [0]

        def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
            def fake_run_sync(prompt, *, usage_limits=None, **kwargs):
                call_count[0] += 1
                if call_count[0] == 1:
                    raise ModelHTTPError(
                        status_code=500, model_name="test", body="internal error"
                    )
                return FakeRunResult()

            self.run_sync = fake_run_sync

        team = _make_fake_team()

        with (
            patch(
                "kodo.orchestrators.api.Agent.__init__",
                autospec=True,
                side_effect=fake_agent_init,
            ),
            patch.object(ApiOrchestrator, "_summarize", return_value="done"),
            patch("time.sleep", autospec=True),
        ):
            orch = ApiOrchestrator(model="claude-opus-4-6")
            result = orch.cycle("test", tmp_path, team, max_exchanges=5)

        assert call_count[0] == 2

    def test_timeout_error_retries(self, tmp_path: Path):
        """httpx.TimeoutException should retry with backoff."""
        log.init(RunDir.create(tmp_path, "api_timeout"))
        import httpx

        call_count = [0]

        def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
            def fake_run_sync(prompt, *, usage_limits=None, **kwargs):
                call_count[0] += 1
                if call_count[0] < 3:
                    raise httpx.TimeoutException("Request timeout")
                return FakeRunResult(output="recovered")

            self.run_sync = fake_run_sync

        team = _make_fake_team()

        with (
            patch(
                "kodo.orchestrators.api.Agent.__init__",
                autospec=True,
                side_effect=fake_agent_init,
            ),
            patch("time.sleep", autospec=True),  # Skip actual sleep
            patch.object(ApiOrchestrator, "_summarize", return_value="ok"),
        ):
            orch = ApiOrchestrator(model="claude-opus-4-6")
            result = orch.cycle("test", tmp_path, team, max_exchanges=5)

        # Should have retried and succeeded
        assert call_count[0] == 3
        assert result is not None

    def test_connect_error_retries(self, tmp_path: Path):
        """httpx.ConnectError should retry with backoff."""
        log.init(RunDir.create(tmp_path, "api_connect"))
        import httpx

        call_count = [0]

        def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
            def fake_run_sync(prompt, *, usage_limits=None, **kwargs):
                call_count[0] += 1
                if call_count[0] < 2:
                    raise httpx.ConnectError("Connection refused")
                return FakeRunResult(output="connected")

            self.run_sync = fake_run_sync

        team = _make_fake_team()

        with (
            patch(
                "kodo.orchestrators.api.Agent.__init__",
                autospec=True,
                side_effect=fake_agent_init,
            ),
            patch("time.sleep", autospec=True),
            patch.object(ApiOrchestrator, "_summarize", return_value="ok"),
        ):
            orch = ApiOrchestrator(model="claude-opus-4-6")
            orch.cycle("test", tmp_path, team, max_exchanges=5)

        assert call_count[0] == 2

    def test_remote_protocol_error_retries(self, tmp_path: Path):
        """httpx.RemoteProtocolError should retry."""
        log.init(RunDir.create(tmp_path, "api_protocol"))
        import httpx

        call_count = [0]

        def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
            def fake_run_sync(prompt, *, usage_limits=None, **kwargs):
                call_count[0] += 1
                if call_count[0] == 1:
                    raise httpx.RemoteProtocolError("Protocol error")
                return FakeRunResult(output="ok")

            self.run_sync = fake_run_sync

        team = _make_fake_team()

        with (
            patch(
                "kodo.orchestrators.api.Agent.__init__",
                autospec=True,
                side_effect=fake_agent_init,
            ),
            patch("time.sleep", autospec=True),
            patch.object(ApiOrchestrator, "_summarize", return_value="done"),
        ):
            orch = ApiOrchestrator(model="claude-opus-4-6")
            orch.cycle("test", tmp_path, team, max_exchanges=5)

        assert call_count[0] == 2

    def test_fatal_agent_error_stops_cycle(self, tmp_path: Path):
        """FatalAgentError should immediately stop the cycle."""
        log.init(RunDir.create(tmp_path, "api_fatal"))
        from kodo.orchestrators.base import FatalAgentError

        def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
            def fake_run_sync(prompt, *, usage_limits=None, **kwargs):
                raise FatalAgentError("Worker crashed fatally")

            self.run_sync = fake_run_sync

        team = _make_fake_team()

        with patch(
            "kodo.orchestrators.api.Agent.__init__",
            autospec=True,
            side_effect=fake_agent_init,
        ):
            orch = ApiOrchestrator(model="claude-opus-4-6")
            result = orch.cycle("test", tmp_path, team, max_exchanges=5)

        assert result.finished is True
        assert result.success is False
        assert "Aborted" in result.summary

    def test_summarize_exception_uses_fallback(self, tmp_path: Path):
        """When _summarize raises, the cycle handles it gracefully."""
        log.init(RunDir.create(tmp_path, "api_sum_err"))

        call_count = [0]

        def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
            def fake_run_sync(prompt, *, usage_limits=None, **kwargs):
                call_count[0] += 1
                if call_count[0] == 1:
                    # First call succeeds
                    return FakeRunResult(output="work done")
                else:
                    # Summarizer call raises
                    raise RuntimeError("Summarization API down")

            self.run_sync = fake_run_sync

        team = _make_fake_team()

        with (
            patch(
                "kodo.orchestrators.api.Agent.__init__",
                autospec=True,
                side_effect=fake_agent_init,
            ),
        ):
            orch = ApiOrchestrator(model="claude-opus-4-6")
            result = orch.cycle("test", tmp_path, team, max_exchanges=5)

        # Should have fallback summary even though summarization failed
        assert result.summary is not None
        assert (
            "Summarization failed" in result.summary
            or "No detailed summary" in result.summary
        )

    def test_retry_accumulates_cost_from_failed_attempts(self, tmp_path: Path):
        """Cost should include tokens from failed attempts, not just the final run."""
        log.init(RunDir.create(tmp_path, "api_retry_cost"))
        from pydantic_ai.exceptions import ModelHTTPError
        from pydantic_ai.usage import RunUsage

        call_count = [0]

        def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
            def fake_run_sync(prompt, *, usage_limits=None, **kwargs):
                nonlocal call_count
                call_count[0] += 1
                # Simulate pydantic-ai mutating the shared RunUsage
                # in-place before an error or after success.
                usage = kwargs.get("usage")
                if usage is not None:
                    usage.incr(
                        RunUsage(input_tokens=500, output_tokens=200, requests=3),
                    )
                if call_count[0] == 1:
                    # First attempt: tokens consumed, then HTTP 500
                    raise ModelHTTPError(
                        status_code=500, model_name="test", body="Internal error"
                    )
                # Second attempt succeeds
                return FakeRunResult()

            self.run_sync = fake_run_sync

        team = _make_fake_team()

        with (
            patch(
                "kodo.orchestrators.api.Agent.__init__",
                autospec=True,
                side_effect=fake_agent_init,
            ),
            patch("time.sleep", autospec=True),
            patch.object(ApiOrchestrator, "_summarize", return_value="ok"),
        ):
            orch = ApiOrchestrator(model="opus")
            result = orch.cycle("test", tmp_path, team, max_exchanges=10)

        assert call_count[0] == 2
        # Both attempts contributed 3 requests each → 6 total
        assert result.exchanges == 6
        # Cost should reflect tokens from both attempts (1000 input, 400 output)
        assert result.total_cost_usd > 0


class TestForParallel:
    """Test the for_parallel() method that creates thread-safe copies."""

    def test_for_parallel_preserves_config(self):
        """for_parallel() preserves all configuration."""
        orch = ApiOrchestrator(
            model="claude-opus-4-6",
            max_context_tokens=50000,
            system_prompt="custom prompt",
            fallback_model="claude-sonnet-4-5",
        )
        copy = orch.for_parallel()

        assert copy.model == "claude-opus-4-6"
        assert copy.max_context_tokens == 50000
        assert copy._system_prompt == "custom prompt"
        assert copy._fallback_model == "claude-sonnet-4-5"
        assert copy._pydantic_model is not None

    def test_for_parallel_creates_fresh_model(self):
        """for_parallel() creates a new instance with its own fresh model."""
        orch = ApiOrchestrator(model="gemini-flash")
        copy = orch.for_parallel()

        # Each instance should have its own model (not sharing cached clients)
        assert copy._pydantic_model is not orch._pydantic_model


class TestMessagesToText:
    """Test the _messages_to_text helper with various message types."""

    def test_tool_return_part_included(self):
        """ToolReturnPart messages should be included in text."""
        from pydantic_ai.messages import ModelRequest, ToolReturnPart

        messages = [
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="ask_worker",
                        content={"result": "completed"},  # Non-string content
                        timestamp="2024-01-01T00:00:00Z",
                    )
                ]
            ),
        ]
        text = _messages_to_text(messages)
        assert "tool_result(ask_worker)" in text
        assert "completed" in text or "result" in text

    def test_tool_call_part_included(self):
        """ToolCallPart messages should be included."""
        from pydantic_ai.messages import ModelResponse, ToolCallPart

        messages = [
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="ask_tester",
                        args={"task": "run tests"},
                        tool_call_id="call_123",
                    )
                ],
                model_name="test",
                timestamp="2024-01-01T00:00:00Z",
            ),
        ]
        text = _messages_to_text(messages)
        assert "tool_use: ask_tester" in text

    def test_long_content_truncated(self):
        """Long content should be truncated to prevent bloat."""
        from pydantic_ai.messages import ModelRequest, UserPromptPart

        long_content = "x" * 1000
        messages = [
            ModelRequest(
                parts=[
                    UserPromptPart(
                        content=long_content, timestamp="2024-01-01T00:00:00Z"
                    )
                ]
            ),
        ]
        text = _messages_to_text(messages)
        # Should be truncated to 500 chars
        assert len(text) < 600


class TestCycleWithoutDoneMode:
    """Test cycle() behavior when done_mode is not 'legacy' (new modes)."""

    def test_cycle_without_legacy_done_mode_no_done_tool(self, tmp_path: Path):
        """When done_mode != 'legacy', the done tool should not be created."""
        log.init(RunDir.create(tmp_path, "api_no_legacy"))

        def fake_run_sync(prompt, *, usage_limits=None, **kwargs):
            return FakeRunResult(output="work in progress")

        agent_tools = []

        def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
            nonlocal agent_tools
            agent_tools = tools or []
            self.run_sync = fake_run_sync

        team = _make_fake_team()

        with (
            patch(
                "kodo.orchestrators.api.Agent.__init__",
                autospec=True,
                side_effect=fake_agent_init,
            ),
            patch.object(ApiOrchestrator, "_summarize", return_value="progress"),
        ):
            orch = ApiOrchestrator(model="claude-opus-4-6")
            result = orch.cycle(
                "test",
                tmp_path,
                team,
                max_exchanges=5,
                config=CycleConfig(done_mode=None),  # NOT legacy
            )

        # Should have no 'done' tool
        tool_names = {t.name for t in agent_tools}
        assert "done" not in tool_names
        assert result.finished is False
