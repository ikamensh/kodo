"""MCP server lifecycle and summarizer error recovery."""

from __future__ import annotations

import asyncio
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, create_autospec, patch

import httpx
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

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
        patch("kodo.summarizer._probe_ollama", autospec=True, return_value=None),
        patch("kodo.summarizer._probe_gemini", autospec=True, return_value=None),
    ):
        summarizer = Summarizer()
    return build_mcp_server(
        team,
        Path("/tmp/proj"),
        summarizer,
        DoneSignal(),
        "Build X",
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

    with patch("uvicorn.Server", autospec=True, side_effect=_make_fake_uvicorn_server):
        with McpServerContext(mcp) as ctx:
            assert ctx.port > 0
            assert "127.0.0.1" in ctx.sse_url
            assert "/sse" in ctx.sse_url
            assert ctx._thread.is_alive()


def test_mcp_context_exit_joins_thread():
    """McpServerContext.__exit__ shuts down the server thread within a few seconds."""
    mcp = _make_mcp_with_tools()

    with patch("uvicorn.Server", autospec=True, side_effect=_make_fake_uvicorn_server):
        start = time.monotonic()
        with McpServerContext(mcp) as ctx:
            pass
        elapsed = time.monotonic() - start

        assert elapsed < 4.0, "__exit__ should complete well within the 5s join timeout"
        assert not ctx._thread.is_alive()


def test_mcp_context_finishes_application_shutdown():
    """Leaving the context awaits the real server's async lifespan cleanup."""
    events = []

    @asynccontextmanager
    async def lifespan(app):
        events.append("started")
        yield
        await asyncio.sleep(0.01)
        events.append("closed")

    async def health(request):
        return PlainTextResponse("ready")

    app = Starlette(routes=[Route("/health", health)], lifespan=lifespan)
    mcp = SimpleNamespace(settings=SimpleNamespace(), sse_app=lambda: app)
    with McpServerContext(mcp) as ctx:
        response = httpx.get(f"http://127.0.0.1:{ctx.port}/health", timeout=2)
        assert response.text == "ready"
        assert events == ["started"]

    assert events == ["started", "closed"]
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
        patch(
            "kodo.orchestrators.api.Agent.__init__",
            autospec=True,
            side_effect=fake_agent_init,
        ),
        patch("kodo.summarizer._probe_ollama", autospec=True, return_value=None),
        patch("kodo.summarizer._probe_gemini", autospec=True, return_value=None),
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
        patch("uvicorn.Server", autospec=True, side_effect=_make_fake_uvicorn_server),
        patch("shutil.which", autospec=True, return_value="/usr/bin/npx"),
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
        patch("uvicorn.Server", autospec=True, side_effect=_make_fake_uvicorn_server),
        patch("shutil.which", autospec=True, return_value=None),
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
        patch("uvicorn.Server", autospec=True, side_effect=fake_uvicorn_server),
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
        patch("uvicorn.Server", autospec=True, side_effect=fake_uvicorn_server),
        pytest.raises(ValueError, match="bad config"),
    ):
        with McpServerContext(mcp):
            pass


def test_enter_raises_on_startup_timeout():
    """__enter__ should raise RuntimeError when server doesn't start in time."""
    import pytest

    mcp = _make_mcp_with_tools()

    def fake_uvicorn_server(config):
        """Fake server that never signals ready but exits when told."""
        s = MagicMock()
        s.should_exit = False

        async def slow_startup(sockets=None):
            while not s.should_exit:
                await asyncio.sleep(0.01)

        s.startup = slow_startup

        async def serve():
            await s.startup()

        s.serve = serve
        return s

    original_event_class = threading.Event
    event_count = [0]

    def event_factory():
        event_count[0] += 1
        evt = original_event_class()
        if event_count[0] == 1:
            # Mock wait to return False (timeout) after a tiny delay
            # so the thread has time to set self._server
            def _fake_wait(timeout=None):
                time.sleep(0.05)
                return False

            evt.wait = _fake_wait
        return evt

    with (
        patch("uvicorn.Server", autospec=True, side_effect=fake_uvicorn_server),
        patch("threading.Event", autospec=True, side_effect=event_factory),
        pytest.raises(RuntimeError, match="MCP server failed to start within 10s"),
    ):
        with McpServerContext(mcp):
            pass


def test_exit_handles_loop_already_closed():
    """Repeated cleanup leaves an already stopped server and closed loop alone."""
    mcp = _make_mcp_with_tools()
    with patch("uvicorn.Server", autospec=True, side_effect=_make_fake_uvicorn_server):
        with McpServerContext(mcp) as ctx:
            pass
        ctx.__exit__()
        assert not ctx._thread.is_alive()
        assert ctx._loop.is_closed()


def test_exit_unwinds_unresponsive_server(monkeypatch):
    """The shutdown deadline still allows cancelled server tasks to clean up."""
    cleaned_up = threading.Event()

    def unresponsive_server(config):
        server = SimpleNamespace(should_exit=False)

        async def startup(sockets=None):
            pass

        async def serve():
            await server.startup()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0.01)
                cleaned_up.set()

        server.startup = startup
        server.serve = serve
        return server

    with patch("uvicorn.Server", autospec=True, side_effect=unresponsive_server):
        with McpServerContext(_make_mcp_with_tools()) as ctx:
            original_join = ctx._thread.join
            # Exercise the timeout without spending five seconds waiting.
            monkeypatch.setattr(
                ctx._thread, "join", lambda timeout: original_join(timeout=0.2)
            )

    assert cleaned_up.is_set()
    assert not ctx._thread.is_alive()
    assert ctx._loop.is_closed()


def test_exit_reports_stuck_thread():
    """A server that outlives the shutdown deadline reports a stuck-thread event."""
    ctx = McpServerContext(_make_mcp_with_tools())
    ctx._thread = create_autospec(threading.Thread, instance=True)
    ctx._thread.is_alive.return_value = True
    with patch("kodo.log.emit", autospec=True) as emit:
        ctx.__exit__()
    emit.assert_called_once_with(
        "mcp_server_thread_stuck", message="Thread still alive after 7s"
    )
