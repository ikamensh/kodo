"""Exercise Kimi orchestration through a fake ACP executable and real MCP server."""

from __future__ import annotations


import pytest

from kodo.orchestrators.kimi_code import KimiCodeOrchestrator


@pytest.fixture
def kimi_cycle(fake_kimi):
    """Replace the external CLI while retaining process, protocol and MCP wiring."""
    orch = KimiCodeOrchestrator(
        model="configured-kimi", system_prompt="Coordinate tasks."
    )
    yield orch, fake_kimi
    orch._summarizer.shutdown()


def test_kimi_cycle_completes_through_mcp(kimi_cycle, tmp_path, monkeypatch):
    """The CLI receives the local MCP server and its done call completes the cycle."""
    orch, requests = kimi_cycle
    monkeypatch.setenv("KODO_FAKE_KIMI_MODE", "mcp")
    result = orch.cycle("Build the feature", tmp_path, {}, max_exchanges=5)
    assert result.finished and result.success
    assert result.summary == "Completed through Kimi MCP"
    messages = requests()
    created = next(m for m in messages if m.get("method") == "session/new")
    assert created["params"]["cwd"] == str(tmp_path)
    assert created["params"]["mcpServers"][0]["type"] == "sse"
    prompts = [m for m in messages if m.get("method") == "session/prompt"]
    assert len(prompts) == 1
    prompt = prompts[0]["params"]["prompt"][0]["text"]
    assert "Coordinate tasks." in prompt
    assert "Build the feature" in prompt


def test_kimi_nudge_resumes_session_and_completes(kimi_cycle, tmp_path, monkeypatch):
    """A missing done call triggers one nudge in the same session, then stops."""
    orch, requests = kimi_cycle
    monkeypatch.setenv("KODO_FAKE_KIMI_MODE", "nudge")
    result = orch.cycle("Build the feature", tmp_path, {}, max_exchanges=5)
    assert result.finished and result.success
    assert result.exchanges == 2
    messages = requests()
    methods = [m.get("method") for m in messages]
    assert methods.count("session/new") == 1
    assert methods.count("session/load") == 1
    prompts = [m for m in messages if m.get("method") == "session/prompt"]
    assert prompts[0]["params"]["sessionId"] == prompts[1]["params"]["sessionId"]


@pytest.mark.parametrize("exchanges", [1, 2, 20])
def test_kimi_missing_done_respects_exchange_and_nudge_limits(
    kimi_cycle, tmp_path, exchanges
):
    """Unfinished work preserves its summary and stops at the first applicable limit."""
    orch, requests = kimi_cycle
    result = orch.cycle("Build the feature", tmp_path, {}, max_exchanges=exchanges)
    assert not result.finished
    assert result.summary == "Hello world"
    assert result.exchanges == min(exchanges, 4)
    messages = requests()
    prompts = [m for m in messages if m.get("method") == "session/prompt"]
    assert len(prompts) == min(exchanges, 4)


def test_kimi_provider_error_does_not_trigger_nudges(kimi_cycle, tmp_path, monkeypatch):
    """A provider error is reported once rather than spending the remaining budget."""
    orch, requests = kimi_cycle
    monkeypatch.setenv("KODO_FAKE_KIMI_MODE", "error")
    result = orch.cycle("Build the feature", tmp_path, {}, max_exchanges=5)
    assert not result.finished
    assert "Provider unavailable" in result.summary
    messages = requests()
    assert sum(m.get("method") == "session/prompt" for m in messages) == 1


@pytest.mark.parametrize(("exchanges", "tool_calls"), [(1, 1), (3, 3), (3, 4)])
def test_kimi_tool_limit_uses_the_remaining_cycle_budget(
    kimi_cycle, tmp_path, monkeypatch, exchanges, tool_calls
):
    """Cancel at the budget and count actual work, including starts already queued."""
    orch, requests = kimi_cycle
    monkeypatch.setenv("KODO_FAKE_KIMI_MODE", "limit")
    monkeypatch.setenv("KODO_FAKE_KIMI_TOOLS", str(tool_calls))
    result = orch.cycle("Build the feature", tmp_path, {}, max_exchanges=exchanges)
    assert not result.finished
    assert result.exchanges == tool_calls
    methods = [message.get("method") for message in requests()]
    assert methods.count("session/prompt") == 1
    assert methods.count("session/cancel") == 1
