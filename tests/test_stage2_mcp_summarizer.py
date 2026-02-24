"""MCP server lifecycle and summarizer error recovery."""

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
from tests.conftest import make_agent


def _make_mcp_with_tools():
    """Build an MCP server with worker_fast + tester + done tools."""
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


def _make_fake_uvicorn_server(config):
    """Fake uvicorn.Server that runs an async loop until should_exit."""
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


def test_mcp_context_starts_server_thread():
    """McpServerContext.__enter__ finds a free port and starts a background thread."""
    mcp = _make_mcp_with_tools()

    with patch("uvicorn.Server", side_effect=_make_fake_uvicorn_server):
        with McpServerContext(mcp) as ctx:
            assert ctx.port > 0
            assert "127.0.0.1" in ctx.sse_url
            assert "/sse" in ctx.sse_url
            assert ctx._thread.is_alive()


def test_mcp_context_exit_joins_thread():
    """McpServerContext.__exit__ shuts down the server thread within a few seconds."""
    mcp = _make_mcp_with_tools()

    with patch("uvicorn.Server", side_effect=_make_fake_uvicorn_server):
        start = time.monotonic()
        with McpServerContext(mcp) as ctx:
            pass
        elapsed = time.monotonic() - start

        assert elapsed < 4.0, "__exit__ should complete well within the 5s join timeout"
        assert not ctx._thread.is_alive()


def test_mcp_exposes_expected_tools():
    """MCP server registers ask_<agent> + done tools matching the team."""
    mcp = _make_mcp_with_tools()
    tool_names = set(mcp._tool_manager._tools.keys())

    assert "ask_worker_fast" in tool_names
    assert "ask_tester" in tool_names
    assert "done" in tool_names
    assert len(tool_names) == 3


def test_summarize_api_failure_includes_fallback_context(tmp_path: Path):
    """When the summarizer API call fails, the summary includes accumulated work."""
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
        orch._summarizer.get_accumulated_summary()
        orch._summarizer.summarize("worker", "task2", "Did more")
        orch._summarizer.get_accumulated_summary()
        result = orch._summarize([])

    assert "Summarization failed" in result or "ConnectionError" in result
