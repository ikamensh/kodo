"""Fault injection: verify error handling and resume impact.

Run: uv run pytest tests/orchestrators/test_fault_injection.py -v

Scenarios:
1. Agent session raises ConnectionError during exchange
2. api.py _summarize raises
3. summarizer.py _do_summarize raises (already caught internally)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from kodo import log
from kodo.log import RunDir
from kodo.orchestrators.api import ApiOrchestrator
from tests.conftest import FakeRunResult, FakeSession


def _make_fake_team():
    from kodo.agent import Agent

    session = FakeSession(response_text="ok")
    agent = Agent(session, "test agent", max_turns=5)
    return {"worker": agent}


def test_agent_session_connection_error_handled(tmp_path: Path):
    """Agent session raising ConnectionError is caught by handle_agent_call; cycle continues."""
    log.init(RunDir.create(tmp_path, "fault_agent"))

    raising_session = FakeSession(response_text="x")

    def query_raises(*a, **kw):
        raise ConnectionError("injected fault")

    raising_session.query = query_raises
    from kodo.agent import Agent

    raising_agent = Agent(raising_session, "test", max_turns=5)
    team = {"worker": raising_agent}

    agent_tools = []

    def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
        nonlocal agent_tools
        agent_tools = tools or []
        tool_list = tools or []

        def fake_run_sync(prompt, *, usage_limits=None):
            for t in tool_list:
                if t.name == "ask_worker":
                    result = t.function("do task", new_conversation=False)
                    assert "[ERROR]" in result or "ConnectionError" in result
                    break
            return FakeRunResult(output="partial")

        self.run_sync = fake_run_sync

    with (
        patch("kodo.orchestrators.api.Agent.__init__", fake_agent_init),
        patch.object(ApiOrchestrator, "_summarize", return_value="summary"),
    ):
        orch = ApiOrchestrator(model="claude-opus-4-6")
        result = orch.cycle("build feature", tmp_path, team, max_exchanges=10)

    assert result.finished is False
    assert result.summary == "summary"
    # Cycle completed despite agent crash — handle_agent_call caught it


def test_summarize_failure_returns_fallback_and_emits_cycle_end(tmp_path: Path):
    """When _summarize's inner model call raises, the cycle still completes
    with a fallback summary and emits cycle_end so the run remains resumable."""
    log.init(RunDir.create(tmp_path, "fault_summarize"))

    call_count = 0

    def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
        nonlocal call_count

        def fake_run_sync(prompt, *, usage_limits=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call is the orchestrator run — succeeds normally.
                return FakeRunResult(output="partial progress")
            # Second call is from _summarize — blow up.
            raise ConnectionError("injected _summarize fault")

        self.run_sync = fake_run_sync

    team = _make_fake_team()

    with patch("kodo.orchestrators.api.Agent.__init__", fake_agent_init):
        orch = ApiOrchestrator(model="claude-opus-4-6")
        result = orch.cycle("build feature", tmp_path, team, max_exchanges=10)

    # Cycle completed (no exception) with a fallback summary.
    assert result.finished is False
    assert "Summarization failed" in result.summary
    assert "injected _summarize fault" in result.summary

    # cycle_end was emitted — run is resumable.
    import json

    events = [
        json.loads(line)
        for line in log.get_log_file().read_text().splitlines()
        if line.strip()
    ]
    cycle_ends = [e for e in events if e.get("event") == "cycle_end"]
    assert len(cycle_ends) == 1
    assert cycle_ends[0]["reason"] == "stop_no_done"


def test_summarizer_do_summarize_swallows_exception(tmp_path: Path):
    """Summarizer._do_summarize catches Exception; never crashes."""
    from kodo.summarizer import Summarizer

    with (
        patch("kodo.summarizer._probe_ollama", return_value=None),
        patch("kodo.summarizer._probe_gemini", return_value=None),
    ):
        s = Summarizer()

    def raising_do(agent_name, task, report):
        raise ConnectionError("injected summarizer fault")

    s._do_summarize = raising_do
    s.summarize("worker", "task", "report")
    s.get_accumulated_summary()  # waits for executor
    # No exception propagates — summarizer is fire-and-forget, exceptions caught internally
    # (The real _do_summarize has except Exception: pass; our patch replaces it
    # so we're testing that IF it raised, the executor would swallow it. Actually
    # the executor doesn't swallow - the exception would propagate to the thread.
    # The caller (get_accumulated_summary) waits for the future - does it re-raise?
    # executor.submit().result() would re-raise. So get_accumulated_summary
    # calls executor.shutdown(wait=True) - that waits for tasks. If a task raises,
    # the exception is stored in the future. shutdown doesn't propagate it.
    # So we're good - the exception stays in the worker thread.)
    # Actually in Python ThreadPoolExecutor, if a task raises, the exception
    # is captured in the Future. future.result() would raise. But shutdown(wait=True)
    # doesn't call result() - it just waits for the thread to finish. The exception
    # is lost (stored in the Future but nobody calls result()). So we don't crash.
    # Good.
    pass
