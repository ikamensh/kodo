"""Tests for Agent and AgentResult."""

from __future__ import annotations

import time
from pathlib import Path

from kodo.agent import Agent, AgentResult
from kodo.sessions.base import QueryResult
from tests.conftest import FakeSession, make_agent


def test_agent_run_returns_result(tmp_project: Path) -> None:
    agent = make_agent("hello world")
    result = agent.run("do something", tmp_project, agent_name="test")
    assert result.text == "hello world"
    assert not result.is_error
    assert result.elapsed_s > 0


def test_agent_new_conversation_clears_stats(tmp_project: Path) -> None:
    agent = make_agent("ok")
    agent.run("task1", tmp_project, agent_name="test")
    assert agent.session.stats.queries == 1
    agent.run("task2", tmp_project, new_conversation=True, agent_name="test")
    # Stats should reflect only post-reset queries
    assert agent.session.stats.queries == 1


def test_agent_context_reset_flag(tmp_project: Path) -> None:
    agent = make_agent("ok")
    result = agent.run("task", tmp_project, new_conversation=True, agent_name="test")
    assert result.context_reset is True
    assert "new conversation" in result.context_reset_reason


def test_agent_no_reset_by_default(tmp_project: Path) -> None:
    agent = make_agent("ok")
    result = agent.run("task", tmp_project, agent_name="test")
    assert result.context_reset is False
    assert result.context_reset_reason == ""


def test_agent_error_propagated(tmp_project: Path) -> None:
    agent = make_agent("something failed", is_error=True)
    result = agent.run("task", tmp_project, agent_name="test")
    assert result.is_error is True


class _SlowSession(FakeSession):
    """Session that sleeps for a configurable duration."""

    def __init__(self, delay: float, **kwargs):
        super().__init__(**kwargs)
        self._delay = delay

    def query(self, prompt, project_dir, *, max_turns):
        time.sleep(self._delay)
        return super().query(prompt, project_dir, max_turns=max_turns)


def test_agent_timeout_returns_error(tmp_project: Path) -> None:
    session = _SlowSession(delay=0.15, response_text="too slow")
    agent = Agent(session, "slow agent", max_turns=5, timeout_s=0.01)
    result = agent.run("do something", tmp_project, agent_name="test")
    assert result.is_error is True
    assert "timed out" in result.text.lower()


def test_agent_timeout_calls_terminate(tmp_project: Path) -> None:
    """On timeout, Agent.run() should call terminate() before reset()."""
    call_order: list[str] = []

    class _TrackedSlowSession(_SlowSession):
        def terminate(self) -> None:
            call_order.append("terminate")

        def reset(self) -> None:
            call_order.append("reset")
            super().reset()

    session = _TrackedSlowSession(delay=0.15, response_text="too slow")
    agent = Agent(session, "slow agent", max_turns=5, timeout_s=0.01)
    agent.run("do something", tmp_project, agent_name="test")
    assert call_order == ["terminate", "reset"]


def test_agent_no_timeout_when_fast(tmp_project: Path) -> None:
    session = _SlowSession(delay=0.0, response_text="fast")
    agent = Agent(session, "fast agent", max_turns=5, timeout_s=5.0)
    result = agent.run("do something", tmp_project, agent_name="test")
    assert not result.is_error
    assert result.text == "fast"


def test_agent_close_calls_session_close(tmp_project: Path) -> None:
    agent = make_agent("ok")
    agent.session.close_called = False

    def _close():
        agent.session.close_called = True

    agent.session.close = _close
    agent.close()
    assert agent.session.close_called is True


def test_agent_close_no_error_without_close_method(tmp_project: Path) -> None:
    agent = make_agent("ok")
    # FakeSession has no close() — should not raise
    agent.close()


class TestAgentResult:
    def test_format_report_with_context_reset(self) -> None:
        qr = QueryResult(text="result", elapsed_s=1.0)
        ar = AgentResult(
            query=qr, context_reset=True, context_reset_reason="too many tokens"
        )
        report = ar.format_report()
        assert "[Context was reset: too many tokens]" in report

    def test_format_report_empty_text(self) -> None:
        qr = QueryResult(text="", elapsed_s=1.0)
        ar = AgentResult(query=qr)
        report = ar.format_report()
        assert "(no output)" in report
