"""Session robustness: stderr overflow, dead-process safety, garbled output."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from kodo.sessions.base import QueryResult, SubprocessSession


# ---------------------------------------------------------------------------
# SubprocessSession stderr cap
# ---------------------------------------------------------------------------


class _StderrFloodSession(SubprocessSession):
    """Spawns a process that writes 15k lines to stderr to test the cap."""

    _session_label = "test"

    def query(self, prompt: str, project_dir: Path, *, max_turns: int) -> QueryResult:
        cmd = [
            sys.executable,
            "-c",
            "import sys; [sys.stderr.write(f'{i}\\n') for i in range(15000)]",
        ]
        proc, stderr_chunks, thread = self._spawn(cmd)
        stderr_text = self._wait(proc, stderr_chunks, thread)
        assert len(stderr_chunks) <= 10_001, "stderr should be capped at 10k lines"
        assert "[... stderr truncated ...]" in stderr_text
        return QueryResult(text="ok", elapsed_s=0.0)


def test_stderr_capped_at_10k_lines(tmp_path: Path):
    """SubprocessSession drops stderr beyond 10k lines to prevent OOM."""
    session = _StderrFloodSession(model="test")
    result = session.query("ignored", tmp_path, max_turns=1)
    assert result.text == "ok"


def test_terminate_already_dead_process(tmp_path: Path):
    """Calling terminate() on an already-exited process does not crash."""
    session = _StderrFloodSession(model="test")
    proc, stderr_chunks, thread = session._spawn(
        [sys.executable, "-c", "pass"]  # exits immediately
    )
    proc.wait()
    assert proc.poll() is not None
    session.terminate()
    session.terminate()  # double-call also safe


# ---------------------------------------------------------------------------
# GeminiCliOrchestrator edge cases
# ---------------------------------------------------------------------------


def _gemini_fake_run(response_stdout):
    """Return a subprocess.run side_effect that returns response_stdout for the main call."""

    def fake_run(cmd, **kwargs):
        if "mcp" in cmd:
            return MagicMock(returncode=0, stdout="", stderr="")
        return MagicMock(returncode=0, stdout=response_stdout, stderr="")

    return fake_run


def test_gemini_garbled_json_uses_raw_stdout(tmp_path: Path):
    """When Gemini CLI returns invalid JSON, the raw stdout becomes the summary."""
    from kodo.orchestrators.gemini_cli import GeminiCliOrchestrator
    from tests.conftest import make_agent

    team = {"worker": make_agent()}
    ctx_obj = MagicMock()
    ctx_obj.sse_url = "http://localhost:0"

    with (
        patch(
            "kodo.orchestrators.gemini_cli.subprocess.run",
            side_effect=_gemini_fake_run("not valid json {{{"),
        ),
        patch("kodo.orchestrators.gemini_cli.McpServerContext") as mock_ctx,
        patch("kodo.log.init"),
    ):
        mock_ctx.return_value.__enter__.return_value = ctx_obj
        orch = GeminiCliOrchestrator(model="gemini-2.5-flash")
        result = orch.cycle("Build X", tmp_path, team, max_exchanges=10)

    assert result.summary == "not valid json {{{"


def test_gemini_timeout_sets_finished_false(tmp_path: Path):
    """When Gemini CLI times out, CycleResult.finished is False with a timeout message."""
    from kodo.orchestrators.gemini_cli import GeminiCliOrchestrator
    from tests.conftest import make_agent

    team = {"worker": make_agent()}

    def fake_run(cmd, **kwargs):
        if "mcp" in cmd:
            return MagicMock(returncode=0)
        if "-p" in cmd:
            raise subprocess.TimeoutExpired(cmd="gemini", timeout=120)
        return MagicMock(returncode=0)

    ctx_obj = MagicMock()
    ctx_obj.sse_url = "http://localhost:0"

    with (
        patch("kodo.orchestrators.gemini_cli.subprocess.run", side_effect=fake_run),
        patch("kodo.orchestrators.gemini_cli.McpServerContext") as mock_ctx,
        patch("kodo.log.init"),
    ):
        mock_ctx.return_value.__enter__.return_value = ctx_obj
        orch = GeminiCliOrchestrator(model="gemini-2.5-flash")
        result = orch.cycle("Build X", tmp_path, team, max_exchanges=10)

    assert result.finished is False
    assert "timed out" in result.summary.lower()
