"""Tests for Agent and AgentResult."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from kodo.agent import Agent, AgentResult
from kodo.sessions.base import QueryResult
from tests.conftest import FakeSession, make_agent


def test_agent_new_conversation_clears_stats(tmp_project: Path) -> None:
    agent = make_agent("ok")
    agent.run("task1", tmp_project, agent_name="test")
    assert agent.session.stats.queries == 1
    agent.run("task2", tmp_project, new_conversation=True, agent_name="test")
    # Stats should reflect only post-reset queries
    assert agent.session.stats.queries == 1


def test_agent_context_reset_flag_fresh_session(tmp_project: Path) -> None:
    """new_conversation on a fresh session should NOT flag context_reset."""
    agent = make_agent("ok")
    result = agent.run("task", tmp_project, new_conversation=True, agent_name="test")
    assert result.context_reset is False
    assert result.context_reset_reason == ""


def test_agent_context_reset_flag_after_prior_query(tmp_project: Path) -> None:
    """new_conversation after a prior query SHOULD flag context_reset."""
    agent = make_agent("ok")
    agent.run("task1", tmp_project, agent_name="test")
    result = agent.run("task2", tmp_project, new_conversation=True, agent_name="test")
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


@pytest.mark.slow
def test_agent_timeout_does_not_hang_on_stuck_session(tmp_project: Path) -> None:
    """When terminate() doesn't actually stop the worker thread,
    run() must still return promptly instead of hanging at pool.shutdown()."""
    import threading

    class _HangingSession(FakeSession):
        """Session whose query blocks until an event is set, ignoring terminate."""

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._unblock = threading.Event()

        def query(self, prompt, project_dir, *, max_turns):
            # Block until explicitly unblocked (simulates a stuck session)
            self._unblock.wait(timeout=5)
            return super().query(prompt, project_dir, max_turns=max_turns)

        def terminate(self) -> None:
            # Intentionally does NOT unblock — simulates a stuck terminate
            pass

    session = _HangingSession(response_text="never seen")
    agent = Agent(session, "hanging agent", max_turns=5, timeout_s=0.05)

    start = time.monotonic()
    result = agent.run("do something", tmp_project, agent_name="test")
    elapsed = time.monotonic() - start

    assert result.is_error is True
    assert "timed out" in result.text.lower()
    # The key assertion: run() returns quickly even though the worker
    # thread is still blocked.  Without shutdown(wait=False), this would
    # hang for ~5 seconds (the _unblock.wait timeout).
    assert elapsed < 1.0, f"run() took {elapsed:.1f}s — likely hung on pool.shutdown()"

    # Clean up: unblock the worker thread so it can exit
    session._unblock.set()


def test_agent_context_manager(tmp_project: Path) -> None:
    """Agent can be used as a context manager, calling close() on exit."""
    call_order: list[str] = []

    class _TrackedSession(FakeSession):
        def terminate(self) -> None:
            call_order.append("terminate")

    session = _TrackedSession(response_text="ok")
    session.close_called = False

    def _close():
        session.close_called = True
        call_order.append("close")

    session.close = _close

    with Agent(session, "worker", max_turns=5) as agent:
        result = agent.run("task", tmp_project, agent_name="test")
        assert result.text == "ok"

    # close() should have been called, which calls terminate() then close()
    assert session.close_called is True
    assert "terminate" in call_order
    assert "close" in call_order


def test_agent_close_calls_terminate_then_close(tmp_project: Path) -> None:
    """close() calls terminate() before close() on the session."""
    call_order: list[str] = []

    class _TrackedSession(FakeSession):
        def terminate(self) -> None:
            call_order.append("terminate")

    session = _TrackedSession(response_text="ok")
    session.close = lambda: call_order.append("close")

    agent = Agent(session, "worker", max_turns=5)
    agent.close()

    assert call_order == ["terminate", "close"]


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


# ── Error propagation (relocated from test_error_paths.py) ───────────────


def test_error_session_produces_error_result(tmp_project: Path) -> None:
    """Agent.run() propagates session errors in AgentResult without crashing."""
    from kodo import log
    from kodo.log import RunDir

    log.init(RunDir.create(tmp_project, "err_agent"))
    session = FakeSession(response_text="[ERROR] connection refused", is_error=True)
    agent = Agent(session, "test-worker", max_turns=5)
    result = agent.run("do something", tmp_project, agent_name="worker")
    assert result.is_error is True
    assert "ERROR" in result.text
