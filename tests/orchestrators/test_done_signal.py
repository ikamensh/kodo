"""Tests for the done tool handler in ClaudeCodeOrchestrator's MCP server.

Note: verification logic (tester/architect pass/fail) is tested in test_verify_done.py.
These tests focus on the MCP done handler wiring and DoneSignal state management.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from kodo.orchestrators.base import CycleConfig, DoneSignal
from kodo.orchestrators.mcp_server import build_mcp_server
from kodo.orchestrators.verification import handle_done
from kodo.summarizer import Summarizer
from tests.conftest import make_agent


def _make_done_handler(team, project_dir, goal="Build X"):
    """Build the MCP server in legacy mode and extract the `done` handler function."""
    signal = DoneSignal()
    with (
        patch("kodo.summarizer._probe_ollama", return_value=None),
        patch("kodo.summarizer._probe_gemini", return_value=None),
    ):
        summarizer = Summarizer()
    mcp = build_mcp_server(
        team, project_dir, summarizer, signal, goal,
        config=CycleConfig(done_mode="legacy"),
    )

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

    def test_legacy_full_verification_rejects_on_tester_failure(self, tmp_project: Path) -> None:
        """Legacy mode with verification='full' runs verify_done gate;
        tester failure causes rejection."""
        team = {
            "worker": make_agent("done"),
            "tester": make_agent("ImportError: missing module"),
            "architect": make_agent("ALL CHECKS PASS"),
        }
        done_fn, signal = _make_done_handler(team, tmp_project)
        result = done_fn("Built everything", True)

        assert signal.called is False
        assert "rejected" in result.lower() or "fix" in result.lower()

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


class TestDoneHandlerQuickCheckRejection:
    def test_quick_check_rejection_tells_to_fix(self, tmp_project: Path) -> None:
        """Quick-check rejection still produces a fix-it message."""
        from kodo.orchestrators.base import QuickCheck

        team = {"tester": make_agent("broken")}
        done_signal = DoneSignal()
        checks = [QuickCheck(
            path=str(tmp_project / "nonexistent.md"),
            description="Required",
            error_message="Missing",
        )]
        result = handle_done(
            "All done", True, done_signal, "goal", team, tmp_project,
            config=CycleConfig(verification=checks),
        )

        assert "fix" in result.lower()
        assert done_signal.called is False


class TestNewDoneTools:
    """Tests for the new three-tool done mode (goal_done, end_cycle, raise_issue)."""

    def _make_new_tools(self, team, project_dir, goal="Build X"):
        """Build MCP server in new mode and extract tool handlers."""
        signal = DoneSignal()
        with (
            patch("kodo.summarizer._probe_ollama", return_value=None),
            patch("kodo.summarizer._probe_gemini", return_value=None),
        ):
            summarizer = Summarizer()
        mcp = build_mcp_server(
            team, project_dir, summarizer, signal, goal,
            config=CycleConfig(done_mode="new"),
        )
        tools = {}
        for tool_name, tool in mcp._tool_manager._tools.items():
            tools[tool_name] = tool.fn
        return tools, signal

    def test_new_mode_exposes_three_done_tools(self, tmp_project: Path) -> None:
        """New mode creates goal_done, end_cycle, raise_issue (not done)."""
        team = {"worker": make_agent("ok")}
        tools, _ = self._make_new_tools(team, tmp_project)
        assert "goal_done" in tools
        assert "end_cycle" in tools
        assert "raise_issue" in tools
        assert "done" not in tools

    def test_goal_done_sets_signal(self, tmp_project: Path) -> None:
        """goal_done sets signal.called/success/terminal correctly."""
        team = {"worker": make_agent("ok")}
        tools, signal = self._make_new_tools(team, tmp_project)
        result = tools["goal_done"]("All done")

        assert signal.called is True
        assert signal.success is True
        assert signal.summary == "All done"
        assert signal.terminal == "goal_done"
        assert "accepted" in result.lower()

    def test_end_cycle_sets_signal(self, tmp_project: Path) -> None:
        """end_cycle sets finished=False semantics via terminal field."""
        team = {"worker": make_agent("ok")}
        tools, signal = self._make_new_tools(team, tmp_project)
        result = tools["end_cycle"]("Made progress but need more work")

        assert signal.called is True
        assert signal.success is False
        assert signal.terminal == "end_cycle"
        assert "continue" in result.lower() or "cycle" in result.lower()

    def test_raise_issue_sets_signal(self, tmp_project: Path) -> None:
        """raise_issue sets terminal='raise_issue' for fatal errors."""
        team = {"worker": make_agent("ok")}
        tools, signal = self._make_new_tools(team, tmp_project)
        result = tools["raise_issue"]("Missing API credentials")

        assert signal.called is True
        assert signal.success is False
        assert signal.terminal == "raise_issue"
        assert "issue" in result.lower() or "stop" in result.lower()
