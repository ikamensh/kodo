"""MCP server lifecycle and summarizer error recovery."""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from kodo import log
from kodo.log import RunDir
from kodo.orchestrators.api import ApiOrchestrator
from kodo.orchestrators.base import DoneSignal
from kodo.orchestrators.mcp_server import McpServerContext, build_mcp_server
from kodo.summarizer import Summarizer
from tests.conftest import make_agent


def _make_mcp_with_tools():
    """Build an MCP server with worker_fast + tester + done tools."""
    from kodo.orchestrators.base import CycleConfig

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
        team, Path("/tmp/proj"), summarizer, DoneSignal(), "Build X",
        config=CycleConfig(done_mode="legacy"),
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


# ── New Coverage Tests ────────────────────────────────────────────────────


def test_stdio_bridge_cmd_uses_npx_when_available():
    """stdio_bridge_cmd should use npx when available."""
    mcp = _make_mcp_with_tools()

    with (
        patch("uvicorn.Server", side_effect=_make_fake_uvicorn_server),
        patch("shutil.which", return_value="/usr/bin/npx"),
    ):
        with McpServerContext(mcp) as ctx:
            cmd = ctx.stdio_bridge_cmd
            assert cmd[0] == "npx"
            assert cmd[1] == "-y"
            assert cmd[2] == "mcp-remote"
            assert ctx.sse_url in cmd[3]


def test_stdio_bridge_cmd_falls_back_to_python():
    """stdio_bridge_cmd should fall back to Python script when npx unavailable."""
    import sys
    mcp = _make_mcp_with_tools()

    with (
        patch("uvicorn.Server", side_effect=_make_fake_uvicorn_server),
        patch("shutil.which", return_value=None),
    ):
        with McpServerContext(mcp) as ctx:
            cmd = ctx.stdio_bridge_cmd
            assert cmd[0] == sys.executable
            assert cmd[1] == "-u"
            assert cmd[2] == "-c"
            assert ctx.sse_url in cmd[3]


def test_enter_raises_on_server_runtime_error():
    """__enter__ should raise RuntimeError from server thread (non-event-loop errors)."""
    import pytest
    mcp = _make_mcp_with_tools()

    def fake_uvicorn_server(config):
        """Fake server that raises RuntimeError during serve."""
        s = MagicMock()
        s.should_exit = False

        async def noop_startup(sockets=None):
            pass

        s.startup = noop_startup

        async def serve():
            await s.startup()
            raise RuntimeError("port already in use")

        s.serve = serve
        return s

    with (
        patch("uvicorn.Server", side_effect=fake_uvicorn_server),
        pytest.raises(RuntimeError, match="port already in use"),
    ):
        with McpServerContext(mcp):
            pass


def test_enter_raises_on_server_generic_exception():
    """__enter__ should raise generic exceptions from server thread."""
    import pytest
    mcp = _make_mcp_with_tools()

    def fake_uvicorn_server(config):
        """Fake server that raises ValueError during serve."""
        s = MagicMock()
        s.should_exit = False

        async def noop_startup(sockets=None):
            pass

        s.startup = noop_startup

        async def serve():
            await s.startup()
            raise ValueError("bad config")

        s.serve = serve
        return s

    with (
        patch("uvicorn.Server", side_effect=fake_uvicorn_server),
        pytest.raises(ValueError, match="bad config"),
    ):
        with McpServerContext(mcp):
            pass


def test_enter_raises_on_startup_timeout():
    """__enter__ should raise RuntimeError when server doesn't start in time."""
    import pytest
    mcp = _make_mcp_with_tools()

    def fake_uvicorn_server(config):
        """Fake server that never signals ready."""
        s = MagicMock()
        s.should_exit = False

        async def slow_startup(sockets=None):
            # Never completes, simulating hung startup
            await asyncio.sleep(100)

        s.startup = slow_startup

        async def serve():
            await s.startup()

        s.serve = serve
        return s

    # We need to mock the ready event's wait to return False without
    # breaking thread internals. Let's use a side_effect on Event creation.
    original_event_class = threading.Event
    event_count = [0]

    def event_factory():
        event_count[0] += 1
        evt = original_event_class()
        if event_count[0] == 1:
            # First Event is the 'ready' event in __enter__
            # Mock its wait to return False (timeout)
            evt.wait = lambda timeout=None: False
        return evt

    with (
        patch("uvicorn.Server", side_effect=fake_uvicorn_server),
        patch("threading.Event", side_effect=event_factory),
        pytest.raises(RuntimeError, match="MCP server failed to start within 10s"),
    ):
        with McpServerContext(mcp):
            pass


def test_exit_handles_loop_already_closed():
    """__exit__ should handle RuntimeError when loop is already closed."""
    mcp = _make_mcp_with_tools()

    with patch("uvicorn.Server", side_effect=_make_fake_uvicorn_server):
        ctx = McpServerContext(mcp)
        ctx.__enter__()

        # Mock loop.call_soon_threadsafe to raise RuntimeError

        def raise_runtime_error(*args):
            raise RuntimeError("Event loop is closed")

        ctx._loop.call_soon_threadsafe = raise_runtime_error

        # Should not raise - __exit__ handles this gracefully
        ctx.__exit__(None, None, None)

        # Thread should be stopped
        assert not ctx._thread.is_alive()


def test_exit_escalates_on_stuck_thread():
    """__exit__ should escalate when thread doesn't stop, calling loop.stop multiple times."""
    mcp = _make_mcp_with_tools()

    with patch("uvicorn.Server", side_effect=_make_fake_uvicorn_server):
        ctx = McpServerContext(mcp)
        ctx.__enter__()

        # Track calls to call_soon_threadsafe
        call_count = {"call_soon_threadsafe": 0}
        original_call_soon = ctx._loop.call_soon_threadsafe

        def track_call_soon(func):
            call_count["call_soon_threadsafe"] += 1
            # Actually call original to properly stop the loop
            original_call_soon(func)

        ctx._loop.call_soon_threadsafe = track_call_soon

        # Mock thread.is_alive to return True for first join, then False
        join_count = [0]
        original_join = ctx._thread.join

        def mock_join(timeout=None):
            join_count[0] += 1
            if join_count[0] <= 2:
                # Don't actually join to simulate stuck thread
                time.sleep(0.01)
            else:
                # Finally join to let test complete
                original_join(timeout=0.1)

        def mock_is_alive():
            # Stuck for first two checks, then stops
            return join_count[0] <= 2

        ctx._thread.join = mock_join
        ctx._thread.is_alive = mock_is_alive

        # Mock log.emit to verify it's called
        with patch("kodo.log.emit") as mock_emit:
            ctx.__exit__(None, None, None)

            # Should have called call_soon_threadsafe twice (initial + escalation)
            assert call_count["call_soon_threadsafe"] >= 2

            # Should have called log.emit with the stuck thread event
            mock_emit.assert_called_once_with(
                "mcp_server_thread_stuck",
                message="Thread still alive after 7s",
            )
