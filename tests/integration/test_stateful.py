"""Integration tests for stateful scenarios — log viewer server."""

from __future__ import annotations

import socket
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest

from tests.integration.cli_wrapper import run_kodo

pytestmark = pytest.mark.integration

_PROJECT_DIR = Path(__file__).resolve().parents[2]
_VIEWER_WAIT_TIMEOUT = 5.0
_VIEWER_POLL_INTERVAL = 0.3


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_viewer(port: int) -> subprocess.Popen:
    return subprocess.Popen(
        ["uv", "run", "kodo", "logs", "--port", str(port)],
        cwd=_PROJECT_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


def _wait_for_server(port: int, timeout: float = _VIEWER_WAIT_TIMEOUT) -> bool:
    url = f"http://127.0.0.1:{port}/"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(_VIEWER_POLL_INTERVAL)
    return False


class TestLogViewerServer:
    def test_viewer_serves_html(self) -> None:
        port = _find_free_port()
        proc = _start_viewer(port)
        try:
            assert _wait_for_server(port), "Viewer did not become ready"
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
                assert resp.status == 200
                body = resp.read()
                assert len(body) > 1000
                text = body.decode("utf-8", errors="replace")
                assert "<html" in text.lower() or "<!doctype" in text.lower()
        finally:
            proc.terminate()
            proc.wait(timeout=5)
