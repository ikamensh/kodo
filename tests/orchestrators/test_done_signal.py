"""Tests for the done tool handler in ClaudeCodeOrchestrator's MCP server.

Note: verification logic (tester/architect pass/fail) is tested in test_verify_done.py.
These tests focus on the MCP done handler wiring and DoneSignal state management.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from kodo.orchestrators.base import DoneSignal, build_mcp_server
from kodo.summarizer import Summarizer
from tests.conftest import make_agent


def _make_done_handler(team, project_dir, goal="Build X"):
    """Build the MCP server and extract the `done` handler function."""
    signal = DoneSignal()
    with (
        patch("kodo.summarizer._probe_ollama", return_value=None),
        patch("kodo.summarizer._probe_gemini", return_value=None),
    ):
        summarizer = Summarizer()
    mcp = build_mcp_server(team, project_dir, summarizer, signal, goal)

    # Extract the done handler from FastMCP's registered tools
    done_fn = None
    for tool_name, tool in mcp._tool_manager._tools.items():
        if tool_name == "done":
            done_fn = tool.fn
            break

    assert done_fn is not None, "done tool not found in MCP server"
    return done_fn, signal


class TestDoneHandlerAccepted:
    def test_accepted_sets_signal(self, tmp_project: Path) -> None:
        """On acceptance, signal.called/success/summary are set correctly."""
        team = {
            "worker": make_agent("done"),
            "tester": make_agent("ALL CHECKS PASS"),
            "architect": make_agent("ALL CHECKS PASS"),
        }
        done_fn, signal = _make_done_handler(team, tmp_project)
        result = done_fn("Built everything", True)

        assert signal.called is True
        assert signal.success is True
        assert signal.summary == "Built everything"
        assert "accepted" in result.lower() or "pass" in result.lower()

    def test_signal_not_set_on_rejection(self, tmp_project: Path) -> None:
        team = {
            "worker": make_agent("done"),
            "tester": make_agent("ImportError: missing module"),
            "architect": make_agent("ALL CHECKS PASS"),
        }
        done_fn, signal = _make_done_handler(team, tmp_project)
        result = done_fn("Built everything", True)

        assert signal.called is False
        assert "REJECTED" in result

    def test_unsuccessful_skips_verification(self, tmp_project: Path) -> None:
        """success=False bypasses verification entirely."""
        tester = make_agent("ALL CHECKS PASS")
        team = {
            "worker": make_agent("done"),
            "tester": tester,
        }
        done_fn, signal = _make_done_handler(team, tmp_project)

        with patch.object(tester, "run", wraps=tester.run) as mock_run:
            result = done_fn("Gave up, blocked on API key", False)
            mock_run.assert_not_called()

        assert signal.called is True
        assert signal.success is False
        assert "unsuccessful" in result.lower()


class TestDoneHandlerRejection:
    def test_rejection_tells_to_fix(self, tmp_project: Path) -> None:
        team = {"tester": make_agent("broken")}
        done_fn, signal = _make_done_handler(team, tmp_project)
        result = done_fn("All done", True)

        assert "fix" in result.lower()
        assert "done again" in result.lower()
