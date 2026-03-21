"""Integration tests for execution modes — JSON contract, error paths, side effects."""

import json
import re

import pytest

from tests.integration.cli_wrapper import run_kodo

pytestmark = pytest.mark.integration

EXEC_TIMEOUT = 120


class TestJsonOutput:
    """JSON output contract tests."""

    def test_json_output_has_required_fields(self) -> None:
        result = run_kodo(
            "--debug", "--goal", "test goal", "--yes", "--json", timeout=EXEC_TIMEOUT
        )
        assert result.success()
        data = json.loads(result.stdout)
        assert data["status"] == "completed"
        assert data["finished"] is True
        assert isinstance(data["cycles"], int) and data["cycles"] >= 1
        assert isinstance(data["exchanges"], int) and data["exchanges"] >= 1
        assert isinstance(data["cost_usd"], (int, float))
        assert "summary" in data

    def test_json_debug_cost_is_zero(self) -> None:
        result = run_kodo(
            "--debug", "--goal", "test goal", "--yes", "--json", timeout=EXEC_TIMEOUT
        )
        assert result.success()
        data = json.loads(result.stdout)
        assert data["cost_usd"] == 0.0

    def test_json_progress_goes_to_stderr(self) -> None:
        result = run_kodo(
            "--debug", "--goal", "test goal", "--yes", "--json", timeout=EXEC_TIMEOUT
        )
        assert result.success()
        # stdout should be pure JSON
        json.loads(result.stdout)
        # stderr should have the progress output
        assert (
            "orchestrator" in result.stderr.lower() or "cycle" in result.stderr.lower()
        )

    def test_json_error_output(self) -> None:
        result = run_kodo(
            "--debug", "--goal", "", "--yes", "--json", timeout=EXEC_TIMEOUT
        )
        assert result.exit_code != 0
        try:
            data = json.loads(result.stdout)
            assert data["status"] == "error"
        except json.JSONDecodeError:
            assert (
                "empty" in result.output().lower() or "error" in result.output().lower()
            )


class TestErrorHandling:
    """Input validation error paths."""

    def test_empty_goal_rejected(self) -> None:
        result = run_kodo("--debug", "--goal", "", "--yes", timeout=EXEC_TIMEOUT)
        assert result.exit_code != 0

    def test_whitespace_goal_rejected(self) -> None:
        result = run_kodo("--debug", "--goal", "   ", "--yes", timeout=EXEC_TIMEOUT)
        assert result.exit_code != 0

    def test_invalid_team_rejected(self) -> None:
        result = run_kodo(
            "--debug",
            "--goal",
            "test",
            "--yes",
            "--team",
            "nonexistent_team_xyz",
            timeout=EXEC_TIMEOUT,
        )
        assert result.exit_code != 0

    def test_resume_nonexistent_run(self) -> None:
        result = run_kodo("--resume", "nonexistent-run-id-12345", timeout=EXEC_TIMEOUT)
        assert result.exit_code != 0

    def test_resume_incompatible_with_goal(self) -> None:
        result = run_kodo("--resume", "--goal", "test", timeout=EXEC_TIMEOUT)
        assert result.exit_code != 0


class TestSideEffects:
    """Verify observable side effects of a run."""

    def test_debug_run_creates_run_directory(self) -> None:
        run_kodo("--debug", "--goal", "config test", "--yes", timeout=EXEC_TIMEOUT)
        result = run_kodo("runs", timeout=30)
        assert result.success()
        assert re.search(r"\d{8}_\d{6}", result.output()), "No run ID in output"
