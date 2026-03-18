"""Integration tests for core goal execution and operating modes (US7-US10, US13-US16, US18)."""

import json
import re

import pytest

from tests.integration.cli_wrapper import run_kodo

pytestmark = pytest.mark.integration

# Increase timeout for execution tests — debug runs take ~5-15s
EXEC_TIMEOUT = 120


class TestGoalExecution:
    """US7: Goal execution with --debug."""

    def test_debug_run_exits_successfully(self) -> None:
        """A debug run with a simple goal should complete with exit code 0."""
        result = run_kodo("--debug", "--goal", "say hello", "--yes", timeout=EXEC_TIMEOUT)
        assert result.success(), f"Expected exit 0, got {result.exit_code}\n{result.output()}"

    def test_debug_run_shows_banner(self) -> None:
        """Debug run should display the kodo banner with version."""
        result = run_kodo("--debug", "--goal", "test goal", "--yes", timeout=EXEC_TIMEOUT)
        assert result.success()
        output = result.output()
        assert "kodo" in output.lower()
        assert "READY TO LAUNCH" in output

    def test_debug_run_shows_debug_mode(self) -> None:
        """Debug run should indicate DEBUG mode in output."""
        result = run_kodo("--debug", "--goal", "test goal", "--yes", timeout=EXEC_TIMEOUT)
        assert result.success()
        output = result.output()
        assert "DEBUG" in output
        assert "mocked backends" in output.lower() or "DEBUG MODE" in output

    def test_debug_run_shows_letter_assignments(self) -> None:
        """Debug run should show letter assignments for mock agents."""
        result = run_kodo("--debug", "--goal", "test goal", "--yes", timeout=EXEC_TIMEOUT)
        assert result.success()
        output = result.output()
        assert "Letter assignments" in output or "letter assignments" in output.lower()
        assert "A = orchestrator" in output

    def test_debug_run_shows_completion(self) -> None:
        """Debug run should show completion summary with cycle count."""
        result = run_kodo("--debug", "--goal", "test goal", "--yes", timeout=EXEC_TIMEOUT)
        assert result.success()
        output = result.output()
        # Should show "Done: N cycle(s)" or similar completion message
        assert re.search(r"Done:.*\d+\s+cycle", output), f"No completion summary found in:\n{output}"

    def test_debug_run_shows_debug_summary(self) -> None:
        """Debug run should print DEBUG SUMMARY at the end."""
        result = run_kodo("--debug", "--goal", "test goal", "--yes", timeout=EXEC_TIMEOUT)
        assert result.success()
        output = result.output()
        assert "DEBUG SUMMARY" in output

    def test_debug_run_shows_goal_in_banner(self) -> None:
        """The goal text should appear in the READY TO LAUNCH banner."""
        result = run_kodo("--debug", "--goal", "my unique goal text", "--yes", timeout=EXEC_TIMEOUT)
        assert result.success()
        output = result.output()
        assert "my unique goal text" in output


class TestTestMode:
    """US8: kodo test --debug."""

    def test_test_mode_exits_successfully(self) -> None:
        result = run_kodo("test", "--debug", "--yes", timeout=EXEC_TIMEOUT)
        assert result.success(), f"Exit {result.exit_code}\n{result.output()}"

    def test_test_mode_uses_test_team(self) -> None:
        """Test mode should default to 'test' team."""
        result = run_kodo("test", "--debug", "--yes", timeout=EXEC_TIMEOUT)
        assert result.success()
        output = result.output()
        assert "Team:" in output
        assert "test" in output.lower()

    def test_test_mode_has_stages(self) -> None:
        """Test mode should show stages in the plan."""
        result = run_kodo("test", "--debug", "--yes", timeout=EXEC_TIMEOUT)
        assert result.success()
        output = result.output()
        assert "Stages:" in output or "STAGE" in output

    def test_test_mode_shows_test_goal(self) -> None:
        """Test mode should show the test-related goal text."""
        result = run_kodo("test", "--debug", "--yes", timeout=EXEC_TIMEOUT)
        assert result.success()
        output = result.output()
        # TEST_GOAL starts with "Test this codebase"
        assert "Test this codebase" in output or "test" in output.lower()


class TestImproveMode:
    """US9: kodo improve --debug."""

    def test_improve_mode_exits_successfully(self) -> None:
        result = run_kodo("improve", "--debug", "--yes", timeout=EXEC_TIMEOUT)
        assert result.success(), f"Exit {result.exit_code}\n{result.output()}"

    def test_improve_mode_uses_full_team(self) -> None:
        """Improve mode should default to 'full' team."""
        result = run_kodo("improve", "--debug", "--yes", timeout=EXEC_TIMEOUT)
        assert result.success()
        output = result.output()
        # Should mention "full" team
        assert "full" in output.lower()

    def test_improve_mode_has_stages(self) -> None:
        """Improve mode should show stages in the plan."""
        result = run_kodo("improve", "--debug", "--yes", timeout=EXEC_TIMEOUT)
        assert result.success()
        output = result.output()
        assert "Stages:" in output or "STAGE" in output

    def test_improve_mode_shows_improve_goal(self) -> None:
        """Improve mode should show the improve-related goal text."""
        result = run_kodo("improve", "--debug", "--yes", timeout=EXEC_TIMEOUT)
        assert result.success()
        output = result.output()
        assert "Review this codebase" in output or "improve" in output.lower()


class TestJsonOutput:
    """US13: JSON output mode."""

    def test_json_output_is_valid_json(self) -> None:
        """--json flag should produce valid JSON on stdout."""
        result = run_kodo("--debug", "--goal", "test goal", "--yes", "--json", timeout=EXEC_TIMEOUT)
        assert result.success(), f"Exit {result.exit_code}\nstderr: {result.stderr}"
        data = json.loads(result.stdout)
        assert isinstance(data, dict)

    def test_json_output_has_required_fields(self) -> None:
        """JSON output should contain status, finished, cycles, exchanges, cost_usd, summary."""
        result = run_kodo("--debug", "--goal", "test goal", "--yes", "--json", timeout=EXEC_TIMEOUT)
        assert result.success()
        data = json.loads(result.stdout)
        assert data["status"] == "completed"
        assert data["finished"] is True
        assert isinstance(data["cycles"], int) and data["cycles"] >= 1
        assert isinstance(data["exchanges"], int) and data["exchanges"] >= 1
        assert isinstance(data["cost_usd"], (int, float))
        assert "summary" in data

    def test_json_debug_cost_is_zero(self) -> None:
        """Debug mode should report zero cost."""
        result = run_kodo("--debug", "--goal", "test goal", "--yes", "--json", timeout=EXEC_TIMEOUT)
        assert result.success()
        data = json.loads(result.stdout)
        assert data["cost_usd"] == 0.0

    def test_json_progress_goes_to_stderr(self) -> None:
        """With --json, progress output should go to stderr, not stdout."""
        result = run_kodo("--debug", "--goal", "test goal", "--yes", "--json", timeout=EXEC_TIMEOUT)
        assert result.success()
        # stdout should be pure JSON
        data = json.loads(result.stdout)
        assert isinstance(data, dict)
        # stderr should have the progress output
        assert "orchestrator" in result.stderr.lower() or "cycle" in result.stderr.lower()

    def test_json_error_output(self) -> None:
        """Errors with --json should produce JSON error object."""
        result = run_kodo("--debug", "--goal", "", "--yes", "--json", timeout=EXEC_TIMEOUT)
        assert result.exit_code != 0
        # Should produce JSON error on stdout
        try:
            data = json.loads(result.stdout)
            assert data["status"] == "error"
        except json.JSONDecodeError:
            # If no JSON is produced, the error message should be in output
            assert "empty" in result.output().lower() or "error" in result.output().lower()


class TestDebugMode:
    """US14: Debug mode verification."""

    def test_debug_flag_skips_api_keys(self) -> None:
        """Debug mode should not require any API keys."""
        # Run with no API key env vars (they shouldn't matter)
        result = run_kodo(
            "--debug", "--goal", "test goal", "--yes",
            env={"ANTHROPIC_API_KEY": "", "OPENAI_API_KEY": ""},
            timeout=EXEC_TIMEOUT,
        )
        assert result.success(), f"Debug mode should work without API keys:\n{result.output()}"

    def test_debug_run_uses_mock_sessions(self) -> None:
        """Debug output should show mock session identifiers (letter+number tokens)."""
        result = run_kodo("--debug", "--goal", "test goal", "--yes", timeout=EXEC_TIMEOUT)
        assert result.success()
        output = result.output()
        # Mock sessions generate tokens like A1, A2, B1, etc.
        assert re.search(r"[A-E]\d+", output), f"No mock tokens found in:\n{output}"


class TestNonInteractive:
    """US15: Non-interactive mode (--yes flag)."""

    def test_yes_flag_skips_confirmation(self) -> None:
        """--yes should skip the confirmation prompt and proceed directly."""
        result = run_kodo("--debug", "--goal", "test goal", "--yes", timeout=EXEC_TIMEOUT)
        assert result.success()
        # Should NOT ask "Proceed? [Y/n]" — it should just run
        assert "Proceed?" not in result.output()


class TestTeamSelection:
    """US16: Team selection."""

    def test_team_quick(self) -> None:
        """--team quick should use the quick team with fewer agents."""
        result = run_kodo("--debug", "--goal", "test goal", "--yes", "--team", "quick", timeout=EXEC_TIMEOUT)
        assert result.success()
        output = result.output()
        assert "quick" in output.lower()
        # Quick team should have fewer letter assignments (A, B, C only)
        assert "A = orchestrator" in output
        # Quick team should NOT have architect or tester
        lines = output.splitlines()
        letter_lines = [l for l in lines if re.match(r'\s+[A-Z] = ', l)]
        assert len(letter_lines) <= 4, f"Quick team should have fewer agents, got: {letter_lines}"

    def test_team_solo(self) -> None:
        """--team solo should use the solo team with minimal agents."""
        result = run_kodo("--debug", "--goal", "test goal", "--yes", "--team", "solo", timeout=EXEC_TIMEOUT)
        assert result.success()
        output = result.output()
        assert "solo" in output.lower()

    def test_team_full(self) -> None:
        """--team full should use the full team."""
        result = run_kodo("--debug", "--goal", "test goal", "--yes", "--team", "full", timeout=EXEC_TIMEOUT)
        assert result.success()
        output = result.output()
        assert "full" in output.lower()


class TestResumeMode:
    """US10: Resume capability (error cases only — no persistent run to resume)."""

    def test_resume_nonexistent_run(self) -> None:
        """Resuming a nonexistent run should fail with a clear error."""
        result = run_kodo("--resume", "nonexistent-run-id-12345", timeout=EXEC_TIMEOUT)
        assert result.exit_code != 0
        assert "not found" in result.output().lower() or "error" in result.output().lower()

    def test_resume_incompatible_with_goal(self) -> None:
        """--resume should be incompatible with --goal."""
        result = run_kodo("--resume", "--goal", "test", timeout=EXEC_TIMEOUT)
        assert result.exit_code != 0


class TestErrorHandling:
    """US18: Error handling for invalid inputs."""

    def test_empty_goal_rejected(self) -> None:
        """Empty goal string should be rejected."""
        result = run_kodo("--debug", "--goal", "", "--yes", timeout=EXEC_TIMEOUT)
        assert result.exit_code != 0
        assert "empty" in result.output().lower() or "error" in result.output().lower()

    def test_whitespace_goal_rejected(self) -> None:
        """Whitespace-only goal should be rejected."""
        result = run_kodo("--debug", "--goal", "   ", "--yes", timeout=EXEC_TIMEOUT)
        assert result.exit_code != 0

    def test_invalid_team_rejected(self) -> None:
        """Invalid team name should be rejected."""
        result = run_kodo("--debug", "--goal", "test", "--yes", "--team", "nonexistent_team_xyz", timeout=EXEC_TIMEOUT)
        assert result.exit_code != 0
