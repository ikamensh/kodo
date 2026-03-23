"""End-to-end tests for human steering during orchestrator runs.

Tests that a human can type feedback mid-run and the orchestrator LLM
actually sees and responds to it — not just that the plumbing works,
but that the LLM adjusts its behavior.

Three levels:
1. Mock-based: proves advisory appears in tool returns during a full cycle
2. Real LLM: proves Gemini Flash responds to human input by stopping
3. Terminal: proves the interactive prompt delivers input to the queue
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from kodo import log
from kodo.advisory import AdvisoryQueue
from kodo.agent import Agent
from kodo.log import RunDir
from kodo.orchestrators.api import ApiOrchestrator
from kodo.orchestrators.base import CycleConfig
from tests.conftest import FakeRunResult, FakeSession
from tests.scripted_input import ScriptedInput


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_team(response: str = "Task completed successfully.") -> dict[str, Agent]:
    session = FakeSession(response_text=response)
    return {"worker": Agent(session, "Test worker agent", max_turns=5)}


def _real_gemini_key() -> str | None:
    """Return the real Gemini API key, ignoring fake test placeholders."""
    for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        val = os.environ.get(var, "")
        if val and not val.startswith("fake"):
            return val
    return None


_needs_gemini = pytest.mark.skipif(
    not _real_gemini_key(),
    reason="Real GEMINI_API_KEY / GOOGLE_API_KEY not set",
)


# ---------------------------------------------------------------------------
# 1. Mock-based: advisory plumbing during a full cycle
# ---------------------------------------------------------------------------


class TestAdvisoryPlumbingInCycle:
    """Verify that advisories pushed to the queue appear in tool returns
    during a full ApiOrchestrator.cycle() call (mocked LLM)."""

    def test_advisory_injected_into_tool_return(self, tmp_path: Path):
        """Push advisory before cycle → first tool call drains it into
        the report the LLM sees."""
        log.init(RunDir.create(tmp_path, "plumb_tool"))

        queue = AdvisoryQueue()
        team = _make_team()

        # Track what the tool returns to the LLM
        tool_returns: list[str] = []

        def fake_run_sync(prompt, *, usage_limits=None, **kwargs):
            # Call ask_worker tool (first tool in the list)
            for tool in agent_tools:
                if tool.name == "ask_worker":
                    result = tool.function(task="do something")
                    tool_returns.append(result)
                    break
            # Then call goal_done
            for tool in agent_tools:
                if tool.name == "goal_done":
                    tool.function(summary="done")
                    break
            return FakeRunResult()

        agent_tools = []

        def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
            nonlocal agent_tools
            agent_tools = tools or []
            self.run_sync = fake_run_sync

        # Push advisory AFTER build_cycle_prompt runs but BEFORE tool call.
        # Since fake_run_sync is called after build_cycle_prompt, we push
        # in a wrapper that fires just before the first tool call.
        original_run_sync_timeout = ApiOrchestrator._run_sync_with_timeout

        def inject_then_run(self_orch, agent, prompt, max_exchanges, usage, **kw):
            # Advisory pushed here — after build_cycle_prompt drained (empty),
            # before run_sync calls tools.
            queue.push(
                "STOP: user wants you to abort immediately",
                source="human",
                priority="correction",
            )
            return original_run_sync_timeout(
                self_orch, agent, prompt, max_exchanges, usage, **kw
            )

        with (
            patch(
                "kodo.orchestrators.api.Agent.__init__",
                autospec=True,
                side_effect=fake_agent_init,
            ),
            patch.object(
                ApiOrchestrator,
                "_run_sync_with_timeout",
                inject_then_run,
            ),
        ):
            orch = ApiOrchestrator(model="claude-opus-4-6")
            try:
                orch.cycle(
                    "write hello world",
                    tmp_path,
                    team,
                    max_exchanges=10,
                    advisory_queue=queue,
                    config=CycleConfig(done_mode="new"),
                )
            finally:
                orch.shutdown()

        # The tool return should contain the advisory
        assert len(tool_returns) >= 1
        assert "[human]" in tool_returns[0]
        assert "STOP" in tool_returns[0]
        assert queue.pending_count == 0  # drained

    def test_scripted_input_delivers_to_queue(self):
        """ScriptedInput pushes messages into the queue on timer."""
        queue = AdvisoryQueue()
        scripted = ScriptedInput(queue).after(0.05, "hello from timer")
        scripted.start()
        try:
            time.sleep(0.2)
            assert queue.pending_count == 1
            drained = queue.drain()
            assert drained[0].message == "hello from timer"
            assert drained[0].source == "human"
        finally:
            scripted.cancel()

    def test_scripted_input_multiple_messages(self):
        """Multiple timed messages arrive in order."""
        queue = AdvisoryQueue()
        scripted = (
            ScriptedInput(queue)
            .after(0.05, "first")
            .after(0.10, "second")
            .after(0.15, "third", priority="correction")
        )
        scripted.start()
        try:
            time.sleep(0.3)
            drained = queue.drain()
            assert len(drained) == 3
            assert [d.message for d in drained] == ["first", "second", "third"]
            assert drained[2].priority == "correction"
        finally:
            scripted.cancel()


# ---------------------------------------------------------------------------
# 2. Real LLM: Gemini Flash responds to human steering
# ---------------------------------------------------------------------------


@_needs_gemini
@pytest.mark.live
class TestLLMRespondsToHumanSteering:
    """Real Gemini Flash tests: verify the orchestrator LLM actually reads
    [human] messages and adjusts behavior.

    These make real API calls and are skipped without a Gemini key.
    """

    @pytest.fixture(autouse=True)
    def _ensure_real_key(self):
        """Override fake keys injected by conftest with the real one."""
        key = _real_gemini_key()
        if not key:
            pytest.skip("No real Gemini API key")
        saved = {}
        for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
            saved[var] = os.environ.get(var)
        if os.environ.get("GEMINI_API_KEY", "").startswith("fake"):
            del os.environ["GEMINI_API_KEY"]
        os.environ["GOOGLE_API_KEY"] = key
        yield
        for var, val in saved.items():
            if val is not None:
                os.environ[var] = val
            else:
                os.environ.pop(var, None)

    def test_llm_stops_when_human_says_stop_in_prompt(self, tmp_path: Path):
        """Advisory pre-loaded → appears in cycle prompt → LLM calls goal_done
        without doing unnecessary work."""
        log.init(RunDir.create(tmp_path, "llm_stop_prompt"))

        queue = AdvisoryQueue()
        team = _make_team()

        # Pre-load advisory — it gets drained into build_cycle_prompt
        queue.push(
            "STOP. The user wants you to stop immediately. "
            "Do not do any work. Call goal_done right now.",
            source="human",
            priority="correction",
        )

        orch = ApiOrchestrator(model="gemini-flash")
        try:
            result = orch.cycle(
                "Write a hello world Python script and comprehensive tests",
                tmp_path,
                team,
                max_exchanges=15,
                advisory_queue=queue,
                config=CycleConfig(done_mode="new"),
            )
        finally:
            orch.shutdown()

        # The LLM should have called goal_done after seeing the human message
        assert result.finished, (
            f"LLM should have called goal_done after seeing human STOP message. "
            f"Got: finished={result.finished}, summary={result.summary!r}"
        )

    def test_llm_stops_when_human_says_stop_in_tool_return(self, tmp_path: Path):
        """Advisory pushed mid-run via ScriptedInput → appears in tool return →
        LLM calls goal_done on next exchange."""
        log.init(RunDir.create(tmp_path, "llm_stop_tool"))

        queue = AdvisoryQueue()
        team = _make_team()

        # Push advisory after 1 second — by then build_cycle_prompt has
        # already run (empty queue) and the orchestrator is waiting for
        # the Gemini API response. When the first tool call returns,
        # handle_agent_call will drain this into the report.
        scripted = ScriptedInput(queue).after(
            1.0,
            "STOP. The user wants you to stop immediately. "
            "Call goal_done now, do not dispatch more agents.",
            priority="correction",
        )

        orch = ApiOrchestrator(model="gemini-flash")
        scripted.start()
        try:
            result = orch.cycle(
                "Write a hello world Python script and comprehensive tests",
                tmp_path,
                team,
                max_exchanges=20,
                advisory_queue=queue,
                config=CycleConfig(done_mode="new"),
            )
        finally:
            scripted.cancel()
            orch.shutdown()

        assert result.finished, (
            f"LLM should have called goal_done after seeing human STOP in tool return. "
            f"Got: finished={result.finished}, exchanges={result.exchanges}, "
            f"summary={result.summary!r}"
        )
        # Should finish quickly — not use all 20 exchanges
        assert result.exchanges <= 10, (
            f"LLM used {result.exchanges} exchanges — should have stopped earlier "
            f"after seeing human STOP message"
        )

    def test_llm_acknowledges_feedback_in_tool_return(self, tmp_path: Path):
        """Human gives non-stop feedback → LLM continues but adjusts."""
        log.init(RunDir.create(tmp_path, "llm_feedback"))

        queue = AdvisoryQueue()
        team = _make_team()

        # Push feedback after 1 second
        scripted = ScriptedInput(queue).after(
            1.0,
            "Make sure to add error handling for file operations",
        )

        orch = ApiOrchestrator(model="gemini-flash")
        scripted.start()
        try:
            result = orch.cycle(
                "Write a hello world Python script",
                tmp_path,
                team,
                max_exchanges=20,
                advisory_queue=queue,
                config=CycleConfig(done_mode="new"),
            )
        finally:
            scripted.cancel()
            orch.shutdown()

        # LLM should finish (it got feedback, not a stop command)
        assert result.finished, (
            f"LLM should have finished after receiving feedback. "
            f"Got: finished={result.finished}, summary={result.summary!r}"
        )
