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

        def fake_run_sync(prompt, *, usage_limits=None, **kwargs):
            for t in tool_list:
                if t.name == "ask_worker":
                    result = t.function("do task", new_conversation=False)
                    assert "[ERROR]" in result or "ConnectionError" in result
                    break
            return FakeRunResult(output="partial")

        self.run_sync = fake_run_sync

    with (
        patch("kodo.orchestrators.api.Agent.__init__", autospec=True, side_effect=fake_agent_init),
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

        def fake_run_sync(prompt, *, usage_limits=None, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call is the orchestrator run — succeeds normally.
                return FakeRunResult(output="partial progress")
            # Second call is from _summarize — blow up.
            raise ConnectionError("injected _summarize fault")

        self.run_sync = fake_run_sync

    team = _make_fake_team()

    with patch("kodo.orchestrators.api.Agent.__init__", autospec=True, side_effect=fake_agent_init):
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


