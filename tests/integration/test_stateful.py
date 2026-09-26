"""Integration tests for stateful scenarios — log viewer server."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest


pytestmark = pytest.mark.integration

_PROJECT_DIR = Path(__file__).resolve().parents[2]
_VIEWER_WAIT_TIMEOUT = 30.0  # polled; only a broken start waits this long
_VIEWER_POLL_INTERVAL = 0.3


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_viewer(port: int, tmp_path: Path) -> subprocess.Popen:
    # A host without reverse DNS (CI macOS stalls on it): a loopback server
    # must never resolve its own name.
    site = tmp_path / "site"
    site.mkdir()
    (site / "sitecustomize.py").write_text(
        "import socket\n"
        "def _no_reverse_dns(name=''):\n"
        "    raise OSError('reverse DNS unavailable')\n"
        "socket.getfqdn = _no_reverse_dns\n"
    )
    python_path = os.pathsep.join(filter(None, [str(site), os.environ.get("PYTHONPATH")]))
    return subprocess.Popen(
        [sys.executable, "-m", "kodo", "logs", "--port", str(port)],
        cwd=_PROJECT_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        # Headless and hermetic: `kodo logs` opens a browser before serving and
        # indexes every past run, which takes seconds on a well-used machine.
        env={
            **os.environ, "BROWSER": "true", "KODO_RUNS_DIR": str(tmp_path / "runs"),
            "PYTHONPATH": python_path,
        },
    )


def _wait_for_server(
    port: int, proc: subprocess.Popen, timeout: float = _VIEWER_WAIT_TIMEOUT
) -> bool:
    url = f"http://127.0.0.1:{port}/"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and proc.poll() is None:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(_VIEWER_POLL_INTERVAL)
    return False


class TestLogViewerServer:
    def test_viewer_serves_html(self, tmp_path: Path) -> None:
        port = _find_free_port()
        proc = _start_viewer(port, tmp_path)
        try:
            if not _wait_for_server(port, proc):
                proc.terminate()
                output, _ = proc.communicate(timeout=5)
                pytest.fail(f"Viewer did not become ready (exit {proc.returncode}):\n{output}")
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
                assert resp.status == 200
                body = resp.read()
                assert len(body) > 1000
                text = body.decode("utf-8", errors="replace")
                assert "<html" in text.lower() or "<!doctype" in text.lower()
        finally:
            proc.terminate()
            proc.wait(timeout=5)
