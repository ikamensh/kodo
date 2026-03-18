"""Tests for CodexOrchestrator — JSONL parsing, error handling, done signal."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch


from kodo.orchestrators.codex_cli import CodexOrchestrator


# ── Helpers ─────────────────────────────────────────────────────────────


@contextlib.contextmanager
def _base_patches(done_signal):
    """Stub out MCP server, logging, and build_cycle_prompt."""
    mock_mcp = MagicMock()
    mock_ctx = MagicMock()
    mock_ctx.sse_url = "http://127.0.0.1:9999/sse"
    mock_ctx.stdio_bridge_cmd = ["uvx", "supergateway", "--sse-url", mock_ctx.sse_url]
    mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
    mock_ctx.__exit__ = MagicMock(return_value=False)

    with (
        patch(
            "kodo.orchestrators.cli_base.build_mcp_server",
            autospec=True,
            return_value=mock_mcp,
        ),
        patch(
            "kodo.orchestrators.cli_base.McpServerContext",
            autospec=True,
            return_value=mock_ctx,
        ),
        patch(
            "kodo.orchestrators.cli_base.build_cycle_prompt",
            autospec=True,
            return_value="go",
        ),
        patch(
            "kodo.orchestrators.cli_base.DoneSignal",
            autospec=True,
            return_value=done_signal,
        ),
        patch("kodo.orchestrators.cli_base.VerificationState", autospec=True),
        patch("kodo.orchestrators.cli_base.log", autospec=True),
        patch("kodo.orchestrators.codex_cli.log", autospec=True),
    ):
        yield


def _make_popen(stdout_lines, returncode=0, stderr_text=""):
    """Build a mock Popen that yields stdout_lines as JSONL."""
    mock_proc = MagicMock()
    mock_proc.stdout = iter(
        [
            json.dumps(line) + "\n" if isinstance(line, dict) else line + "\n"
            for line in stdout_lines
        ]
    )
    mock_proc.stderr = MagicMock()
    mock_proc.stderr.read.return_value = stderr_text
    mock_proc.returncode = returncode
    mock_proc.wait.return_value = returncode
    mock_proc.poll.return_value = returncode
    return mock_proc


def _make_orch():
    return CodexOrchestrator(model="codex-test")


# ── JSONL parsing ───────────────────────────────────────────────────────


class TestCodexJsonlParsing:
    def test_task_started_increments_exchanges(self, tmp_path: Path):
        """Each task_started message increments the exchange counter."""
        done = MagicMock(
            called=True, success=True, summary="done", terminal="goal_done"
        )
        proc = _make_popen(
            [
                {"type": "task_started"},
                {"type": "task_started"},
                {"type": "message", "msg": {"type": "message", "content": "hello"}},
            ]
        )

        with (
            _base_patches(done),
            patch("subprocess.Popen", autospec=True, return_value=proc),
        ):
            result = _make_orch().cycle(
                goal="test",
                project_dir=tmp_path,
                team=MagicMock(spec=dict),
                max_exchanges=5,
            )

        assert result.exchanges >= 2

    def test_task_complete_captures_message(self, tmp_path: Path):
        """task_complete message text is captured as response."""
        done = MagicMock(called=False, summary="", terminal=None, success=False)
        proc = _make_popen(
            [
                {"type": "task_started"},
                {
                    "type": "task_complete",
                    "msg": {"type": "task_complete", "message": "All done!"},
                },
            ]
        )

        with (
            _base_patches(done),
            patch("subprocess.Popen", autospec=True, return_value=proc),
        ):
            result = _make_orch().cycle(
                goal="test",
                project_dir=tmp_path,
                team=MagicMock(spec=dict),
                max_exchanges=5,
            )

        assert "All done!" in result.summary

    def test_message_content_captured(self, tmp_path: Path):
        """message type with content is captured."""
        done = MagicMock(called=False, summary="", terminal=None, success=False)
        proc = _make_popen(
            [
                {"type": "task_started"},
                {
                    "type": "message",
                    "msg": {"type": "message", "content": "Working on it"},
                },
            ]
        )

        with (
            _base_patches(done),
            patch("subprocess.Popen", autospec=True, return_value=proc),
        ):
            result = _make_orch().cycle(
                goal="test",
                project_dir=tmp_path,
                team=MagicMock(spec=dict),
                max_exchanges=5,
            )

        assert "Working on it" in result.summary

    def test_malformed_json_lines_skipped(self, tmp_path: Path):
        """Non-JSON lines in stdout are silently skipped."""
        done = MagicMock(called=False, summary="", terminal=None, success=False)
        proc = _make_popen(
            [
                "not valid json",
                {"type": "task_started"},
                "",  # blank line (will add \n, still blank after strip)
                {"type": "message", "msg": {"type": "message", "content": "ok"}},
            ]
        )

        with (
            _base_patches(done),
            patch("subprocess.Popen", autospec=True, return_value=proc),
        ):
            result = _make_orch().cycle(
                goal="test",
                project_dir=tmp_path,
                team=MagicMock(spec=dict),
                max_exchanges=5,
            )

        # Should not crash, should capture "ok"
        assert "ok" in result.summary


# ── Error handling ──────────────────────────────────────────────────────


class TestCodexErrors:
    def test_nonzero_exit_uses_stderr(self, tmp_path: Path):
        """Non-zero exit with no response uses stderr text."""
        done = MagicMock(called=False, summary="", terminal=None, success=False)
        proc = _make_popen([], returncode=1, stderr_text="model not found")

        with (
            _base_patches(done),
            patch("subprocess.Popen", autospec=True, return_value=proc),
        ):
            result = _make_orch().cycle(
                goal="test",
                project_dir=tmp_path,
                team=MagicMock(spec=dict),
                max_exchanges=5,
            )

        assert "model not found" in result.summary

    def test_nonzero_exit_fallback_message(self, tmp_path: Path):
        """Non-zero exit with no stderr gets a fallback error message."""
        done = MagicMock(called=False, summary="", terminal=None, success=False)
        proc = _make_popen([], returncode=42, stderr_text="")

        with (
            _base_patches(done),
            patch("subprocess.Popen", autospec=True, return_value=proc),
        ):
            result = _make_orch().cycle(
                goal="test",
                project_dir=tmp_path,
                team=MagicMock(spec=dict),
                max_exchanges=5,
            )

        assert "42" in result.summary


# ── Done signal integration ─────────────────────────────────────────────


class TestCodexDoneSignal:
    def test_done_signal_overrides_response(self, tmp_path: Path):
        """When done_signal.called is True, its summary is used."""
        done = MagicMock(
            called=True, success=True, summary="done via tool", terminal="goal_done"
        )
        proc = _make_popen(
            [
                {"type": "task_started"},
                {
                    "type": "message",
                    "msg": {"type": "message", "content": "subprocess output"},
                },
            ]
        )

        with (
            _base_patches(done),
            patch("subprocess.Popen", autospec=True, return_value=proc),
        ):
            result = _make_orch().cycle(
                goal="test",
                project_dir=tmp_path,
                team=MagicMock(spec=dict),
                max_exchanges=5,
            )

        assert result.finished is True
        assert "done via tool" in result.summary


# ── Construction ────────────────────────────────────────────────────────


class TestCodexConstruction:
    def test_default_model(self):
        from kodo.models import CODEX_DEFAULT

        orch = CodexOrchestrator()
        assert orch.model == CODEX_DEFAULT
        assert orch._orchestrator_name == "codex"

    def test_custom_model(self):
        orch = CodexOrchestrator(model="o3-mini")
        assert orch.model == "o3-mini"
