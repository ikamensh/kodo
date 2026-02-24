"""Integration tests for kodo --goal non-interactive workflow.

Verifies end-to-end flow: _main_inner with --goal --skip-intake --yes,
mocked launch_run, and edge cases (empty goal, mutual exclusion).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from kodo.cli._main import _main_inner
from kodo.orchestrators.base import RunResult, CycleResult


def _fake_run_result():
    """Minimal RunResult for mocked launch_run."""
    return RunResult(
        cycles=[
            CycleResult(
                exchanges=1,
                total_cost_usd=0.0,
                finished=True,
                summary="Done.",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Happy path: --goal --skip-intake --yes
# ---------------------------------------------------------------------------


class TestGoalNonInteractiveHappyPath:
    """--goal with --skip-intake --yes runs launch_run with correct args."""

    def test_goal_text_and_plan_passed_to_launch_run(self, tmp_path: Path):
        """goal_text is 'Hello world test', plan is None when --skip-intake."""
        with (
            patch("kodo.cli._params.has_claude", return_value=True),
            patch("kodo.cli._params.check_api_key", return_value=None),
            patch("kodo.cli._main.launch_run") as mock_launch,
        ):
            mock_launch.return_value = _fake_run_result()
            sys.argv = [
                "kodo",
                "--goal",
                "Hello world test",
                "--skip-intake",
                "--yes",
                str(tmp_path),
            ]
            _main_inner()

            mock_launch.assert_called_once()
            call_args = mock_launch.call_args
            run_dir, goal_text, params, *rest = call_args[0]
            plan = (
                call_args.kwargs.get("plan")
                if call_args.kwargs
                else (rest[0] if len(rest) > 0 else None)
            )

            assert goal_text == "Hello world test"
            assert plan is None

    def test_params_include_correct_defaults(self, tmp_path: Path):
        """Params have saga defaults when no overrides given."""
        with (
            patch("kodo.cli._params.has_claude", return_value=True),
            patch("kodo.cli._params.check_api_key", return_value=None),
            patch("kodo.cli._main.launch_run") as mock_launch,
        ):
            mock_launch.return_value = _fake_run_result()
            sys.argv = [
                "kodo",
                "--goal",
                "Hello world test",
                "--skip-intake",
                "--yes",
                str(tmp_path),
            ]
            _main_inner()

            params = mock_launch.call_args[0][2]
            assert params["team"] == "saga"
            assert params["max_exchanges"] == 30  # saga default
            assert params["max_cycles"] == 5  # saga default
            assert "orchestrator" in params
            assert "orchestrator_model" in params


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestGoalEdgeCases:
    """Empty goal and mutual exclusion of --goal/--goal-file."""

    def test_empty_goal_produces_error(self):
        """--goal '' should fail with clear error, not crash."""
        with (
            patch("kodo.cli._params.has_claude", return_value=True),
            patch("kodo.cli._params.check_api_key", return_value=None),
        ):
            sys.argv = ["kodo", "--goal", "", "--skip-intake", "--yes", "."]
            with pytest.raises(SystemExit):
                _main_inner()

    def test_goal_and_goal_file_mutually_exclusive(self):
        """--goal with --goal-file should fail (argparse mutual exclusion)."""
        with pytest.raises(SystemExit):
            sys.argv = ["kodo", "--goal", "X", "--goal-file", "goal.md", "."]
            _main_inner()
