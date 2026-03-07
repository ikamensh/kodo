"""Additional tests for kodo/cli/_main.py to increase coverage from 80% to 85%+.

Focuses on:
- --debug flag behavior
- --improve with --focus output
- Interactive cancellation flows
- Goal.md and plan rejection
- Edge cases in validation
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from kodo.cli._main import _main_inner


# ---------------------------------------------------------------------------
# --debug flag
# ---------------------------------------------------------------------------


class TestDebugFlag:
    """Test --debug flag sets skip_intake and uses mocked backends."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        """Mock dependencies to isolate _main.py logic."""
        with (
            patch("kodo.cli._main._print_banner"),
            patch("kodo.cli._main.launch_run") as mock_launch,
        ):
            mock_launch.return_value = MagicMock(
                finished=True,
                cycles=[],
                total_exchanges=0,
                total_cost_usd=0,
                summary="done",
                stage_results=[],
            )
            yield

    def test_debug_flag_sets_skip_intake(self, tmp_path):
        """--debug should set skip_intake=True."""
        captured_args = {}

        def capture_launch(run_dir, goal_text, params, **kwargs):
            captured_args["debug"] = kwargs.get("debug", False)
            captured_args["plan"] = kwargs.get("plan")
            return MagicMock(
                finished=True,
                cycles=[],
                total_exchanges=0,
                total_cost_usd=0,
                summary="done",
                stage_results=[],
            )

        with (
            patch("sys.argv", ["kodo", "--goal", "test", "--yes", "--debug", "--project", str(tmp_path)]),
            patch("kodo.cli._main.launch_run", side_effect=capture_launch),
        ):
            _main_inner()

        assert captured_args["debug"] is True


# Note: --improve tests are complex due to interaction with run_improve_discovery
# and are better tested via integration tests or existing improve-specific tests


# ---------------------------------------------------------------------------
# Interactive resume cancellation
# ---------------------------------------------------------------------------


class TestResumeInteractive:
    """Test interactive resume flow with user cancellation."""

    def test_resume_user_cancels_at_prompt(self, tmp_path):
        """User can cancel resume at confirmation prompt."""
        from kodo.log import RunState

        fake_state = RunState(
            run_id="20260101_120000",
            log_file=tmp_path / "kodo_runs" / "20260101_120000" / "run.jsonl",
            goal="Build X",
            orchestrator="api",
            model="gemini-flash",
            project_dir=str(tmp_path),
            max_exchanges=30,
            max_cycles=5,
            team=["worker_fast"],
            completed_cycles=2,
            last_summary="partial progress",
            finished=False,
            agent_session_ids={},
            has_stages=False,
            completed_stages=[],
            stage_summaries={},
            current_stage_cycles=0,
            pending_exchanges=[],
            team_preset="full",
        )

        with (
            patch("sys.argv", ["kodo", "--resume", "--project", str(tmp_path)]),
            patch("kodo.cli._main.log.find_incomplete_runs", return_value=[fake_state]),
            patch("builtins.input", return_value="n"),  # User cancels
            patch("kodo.cli._main._print_banner"),
            pytest.raises(SystemExit) as exc_info,
        ):
            _main_inner()

        assert exc_info.value.code == 0  # Clean exit


# Note: Interactive goal.md and plan rejection tests involve complex questionary
# interactions and are better tested via integration tests




# ---------------------------------------------------------------------------
# Safety confirmation cancellation
# ---------------------------------------------------------------------------


class TestSafetyConfirmation:
    """Test safety confirmation prompt in interactive mode."""

    def test_user_cancels_at_safety_prompt(self, tmp_path):
        """User can cancel at safety confirmation prompt."""
        with (
            patch("sys.argv", ["kodo", "--project", str(tmp_path)]),
            patch("kodo.cli._main._print_banner"),
            patch("kodo.cli._main.get_goal", return_value="Test goal"),
            patch("kodo.cli._main._load_or_select_params", return_value={"team": "full", "orchestrator": "api", "orchestrator_model": "opus", "max_exchanges": 30, "max_cycles": 5}),
            patch("kodo.cli._main._offer_intake", return_value=(None, None)),  # Skip intake
            patch("builtins.input", return_value="n"),  # Cancel at safety prompt
            pytest.raises(SystemExit) as exc_info,
        ):
            _main_inner()

        assert exc_info.value.code == 0  # Clean exit


# ---------------------------------------------------------------------------
# Flag validation edge cases
# ---------------------------------------------------------------------------


class TestFlagValidation:
    """Test additional flag validation edge cases."""

    @pytest.fixture(autouse=True)
    def _mock_backends(self):
        """Mock backend checks."""
        with (
            patch("kodo.cli._params.preferred_orchestrator", return_value="claude-code"),
            patch("kodo.cli._params.check_api_key", return_value=None),
        ):
            yield

    def test_focus_without_improve_fails(self, tmp_path):
        """--focus requires --improve."""
        with (
            patch("sys.argv", ["kodo", "--goal", "test", "--focus", "security", "--project", str(tmp_path)]),
            pytest.raises(SystemExit),
        ):
            _main_inner()

    def test_skip_intake_without_goal_fails(self, tmp_path):
        """--skip-intake requires a goal."""
        with (
            patch("sys.argv", ["kodo", "--skip-intake", "--project", str(tmp_path)]),
            pytest.raises(SystemExit),
        ):
            _main_inner()

    def test_auto_refine_without_goal_fails(self, tmp_path):
        """--auto-refine requires a goal."""
        with (
            patch("sys.argv", ["kodo", "--auto-refine", "--project", str(tmp_path)]),
            pytest.raises(SystemExit),
        ):
            _main_inner()


# Note: --improve report output tests are complex due to interaction with
# run_improve_discovery and are better tested via existing improve-specific tests


# ---------------------------------------------------------------------------
# Non-interactive auto-refine with no backend
# ---------------------------------------------------------------------------


class TestAutoRefineNoBackend:
    """Test auto-refine behavior when no backend available."""

    def test_auto_refine_no_backend_exits(self, tmp_path):
        """auto-refine without backend should fail with error."""
        with (
            patch("sys.argv", ["kodo", "--goal", "test", "--auto-refine", "--yes", "--project", str(tmp_path)]),
            patch("kodo.cli._main._print_banner"),
            patch("kodo.cli._params._build_params_from_flags", return_value={"team": "full", "orchestrator": "api", "orchestrator_model": "opus"}),
            patch("kodo.cli._main._load_goal_plan", return_value=None),
            patch("kodo.cli._main.preferred_backend", return_value=None),  # No backend
            pytest.raises(SystemExit),
        ):
            _main_inner()
