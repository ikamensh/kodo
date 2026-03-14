"""Adversarial tests for ApiOrchestrator — based on expected interface behavior."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from kodo import log
from kodo.agent import Agent
from kodo.log import RunDir
from kodo.orchestrators.api import ApiOrchestrator, _messages_to_text
from kodo.orchestrators.base import CycleConfig
from tests.conftest import FakeRunResult, FakeSession


def _make_team():
    session = FakeSession(response_text="ok")
    return {"worker": Agent(session, "test agent", max_turns=5)}


def test_done_with_success_false(tmp_path: Path):
    """Calling done(success=False) should mark finished but not successful."""
    log.init(RunDir.create(tmp_path, "done_fail"))

    def fake_run_sync(prompt, *, usage_limits=None, **kwargs):
        for tool in agent_tools:
            if tool.name == "done":
                tool.function(summary="cannot complete", success=False)
                break
        return FakeRunResult()

    agent_tools = []

    def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
        nonlocal agent_tools
        agent_tools = tools or []
        self.run_sync = fake_run_sync

    with patch("kodo.orchestrators.api.Agent.__init__", autospec=True, side_effect=fake_agent_init):
        orch = ApiOrchestrator(model="claude-opus-4-6")
        result = orch.cycle(
            "build feature", tmp_path, _make_team(), max_exchanges=10,
            config=CycleConfig(done_mode="legacy"),
        )

    assert result.finished is True
    assert result.success is False
    assert "cannot complete" in result.summary


def test_agent_crash_returns_error_string(tmp_path: Path):
    """If an agent tool crashes, the orchestrator should get an error string, not crash itself."""
    log.init(RunDir.create(tmp_path, "agent_crash"))
    crash_session = FakeSession(response_text="ok")
    crash_agent = Agent(crash_session, "crasher", max_turns=5)

    # Make the agent's run method raise

    def crashing_run(*args, **kwargs):
        raise RuntimeError("agent exploded")

    crash_agent.run = crashing_run

    team = {"worker": crash_agent}
    tool_results = []

    def fake_run_sync(prompt, *, usage_limits=None, **kwargs):
        # Call the worker tool and capture its return
        for tool in agent_tools:
            if tool.name == "ask_worker":
                result = tool.function(task="do something")
                tool_results.append(result)
                break
        # Then call done
        for tool in agent_tools:
            if tool.name == "done":
                tool.function(summary="tried", success=False)
                break
        return FakeRunResult()

    agent_tools = []

    def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
        nonlocal agent_tools
        agent_tools = tools or []
        self.run_sync = fake_run_sync

    with patch("kodo.orchestrators.api.Agent.__init__", autospec=True, side_effect=fake_agent_init):
        orch = ApiOrchestrator(model="claude-opus-4-6")
        orch.cycle(
            "build feature", tmp_path, team, max_exchanges=10,
            config=CycleConfig(done_mode="legacy"),
        )

    # Should not crash, and the tool result should contain the error
    assert len(tool_results) == 1
    assert "crashed" in tool_results[0]
    assert "exploded" in tool_results[0]


def test_messages_to_text_with_tool_parts():
    """_messages_to_text should handle ToolCallPart and ToolReturnPart."""
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        TextPart,
        ToolCallPart,
        ToolReturnPart,
    )

    messages = [
        ModelResponse(
            parts=[
                TextPart(part_kind="text", content="Let me call a tool"),
                ToolCallPart(
                    part_kind="tool-call",
                    tool_name="ask_worker",
                    tool_call_id="tc1",
                    args={"task": "build it"},
                ),
            ],
            model_name="test",
            timestamp="2024-01-01T00:00:00Z",
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    part_kind="tool-return",
                    tool_name="ask_worker",
                    tool_call_id="tc1",
                    content="done building",
                    timestamp="2024-01-01T00:00:00Z",
                ),
            ]
        ),
    ]
    text = _messages_to_text(messages)
    assert "ask_worker" in text
    assert "done building" in text
    assert "[assistant]" in text
    assert "[user]" in text


def test_messages_to_text_empty_list():
    """Empty message list should return empty string."""
    assert _messages_to_text([]) == ""


def test_cost_calculation_with_unknown_model(tmp_path: Path):
    """If the model isn't in the pricing table, cost should be 0 (not crash)."""
    log.init(RunDir.create(tmp_path, "unknown_pricing"))

    def fake_run_sync(prompt, *, usage_limits=None, **kwargs):
        for tool in agent_tools:
            if tool.name == "done":
                tool.function(summary="done", success=False)
                break
        return FakeRunResult()

    agent_tools = []

    def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
        nonlocal agent_tools
        agent_tools = tools or []
        self.run_sync = fake_run_sync

    with patch("kodo.orchestrators.api.Agent.__init__", autospec=True, side_effect=fake_agent_init):
        orch = ApiOrchestrator(model="some-unknown-model-2026")
        result = orch.cycle(
            "goal", tmp_path, _make_team(), max_exchanges=5,
            config=CycleConfig(done_mode="legacy"),
        )

    assert result.total_cost_usd == 0.0
