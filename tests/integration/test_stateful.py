"""Integration tests for stateful/side-effect scenarios (US6 deep, US10 deep, config persistence)."""

from __future__ import annotations

import json
import re
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest

from tests.integration.cli_wrapper import run_kodo

pytestmark = pytest.mark.integration

EXEC_TIMEOUT = 120
_VIEWER_WAIT_TIMEOUT = 5.0
_VIEWER_POLL_INTERVAL = 0.3

_PROJECT_DIR = Path(__file__).resolve().parents[2]


def _find_free_port() -> int:
    """Bind to 127.0.0.1:0, get assigned port, close socket."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_viewer(port: int) -> subprocess.Popen:
    """Start kodo logs server in background. Caller must terminate and clean up."""
    proc = subprocess.Popen(
        ["uv", "run", "kodo", "logs", "--port", str(port)],
        cwd=_PROJECT_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    return proc


def _wait_for_server(port: int, timeout: float = _VIEWER_WAIT_TIMEOUT) -> bool:
    """Poll until server responds or timeout. Returns True if ready."""
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


def _get_latest_run_id() -> str | None:
    """Get the most recent run ID from kodo runs output."""
    result = run_kodo("runs", timeout=30)
    if not result.success():
        return None
    # Run IDs match YYYYMMDD_HHMMSS
    match = re.search(r"(\d{8}_\d{6})", result.output())
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# 1. Log Viewer Server (US6 deep)
# ---------------------------------------------------------------------------


class TestLogViewerServer:
    """US6: kodo logs starts the log viewer server — deep tests."""

    def test_viewer_serves_html(self) -> None:
        """Start viewer on random port, GET /, verify 200, HTML, >1000 bytes."""
        port = _find_free_port()
        proc = _start_viewer(port)
        try:
            assert _wait_for_server(port), "Viewer did not become ready in time"
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/", timeout=5
            ) as resp:
                assert resp.status == 200
                body = resp.read()
                assert len(body) > 1000, f"Expected >1000 bytes, got {len(body)}"
                text = body.decode("utf-8", errors="replace")
                assert "<html" in text.lower() or "<!doctype" in text.lower()
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_viewer_serves_on_custom_port(self) -> None:
        """Viewer listens on the specified --port."""
        port = _find_free_port()
        proc = _start_viewer(port)
        try:
            assert _wait_for_server(port), "Viewer did not become ready in time"
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/", timeout=5
            ) as resp:
                assert resp.status == 200
                assert len(resp.read()) > 100
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_viewer_nonexistent_logfile(self) -> None:
        """kodo logs with nonexistent logfile fails with clear error."""
        result = run_kodo("logs", "/tmp/nonexistent_xyz_12345.jsonl", timeout=10)
        assert result.exit_code != 0
        out = result.output().lower()
        assert "not found" in out or "file" in out or "error" in out


# ---------------------------------------------------------------------------
# 2. Run Resume (US10 deep)
# ---------------------------------------------------------------------------


class TestRunResume:
    """US10: kodo --resume resumes an interrupted run — deep tests."""

    def test_resume_latest_after_debug_run(self) -> None:
        """Debug run creates a run; resume by run_id succeeds."""
        run_kodo("--debug", "--goal", "first run", "--yes", timeout=EXEC_TIMEOUT)
        run_id = _get_latest_run_id()
        assert run_id, "No run ID found after debug run"
        result = run_kodo(
            "--debug", "--resume", run_id, "--yes", timeout=EXEC_TIMEOUT
        )
        assert result.success(), f"Resume failed: {result.output()}"
        assert "resum" in result.output().lower() or run_id in result.output()

    def test_resume_specific_run_id(self) -> None:
        """Resume with explicit run ID succeeds."""
        run_kodo("--debug", "--goal", "resume test goal", "--yes", timeout=EXEC_TIMEOUT)
        run_id = _get_latest_run_id()
        assert run_id, "No run ID found after debug run"
        result = run_kodo(
            "--debug", "--resume", run_id, "--yes", timeout=EXEC_TIMEOUT
        )
        assert result.success(), f"Resume failed: {result.output()}"


# ---------------------------------------------------------------------------
# 3. Config Persistence (filesystem side effects)
# ---------------------------------------------------------------------------


class TestConfigPersistence:
    """Config and run directory persistence."""

    def test_debug_run_creates_run_directory(self) -> None:
        """Debug run creates entries under ~/.kodo/runs/."""
        run_kodo("--debug", "--goal", "config test", "--yes", timeout=EXEC_TIMEOUT)
        result = run_kodo("runs", timeout=30)
        assert result.success()
        # Should have at least one run (table header + data row)
        lines = [l for l in result.output().splitlines() if l.strip()]
        assert len(lines) >= 2, f"Expected runs output, got: {result.output()}"
        # Data rows contain run IDs (YYYYMMDD_HHMMSS)
        assert re.search(r"\d{8}_\d{6}", result.output()), "No run ID in output"

    def test_json_output_contains_run_metadata(self) -> None:
        """JSON output contains run metadata (status, cycles, summary, etc.)."""
        result = run_kodo(
            "--debug", "--goal", "metadata test", "--yes", "--json",
            timeout=EXEC_TIMEOUT,
        )
        assert result.success(), f"Run failed: {result.output()}"
        data = json.loads(result.stdout)
        assert isinstance(data, dict)
        assert "status" in data
        assert "cycles" in data
        assert "summary" in data
        assert data["status"] == "completed"
        assert isinstance(data["cycles"], int) and data["cycles"] >= 1
