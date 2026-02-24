"""Tests for MCP server context and summarizer — areas with known issues and partial coverage."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from kodo import log
from kodo.log import RunDir
from kodo.orchestrators.base import McpServerContext, build_mcp_server, DoneSignal
from kodo.orchestrators.api import ApiOrchestrator
from kodo.summarizer import Summarizer
from tests.conftest import FakeRunResult, make_agent


# ---------------------------------------------------------------------------
# McpServerContext tests
# ---------------------------------------------------------------------------


def _make_mcp_with_tools():
    """Build MCP server with worker_fast, tester, done — mirrors saga team tools."""
    team = {
        "worker_fast": make_agent("ok"),
        "tester": make_agent("ALL CHECKS PASS"),
    }
    with (
        patch("kodo.summarizer._probe_ollama", return_value=None),
        patch("kodo.summarizer._probe_gemini", return_value=None),
    ):
        summarizer = Summarizer()
    return build_mcp_server(
        team, Path("/tmp/proj"), summarizer, DoneSignal(), "Build X"
    )


def test_mcp_server_context_enter_finds_port_and_starts_thread(tmp_path: Path):
    """McpServerContext.__enter__ finds a free port and starts server thread."""
    mcp = _make_mcp_with_tools()

    def make_server(config):
        s = MagicMock()
        s.should_exit = False

        # Real code replaces server.startup with _startup that calls ready.set()
        async def noop_startup(sockets=None):
            pass

        s.startup = noop_startup

        async def serve():
            await s.startup()
            while not s.should_exit:
                await asyncio.sleep(0.02)

        s.serve = serve
        return s

    with patch("uvicorn.Server", side_effect=make_server):
        with McpServerContext(mcp) as ctx:
            assert ctx.port > 0
            assert "127.0.0.1" in ctx.sse_url
            assert "/sse" in ctx.sse_url
            assert ctx._thread is not None
            assert ctx._thread.is_alive()


def test_mcp_server_context_exit_shuts_down_without_hanging(tmp_path: Path):
    """McpServerContext.__exit__ cleanly shuts down without hanging (timeout 5s)."""
    mcp = _make_mcp_with_tools()

    def make_server(config):
        s = MagicMock()
        s.should_exit = False

        async def noop_startup(sockets=None):
            pass

        s.startup = noop_startup

        async def serve():
            await s.startup()
            while not s.should_exit:
                await asyncio.sleep(0.02)

        s.serve = serve
        return s

    with patch("uvicorn.Server", side_effect=make_server):
        start = time.monotonic()
        with McpServerContext(mcp) as ctx:
            pass  # __exit__ runs here
        elapsed = time.monotonic() - start

        assert elapsed < 4.0, "__exit__ should complete within 4s (join timeout is 5s)"
        assert ctx._thread is not None
        assert not ctx._thread.is_alive(), "Thread should be joined"


def test_mcp_tools_match_expected_kodo_tools(tmp_path: Path):
    """MCP tools exposed match expected kodo tools (ask_worker_fast, ask_tester, done, etc.)."""
    mcp = _make_mcp_with_tools()
    tool_names = set(mcp._tool_manager._tools.keys())

    assert "ask_worker_fast" in tool_names
    assert "ask_tester" in tool_names
    assert "done" in tool_names
    assert len(tool_names) == 3  # 2 agents + done


# ---------------------------------------------------------------------------
# Summarizer / ApiOrchestrator _summarize tests
# ---------------------------------------------------------------------------


def test_summarize_with_mock_agent_produces_summary(tmp_path: Path):
    """ApiOrchestrator._summarize with mock pydantic-ai agent produces a summary string."""
    log.init(RunDir.create(tmp_path, "sum_ok"))

    def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
        self.run_sync = lambda prompt, **kw: FakeRunResult(
            output="Completed task X. Pending: task Y."
        )

    with patch("kodo.orchestrators.api.Agent.__init__", fake_agent_init):
        orch = ApiOrchestrator(model="claude-opus-4-6")
        result = orch._summarize([{"role": "user", "content": "hello"}])

    assert result is not None
    assert isinstance(result, str)
    assert "Completed task X" in result


def test_summarize_api_failure_falls_back_to_accumulated(tmp_path: Path):
    """When _summarize's API fails, fall back to accumulated summaries per architecture."""
    log.init(RunDir.create(tmp_path, "sum_fail"))

    def fake_agent_init(self, model, *, system_prompt=None, tools=None, **kwargs):
        def run_sync_raises(prompt, **kw):
            raise ConnectionError("API unavailable")

        self.run_sync = run_sync_raises

    with (
        patch("kodo.orchestrators.api.Agent.__init__", fake_agent_init),
        patch("kodo.summarizer._probe_ollama", return_value=None),
        patch("kodo.summarizer._probe_gemini", return_value=None),
    ):
        orch = ApiOrchestrator(model="claude-opus-4-6")
        orch._summarizer.summarize("worker", "task", "Did something")
        orch._summarizer.get_accumulated_summary()  # drain to populate
        orch._summarizer.summarize("worker", "task2", "Did more")
        orch._summarizer.get_accumulated_summary()  # drain again
        result = orch._summarize([])

    assert result is not None
    assert "Summarization failed" in result or "ConnectionError" in result
    assert (
        "Work so far" in result
        or "accumulated" in result.lower()
        or "[worker]" in result
    )


def test_summarizer_none_empty_inputs_no_crash():
    """Summarizer._do_summarize with None/empty inputs does not crash."""
    with (
        patch("kodo.summarizer._probe_ollama", return_value=None),
        patch("kodo.summarizer._probe_gemini", return_value=None),
    ):
        s = Summarizer()

    # These should not raise
    s._do_summarize("agent", None, None)
    s._do_summarize("agent", "", "")
    s._do_summarize("agent", "task", None)
    s._do_summarize("agent", None, "report")

    acc = s.get_accumulated_summary()
    # With truncate backend, empty report yields "" from _summarize_truncate, so nothing appended
    assert isinstance(acc, str)
