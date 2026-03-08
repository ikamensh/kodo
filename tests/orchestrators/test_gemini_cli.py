"""Tests for GeminiCliOrchestrator cycle behavior."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.conftest import make_agent


def _gemini_fake_run(response_stdout):
    """Return a subprocess.run side_effect that returns response_stdout."""

    def fake_run(cmd, **kwargs):
        if "mcp" in cmd:
            return MagicMock(returncode=0, stdout="", stderr="")
        return MagicMock(returncode=0, stdout=response_stdout, stderr="")

    return fake_run


def test_gemini_garbled_json_uses_raw_stdout(tmp_path: Path):
    """When Gemini CLI returns invalid JSON, the raw stdout becomes the summary."""
    from kodo.orchestrators.gemini_cli import GeminiCliOrchestrator

    team = {"worker": make_agent()}
    ctx_obj = MagicMock()
    ctx_obj.sse_url = "http://localhost:0"

    with (
        patch(
            "kodo.orchestrators.gemini_cli.subprocess.run",
            side_effect=_gemini_fake_run("not valid json {{{"),
        ),
        patch("kodo.orchestrators.cli_base.McpServerContext") as mock_ctx,
        patch("kodo.log.init"),
    ):
        mock_ctx.return_value.__enter__.return_value = ctx_obj
        orch = GeminiCliOrchestrator(model="gemini-2.5-flash")
        result = orch.cycle("Build X", tmp_path, team, max_exchanges=10)

    assert result.summary == "not valid json {{{"


def test_gemini_timeout_sets_finished_false(tmp_path: Path):
    """When Gemini CLI times out, CycleResult.finished is False."""
    from kodo.orchestrators.gemini_cli import GeminiCliOrchestrator

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
        patch("kodo.orchestrators.cli_base.McpServerContext") as mock_ctx,
        patch("kodo.log.init"),
    ):
        mock_ctx.return_value.__enter__.return_value = ctx_obj
        orch = GeminiCliOrchestrator(model="gemini-2.5-flash")
        result = orch.cycle("Build X", tmp_path, team, max_exchanges=10)

    assert result.finished is False
    assert "timed out" in result.summary.lower()
