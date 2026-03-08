"""Tests for CursorOrchestrator — stream-json parsing, MCP config, error handling."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kodo.orchestrators.cursor_cli import CursorOrchestrator


# ── Helpers ─────────────────────────────────────────────────────────────


@contextlib.contextmanager
def _base_patches(done_signal):
    """Stub out MCP server, logging, and build_cycle_prompt."""
    mock_mcp = MagicMock()
    mock_ctx = MagicMock()
    mock_ctx.sse_url = "http://127.0.0.1:9999/sse"
    mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
    mock_ctx.__exit__ = MagicMock(return_value=False)

    with (
        patch(
            "kodo.orchestrators.cli_base.build_mcp_server", return_value=mock_mcp
        ),
        patch(
            "kodo.orchestrators.cli_base.McpServerContext", return_value=mock_ctx
        ),
        patch("kodo.orchestrators.cli_base.build_cycle_prompt", return_value="go"),
        patch("kodo.orchestrators.cli_base.DoneSignal", return_value=done_signal),
        patch("kodo.orchestrators.cli_base.VerificationState"),
        patch("kodo.orchestrators.cli_base.log"),
        patch("kodo.orchestrators.cursor_cli.log"),
    ):
        yield


def _make_popen(stdout_lines, returncode=0, stderr_text=""):
    """Build a mock Popen that yields stdout_lines as stream-json."""
    mock_proc = MagicMock()
    mock_proc.stdout = iter(
        [json.dumps(line) + "\n" if isinstance(line, dict) else line + "\n"
         for line in stdout_lines]
    )
    mock_proc.stderr = MagicMock()
    mock_proc.stderr.read.return_value = stderr_text
    mock_proc.returncode = returncode
    mock_proc.wait.return_value = returncode
    mock_proc.poll.return_value = returncode
    return mock_proc


def _make_orch():
    return CursorOrchestrator(model="cursor-test")


# ── Stream-JSON parsing ────────────────────────────────────────────────


class TestCursorStreamParsing:
    def test_tool_use_increments_exchanges(self, tmp_path: Path):
        """Each tool_use message increments exchange counter."""
        done = MagicMock(called=True, success=True, summary="done", terminal="goal_done")
        proc = _make_popen([
            {"type": "tool_use"},
            {"type": "tool_use"},
            {"type": "result", "result": "final answer"},
        ])

        with _base_patches(done), patch("subprocess.Popen", return_value=proc):
            result = _make_orch().cycle(
                goal="test", project_dir=tmp_path, team=MagicMock(), max_exchanges=5,
            )

        assert result.exchanges >= 2

    def test_result_type_captures_text(self, tmp_path: Path):
        """Result message captures the result text."""
        done = MagicMock(called=False, summary="", terminal=None, success=False)
        proc = _make_popen([
            {"type": "tool_use"},
            {"type": "result", "result": "Here is your answer"},
        ])

        with _base_patches(done), patch("subprocess.Popen", return_value=proc):
            result = _make_orch().cycle(
                goal="test", project_dir=tmp_path, team=MagicMock(), max_exchanges=5,
            )

        assert "Here is your answer" in result.summary

    def test_malformed_json_skipped(self, tmp_path: Path):
        """Non-JSON lines are silently skipped."""
        done = MagicMock(called=False, summary="", terminal=None, success=False)
        proc = _make_popen([
            "garbage line",
            {"type": "result", "result": "ok"},
        ])

        with _base_patches(done), patch("subprocess.Popen", return_value=proc):
            result = _make_orch().cycle(
                goal="test", project_dir=tmp_path, team=MagicMock(), max_exchanges=5,
            )

        assert "ok" in result.summary


# ── MCP config file management ─────────────────────────────────────────


class TestCursorMcpConfig:
    def test_creates_mcp_config(self, tmp_path: Path):
        """MCP config is written to .cursor/mcp.json before subprocess."""
        done = MagicMock(called=True, success=True, summary="done", terminal="goal_done")
        proc = _make_popen([{"type": "result", "result": "ok"}])
        config_path = tmp_path / ".cursor" / "mcp.json"

        written_configs = []

        original_write = Path.write_text

        def capture_write(self, content, *args, **kwargs):
            if str(self) == str(config_path):
                written_configs.append(content)
            return original_write(self, content, *args, **kwargs)

        with (
            _base_patches(done),
            patch("subprocess.Popen", return_value=proc),
            patch.object(Path, "write_text", capture_write),
        ):
            _make_orch().cycle(
                goal="test", project_dir=tmp_path, team=MagicMock(), max_exchanges=5,
            )

        # At least one write should contain kodo_team
        assert any("kodo_team" in c for c in written_configs)

    def test_preserves_existing_mcp_config(self, tmp_path: Path):
        """Existing .cursor/mcp.json content is restored after cycle."""
        done = MagicMock(called=True, success=True, summary="done", terminal="goal_done")
        proc = _make_popen([{"type": "result", "result": "ok"}])

        cursor_dir = tmp_path / ".cursor"
        cursor_dir.mkdir()
        config_path = cursor_dir / "mcp.json"
        original = json.dumps({"mcpServers": {"other": {"url": "http://other"}}})
        config_path.write_text(original)

        with _base_patches(done), patch("subprocess.Popen", return_value=proc):
            _make_orch().cycle(
                goal="test", project_dir=tmp_path, team=MagicMock(), max_exchanges=5,
            )

        # After cycle, original config should be restored
        restored = config_path.read_text()
        assert restored == original

    def test_cleans_up_created_config(self, tmp_path: Path):
        """If no .cursor/mcp.json existed, kodo_team entry is removed after."""
        done = MagicMock(called=True, success=True, summary="done", terminal="goal_done")
        proc = _make_popen([{"type": "result", "result": "ok"}])

        with _base_patches(done), patch("subprocess.Popen", return_value=proc):
            _make_orch().cycle(
                goal="test", project_dir=tmp_path, team=MagicMock(), max_exchanges=5,
            )

        config_path = tmp_path / ".cursor" / "mcp.json"
        if config_path.exists():
            content = json.loads(config_path.read_text())
            assert "kodo_team" not in content.get("mcpServers", {})


# ── Error handling ──────────────────────────────────────────────────────


class TestCursorErrors:
    def test_nonzero_exit_uses_stderr(self, tmp_path: Path):
        """Non-zero exit with no result uses stderr text."""
        done = MagicMock(called=False, summary="", terminal=None, success=False)
        proc = _make_popen([], returncode=1, stderr_text="connection refused")

        with _base_patches(done), patch("subprocess.Popen", return_value=proc):
            result = _make_orch().cycle(
                goal="test", project_dir=tmp_path, team=MagicMock(), max_exchanges=5,
            )

        assert "connection refused" in result.summary

    def test_nonzero_exit_fallback(self, tmp_path: Path):
        """Non-zero exit with no stderr gets fallback message."""
        done = MagicMock(called=False, summary="", terminal=None, success=False)
        proc = _make_popen([], returncode=7, stderr_text="")

        with _base_patches(done), patch("subprocess.Popen", return_value=proc):
            result = _make_orch().cycle(
                goal="test", project_dir=tmp_path, team=MagicMock(), max_exchanges=5,
            )

        assert "7" in result.summary


# ── Construction ────────────────────────────────────────────────────────


class TestCursorConstruction:
    def test_default_model(self):
        from kodo.models import CURSOR_COMPOSER
        orch = CursorOrchestrator()
        assert orch.model == CURSOR_COMPOSER
        assert orch._orchestrator_name == "cursor"

    def test_custom_model(self):
        orch = CursorOrchestrator(model="claude-4-sonnet")
        assert orch.model == "claude-4-sonnet"
