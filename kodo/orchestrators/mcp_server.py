"""MCP server construction and lifecycle management for CLI orchestrators."""

from __future__ import annotations

import asyncio
import socket
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kodo.orchestrators.base import (
        CycleConfig,
        DoneSignal,
        TeamConfig,
    )
    from kodo.orchestrators.verification import VerificationState
    from pathlib import Path


def build_mcp_server(
    team: "TeamConfig",
    project_dir: "Path",
    summarizer,
    done_signal: "DoneSignal",
    goal: str,
    orchestrator_tag: str = "unknown",
    verification_state: "VerificationState | None" = None,
    config: "CycleConfig | None" = None,
):
    """Build a FastMCP server exposing each team agent as a tool.

    Shared by all orchestrators that use MCP (ClaudeCode, GeminiCli, Codex, Cursor).
    """
    from mcp.server.fastmcp import FastMCP

    from kodo.orchestrators.tools import add_tools_to_mcp

    mcp = FastMCP("team")
    add_tools_to_mcp(
        mcp,
        team,
        project_dir,
        summarizer,
        done_signal,
        goal,
        orchestrator_tag=orchestrator_tag,
        verification_state=verification_state,
        config=config,
    )
    return mcp


_STDIO_BRIDGE_SCRIPT = """\
import asyncio, sys, json
from mcp.client.sse import sse_client
from mcp.server.stdio import stdio_server

async def main():
    async with sse_client("{url}") as (read_sse, write_sse):
        async with stdio_server() as (read_stdio, write_stdio):
            async def sse_to_stdio():
                async for msg in read_sse:
                    await write_stdio.send(msg)
            async def stdio_to_sse():
                async for msg in read_stdio:
                    await write_sse.send(msg)
            await asyncio.gather(sse_to_stdio(), stdio_to_sse())

asyncio.run(main())
"""


class McpServerContext:
    """Runs a FastMCP server on a random local port for CLI orchestrators.

    Usage::

        mcp = build_mcp_server(team, ...)
        with McpServerContext(mcp) as ctx:
            url = ctx.sse_url  # e.g. "http://127.0.0.1:54321/sse"
            # ... spawn CLI process that connects to this URL ...
    """

    def __init__(self, mcp) -> None:
        self._mcp = mcp
        self._server: Any = None
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._exc: Exception | None = None
        self.port: int = 0
        self.sse_url: str = ""

    def __enter__(self) -> "McpServerContext":
        # Find a free port
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]

        self.sse_url = f"http://127.0.0.1:{self.port}/sse"
        self._mcp.settings.host = "127.0.0.1"
        self._mcp.settings.port = self.port

        ready = threading.Event()

        def _run():
            try:
                import uvicorn

                config = uvicorn.Config(
                    self._mcp.sse_app(),
                    host="127.0.0.1",
                    port=self.port,
                    log_level="error",
                )
                server = uvicorn.Server(config)
                self._server = server

                # Signal ready once server is started
                original_startup = server.startup

                async def _startup(sockets=None):
                    await original_startup(sockets)
                    ready.set()

                server.startup = _startup

                self._loop = asyncio.new_event_loop()
                self._loop.run_until_complete(server.serve())
            except RuntimeError as e:
                # "Event loop is closed" / "Event loop stopped" are expected
                # when __exit__ asks the loop to stop; suppress them silently.
                if "event loop" not in str(e).lower():
                    self._exc = e
                    ready.set()
            except Exception as e:
                self._exc = e
                ready.set()  # unblock main thread so it can raise

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

        # Wait for the server to be ready (up to 10s)
        if not ready.wait(timeout=10):
            if self._server is not None:
                self._server.should_exit = True
            if self._thread:
                self._thread.join(timeout=5)
            if self._exc:
                raise self._exc
            raise RuntimeError("MCP server failed to start within 10s")

        if self._exc:
            if self._server is not None:
                self._server.should_exit = True
            if self._thread:
                self._thread.join(timeout=5)
            raise self._exc

        return self

    @property
    def stdio_bridge_cmd(self) -> list[str]:
        """Return a command that bridges stdio<->SSE for this MCP server.

        Useful for Codex CLI which only supports stdio MCP transport.
        Uses ``npx mcp-remote`` if available, or falls back to a Python script.
        """
        import shutil
        import sys

        if shutil.which("npx"):
            return ["npx", "-y", "mcp-remote", self.sse_url]
        return [
            sys.executable,
            "-u",
            "-c",
            _STDIO_BRIDGE_SCRIPT.format(url=self.sse_url),
        ]

    def __exit__(self, *exc) -> None:
        if self._server is not None:
            self._server.should_exit = True
        # Ask the event loop to stop so run_until_complete() returns
        # and the thread can exit naturally.
        if self._loop:
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except RuntimeError:
                pass  # loop already closed/stopped
        if self._thread:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                from kodo import log

                log.tprint("[mcp] server thread did not stop within 5s, retrying...")
                # Escalation: retry loop.stop and give 2s more
                if self._loop:
                    try:
                        self._loop.call_soon_threadsafe(self._loop.stop)
                    except RuntimeError:
                        pass
                self._thread.join(timeout=2)
                if self._thread.is_alive():
                    log.emit(
                        "mcp_server_thread_stuck",
                        message="Thread still alive after 7s",
                    )
        # Close loop even if thread is stuck — the daemon thread will be
        # cleaned up at process exit.
        if self._loop:
            try:
                self._loop.close()
            except (OSError, RuntimeError):
                pass  # best-effort cleanup
        # Propagate any exception captured in the server thread.
        if self._exc:
            from kodo import log

            log.tprint(f"[mcp] server crashed: {self._exc}")
            raise self._exc
