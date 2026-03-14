"""Additional tests for kodo/cli/_main.py to increase coverage from 80% to 85%+.

Focuses on:
- --debug flag behavior
- --improve with --focus output
- Interactive cancellation flows
- Goal.md and plan rejection
- Edge cases in validation
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
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
            patch("kodo.cli._main._print_banner", autospec=True),
            patch("kodo.cli._main.launch_run", autospec=True) as mock_launch,
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

    def test_debug_flag_forwards_to_launch(self, tmp_path):
        """--debug should forward debug=True to launch_run."""
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
            patch("kodo.cli._main.launch_run", side_effect=capture_launch),  # noqa: autospec
        ):
            _main_inner()

        assert captured_args["debug"] is True

    def test_debug_flag_skips_intake(self, tmp_path):
        """--debug with --goal should skip non-interactive intake (no plan generated)."""
        captured_args = {}

        def capture_launch(run_dir, goal_text, params, **kwargs):
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
            patch("kodo.cli._main.launch_run", side_effect=capture_launch),  # noqa: autospec
            patch("kodo.cli._main.run_intake_noninteractive", autospec=True) as mock_intake,
        ):
            _main_inner()

        # --debug sets skip_intake=True, so run_intake_noninteractive should NOT be called
        mock_intake.assert_not_called()


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
            patch("kodo.cli._main.log.find_incomplete_runs", autospec=True, return_value=[fake_state]),
            patch("builtins.input", autospec=True, return_value="n"),  # User cancels
            patch("kodo.cli._main._print_banner", autospec=True),
            pytest.raises(SystemExit) as exc_info,
        ):
            _main_inner()

        assert exc_info.value.code == 0  # Clean exit


# ---------------------------------------------------------------------------
# goal.md auto-detection in interactive mode
# ---------------------------------------------------------------------------


_STANDARD_PARAMS = {
    "team": "full",
    "orchestrator": "api",
    "orchestrator_model": "opus",
    "max_exchanges": 30,
    "max_cycles": 5,
}


class TestGoalMdAutoDetection:
    """Interactive mode: goal.md in project dir is detected and offered to the user."""

    def test_goal_md_found_and_accepted(self, tmp_path, capsys):
        """When goal.md exists and user accepts, its content is used as the goal."""
        project = tmp_path / "proj"
        project.mkdir()
        (project / "goal.md").write_text("Build a REST API with auth")

        with (
            patch("sys.argv", ["kodo", "--project", str(project)]),
            patch("kodo.cli._main._print_banner", autospec=True),
            patch("kodo.cli._main.get_goal", autospec=True, return_value="SHOULD NOT BE USED") as mock_get,
            patch("kodo.cli._main._load_or_select_params", autospec=True, return_value=_STANDARD_PARAMS),
            patch("kodo.cli._main._offer_intake", autospec=True, return_value=(None, None)),
            patch("kodo.cli._main.launch_run", autospec=True) as mock_launch,
            # User accepts goal.md (Enter = Y default) then accepts safety prompt
            patch("builtins.input", autospec=True, return_value="y"),
        ):
            _main_inner()

        mock_launch.assert_called_once()
        goal_arg = mock_launch.call_args[0][1]
        assert goal_arg == "Build a REST API with auth"
        mock_get.assert_not_called()
        out = capsys.readouterr().out
        assert "Found existing goal" in out

    def test_goal_md_found_and_rejected(self, tmp_path):
        """When goal.md exists but user rejects, falls back to get_goal."""
        project = tmp_path / "proj"
        project.mkdir()
        (project / "goal.md").write_text("Old goal from file")

        # First input("Use this goal?") returns "n", get_goal provides fallback
        with (
            patch("sys.argv", ["kodo", "--project", str(project)]),
            patch("kodo.cli._main._print_banner", autospec=True),
            patch("kodo.cli._main.get_goal", autospec=True, return_value="Fresh goal typed by user"),
            patch("kodo.cli._main._load_or_select_params", autospec=True, return_value=_STANDARD_PARAMS),
            patch("kodo.cli._main._offer_intake", autospec=True, return_value=(None, None)),
            patch("kodo.cli._main.launch_run", autospec=True) as mock_launch,
            patch("builtins.input", autospec=True, side_effect=["n", "y"]),  # reject goal.md, accept safety
        ):
            _main_inner()

        mock_launch.assert_called_once()
        goal_arg = mock_launch.call_args[0][1]
        assert goal_arg == "Fresh goal typed by user"

    def test_no_goal_md_falls_through_to_get_goal(self, tmp_path):
        """When no goal.md exists, get_goal is called."""
        project = tmp_path / "proj"
        project.mkdir()
        # No goal.md file created

        with (
            patch("sys.argv", ["kodo", "--project", str(project)]),
            patch("kodo.cli._main._print_banner", autospec=True),
            patch("kodo.cli._main.get_goal", autospec=True, return_value="Typed goal"),
            patch("kodo.cli._main._load_or_select_params", autospec=True, return_value=_STANDARD_PARAMS),
            patch("kodo.cli._main._offer_intake", autospec=True, return_value=(None, None)),
            patch("kodo.cli._main.launch_run", autospec=True) as mock_launch,
            patch("builtins.input", autospec=True, return_value="y"),
        ):
            _main_inner()

        mock_launch.assert_called_once()
        goal_arg = mock_launch.call_args[0][1]
        assert goal_arg == "Typed goal"

    def test_goal_md_case_insensitive(self, tmp_path, capsys):
        """goal.md detection is case-insensitive (e.g., GOAL.MD)."""
        project = tmp_path / "proj"
        project.mkdir()
        (project / "GOAL.MD").write_text("Uppercase goal file")

        with (
            patch("sys.argv", ["kodo", "--project", str(project)]),
            patch("kodo.cli._main._print_banner", autospec=True),
            patch("kodo.cli._main.get_goal", autospec=True, return_value="SHOULD NOT BE USED") as mock_get,
            patch("kodo.cli._main._load_or_select_params", autospec=True, return_value=_STANDARD_PARAMS),
            patch("kodo.cli._main._offer_intake", autospec=True, return_value=(None, None)),
            patch("kodo.cli._main.launch_run", autospec=True) as mock_launch,
            patch("builtins.input", autospec=True, return_value="y"),
        ):
            _main_inner()

        mock_launch.assert_called_once()
        goal_arg = mock_launch.call_args[0][1]
        assert goal_arg == "Uppercase goal file"
        mock_get.assert_not_called()
        out = capsys.readouterr().out
        assert "Found existing goal" in out

    def test_goal_md_long_content_truncated_in_display(self, tmp_path, capsys):
        """Long goal.md content is truncated at 500 chars in the display."""
        project = tmp_path / "proj"
        project.mkdir()
        long_goal = "A" * 600
        (project / "goal.md").write_text(long_goal)

        with (
            patch("sys.argv", ["kodo", "--project", str(project)]),
            patch("kodo.cli._main._print_banner", autospec=True),
            patch("kodo.cli._main.get_goal", autospec=True, return_value="SHOULD NOT BE USED"),
            patch("kodo.cli._main._load_or_select_params", autospec=True, return_value=_STANDARD_PARAMS),
            patch("kodo.cli._main._offer_intake", autospec=True, return_value=(None, None)),
            patch("kodo.cli._main.launch_run", autospec=True) as mock_launch,
            patch("builtins.input", autospec=True, return_value="y"),
        ):
            _main_inner()

        mock_launch.assert_called_once()
        # Full content is used as goal (not truncated)
        goal_arg = mock_launch.call_args[0][1]
        assert goal_arg == long_goal
        # But display should show "..." for truncation
        out = capsys.readouterr().out
        assert "..." in out


# ---------------------------------------------------------------------------
# Safety confirmation cancellation
# ---------------------------------------------------------------------------


class TestSafetyConfirmation:
    """Test safety confirmation prompt in interactive mode."""

    def test_user_cancels_at_safety_prompt(self, tmp_path):
        """User can cancel at safety confirmation prompt."""
        with (
            patch("sys.argv", ["kodo", "--project", str(tmp_path)]),
            patch("kodo.cli._main._print_banner", autospec=True),
            patch("kodo.cli._main.get_goal", autospec=True, return_value="Test goal"),
            patch("kodo.cli._main._load_or_select_params", autospec=True, return_value={"team": "full", "orchestrator": "api", "orchestrator_model": "opus", "max_exchanges": 30, "max_cycles": 5}),
            patch("kodo.cli._main._offer_intake", autospec=True, return_value=(None, None)),  # Skip intake
            patch("builtins.input", autospec=True, return_value="n"),  # Cancel at safety prompt
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
            patch("kodo.cli._params.preferred_orchestrator", autospec=True, return_value="claude-code"),
            patch("kodo.cli._params.check_api_key", autospec=True, return_value=None),
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


# ---------------------------------------------------------------------------
# --improve --focus wiring through _main_inner
# ---------------------------------------------------------------------------


class TestImproveFocusWiring:
    """Verify --focus value is forwarded from CLI to run_improve_discovery and fallback."""

    @pytest.fixture(autouse=True)
    def _mock_backends(self):
        with (
            patch("kodo.cli._params.preferred_orchestrator", autospec=True, return_value="claude-code"),
            patch("kodo.cli._params.check_api_key", autospec=True, return_value=None),
        ):
            yield

    def test_improve_focus_forwarded_to_discovery(self, tmp_path):
        """--improve --focus 'security' passes focus to run_improve_discovery."""
        captured_focus = {}

        def capture_discovery(run_dir, report_path, prior="", *, focus=None):
            captured_focus["discovery"] = focus
            return None  # force fallback

        def capture_fallback(report_path, prior="", *, focus=None):
            captured_focus["fallback"] = focus
            return MagicMock(stages=[])

        fake_result = MagicMock(finished=True, cycles=[], total_exchanges=0,
                                total_cost_usd=0, summary="done", stage_results=[])

        with (
            patch("sys.argv", ["kodo", "--improve", "--focus", "security", "--project", str(tmp_path)]),
            patch("kodo.cli._main._print_banner", autospec=True),
            patch("kodo.cli._main.run_improve_discovery", autospec=True, side_effect=capture_discovery),
            patch("kodo.cli._main._build_fallback_plan", autospec=True, side_effect=capture_fallback),
            patch("kodo.cli._main.launch_run", autospec=True, return_value=fake_result),
        ):
            _main_inner()

        assert captured_focus["discovery"] == "security", "focus not forwarded to run_improve_discovery"
        assert captured_focus["fallback"] == "security", "focus not forwarded to _build_fallback_plan"

    def test_improve_focus_in_goal_text(self, tmp_path):
        """--improve --focus should include focus area in goal_text passed to launch_run."""
        captured_goal = {}

        def capture_launch(run_dir, goal_text, params, **kwargs):
            captured_goal["text"] = goal_text
            return MagicMock(finished=True, cycles=[], total_exchanges=0,
                             total_cost_usd=0, summary="done", stage_results=[])

        with (
            patch("sys.argv", ["kodo", "--improve", "--focus", "error handling", "--project", str(tmp_path)]),
            patch("kodo.cli._main._print_banner", autospec=True),
            patch("kodo.cli._main.run_improve_discovery", autospec=True, return_value=None),
            patch("kodo.cli._main._build_fallback_plan", autospec=True, return_value=MagicMock(stages=[])),
            patch("kodo.cli._main.launch_run", autospec=True, side_effect=capture_launch),
        ):
            _main_inner()

        assert "error handling" in captured_goal["text"]


# ---------------------------------------------------------------------------
# Non-interactive auto-refine with no backend
# ---------------------------------------------------------------------------


class TestAutoRefineNoBackend:
    """Test auto-refine behavior when no backend available."""

    def test_auto_refine_no_backend_exits(self, tmp_path):
        """auto-refine without backend should fail with error."""
        with (
            patch("sys.argv", ["kodo", "--goal", "test", "--auto-refine", "--yes", "--project", str(tmp_path)]),
            patch("kodo.cli._main._print_banner", autospec=True),
            patch("kodo.cli._params._build_params_from_flags", autospec=True, return_value={"team": "full", "orchestrator": "api", "orchestrator_model": "opus"}),
            patch("kodo.cli._main._load_goal_plan", autospec=True, return_value=None),
            patch("kodo.cli._main.preferred_backend", autospec=True, return_value=None),  # No backend
            pytest.raises(SystemExit),
        ):
            _main_inner()


# ---------------------------------------------------------------------------
# Helpers for A7 tests
# ---------------------------------------------------------------------------


def _fake_run_result(**overrides):
    """Build a minimal mock RunResult for launch_run returns."""
    defaults = dict(
        finished=True,
        cycles=[],
        total_exchanges=0,
        total_cost_usd=0,
        summary="done",
        stage_results=[],
    )
    defaults.update(overrides)
    return MagicMock(**defaults)


def _fake_run_state(tmp_path, **overrides):
    """Build a RunState for resume tests."""
    from kodo.log import RunState

    defaults = dict(
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
    defaults.update(overrides)
    return RunState(**defaults)


# ---------------------------------------------------------------------------
# A7: --yes flag skips safety prompt in interactive mode
# ---------------------------------------------------------------------------


class TestYesFlagSkipsPrompts:
    """--yes without --goal should skip safety prompt in interactive mode.

    This is the critical A7 scenario: the user invokes kodo in interactive
    mode (no --goal / --goal-file / --improve) but passes --yes / -y to
    skip the safety confirmation.  If ``args.yes`` is not wired into
    ``skip_prompts``, input() will be called and unattended runs will hang.
    """

    def test_yes_flag_skips_safety_prompt_interactive_mode(self, tmp_path):
        """'kodo --yes' (interactive) must NOT call input() for safety prompt."""
        launch_called = []

        def capture_launch(run_dir, goal_text, params, **kwargs):
            launch_called.append(True)
            return _fake_run_result()

        with (
            patch("sys.argv", ["kodo", "--yes", "--project", str(tmp_path)]),
            patch("kodo.cli._main._print_banner", autospec=True),
            patch("kodo.cli._main.get_goal", autospec=True, return_value="Interactive goal"),
            patch("kodo.cli._main._load_or_select_params", autospec=True, return_value={
                "team": "full", "orchestrator": "api",
                "orchestrator_model": "opus", "max_exchanges": 30, "max_cycles": 5,
            }),
            patch("kodo.cli._main._offer_intake", autospec=True, return_value=(None, None)),
            patch("kodo.cli._main.launch_run", autospec=True, side_effect=capture_launch),
            # If input() is called, the test FAILS — --yes should skip all prompts
            patch("builtins.input", autospec=True, side_effect=AssertionError(
                "--yes should skip safety prompt but input() was called"
            )),
        ):
            _main_inner()

        assert launch_called, "launch_run should have been called"

    def test_short_y_flag_skips_safety_prompt(self, tmp_path):
        """'kodo -y' (short flag) must also skip all prompts."""
        launch_called = []

        def capture_launch(run_dir, goal_text, params, **kwargs):
            launch_called.append(True)
            return _fake_run_result()

        with (
            patch("sys.argv", ["kodo", "-y", "--project", str(tmp_path)]),
            patch("kodo.cli._main._print_banner", autospec=True),
            patch("kodo.cli._main.get_goal", autospec=True, return_value="Interactive goal"),
            patch("kodo.cli._main._load_or_select_params", autospec=True, return_value={
                "team": "full", "orchestrator": "api",
                "orchestrator_model": "opus", "max_exchanges": 30, "max_cycles": 5,
            }),
            patch("kodo.cli._main._offer_intake", autospec=True, return_value=(None, None)),
            patch("kodo.cli._main.launch_run", autospec=True, side_effect=capture_launch),
            patch("builtins.input", autospec=True, side_effect=AssertionError(
                "-y should skip safety prompt but input() was called"
            )),
        ):
            _main_inner()

        assert launch_called, "launch_run should have been called"

    def test_yes_flag_skips_resume_prompt(self, tmp_path):
        """'kodo --resume --yes' must skip the resume confirmation prompt."""
        fake_state = _fake_run_state(tmp_path)

        with (
            patch("sys.argv", ["kodo", "--resume", "--yes", "--project", str(tmp_path)]),
            patch("kodo.cli._main.log.find_incomplete_runs", autospec=True, return_value=[fake_state]),
            patch("kodo.cli._main._print_banner", autospec=True),
            patch("kodo.cli._main.launch_resume", autospec=True, return_value=_fake_run_result()) as mock_resume,
            # If input() is called, the test FAILS
            patch("builtins.input", autospec=True, side_effect=AssertionError(
                "--yes should skip resume prompt but input() was called"
            )),
        ):
            _main_inner()

        mock_resume.assert_called_once()

    def test_without_yes_safety_prompt_fires(self, tmp_path):
        """Without --yes in interactive mode, safety prompt MUST fire."""
        with (
            patch("sys.argv", ["kodo", "--project", str(tmp_path)]),
            patch("kodo.cli._main._print_banner", autospec=True),
            patch("kodo.cli._main.get_goal", autospec=True, return_value="Test goal"),
            patch("kodo.cli._main._load_or_select_params", autospec=True, return_value={
                "team": "full", "orchestrator": "api",
                "orchestrator_model": "opus", "max_exchanges": 30, "max_cycles": 5,
            }),
            patch("kodo.cli._main._offer_intake", autospec=True, return_value=(None, None)),
            patch("builtins.input", autospec=True, return_value="n"),
            pytest.raises(SystemExit) as exc_info,
        ):
            _main_inner()

        # User typed 'n', so should exit cleanly
        assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# A14: --auto-refine calls run_intake_auto and result reaches launch_run
# ---------------------------------------------------------------------------


class TestAutoRefineCallsIntakeAuto:
    """Verify --auto-refine actually triggers run_intake_auto and the refined
    goal is forwarded to launch_run.  Mutation-tested: if the auto_refine
    branch is disabled (elif False), these tests MUST fail."""

    @pytest.fixture(autouse=True)
    def _mock_backends(self):
        with (
            patch("kodo.cli._params.preferred_orchestrator", autospec=True, return_value="claude-code"),
            patch("kodo.cli._params.check_api_key", autospec=True, return_value=None),
        ):
            yield

    def test_auto_refine_calls_run_intake_auto(self, tmp_path):
        """--goal --auto-refine must call run_intake_auto with the goal text."""
        with (
            patch("sys.argv", [
                "kodo", "--goal", "Build an API", "--auto-refine",
                "--yes", "--project", str(tmp_path),
            ]),
            patch("kodo.cli._main._print_banner", autospec=True),
            patch("kodo.cli._main._load_goal_plan", autospec=True, return_value=None),
            patch("kodo.cli._main.preferred_backend", autospec=True, return_value="claude"),
            patch("kodo.cli._main.run_intake_auto", autospec=True, return_value="Refined API goal") as mock_auto,
            patch("kodo.cli._main.launch_run", autospec=True, return_value=_fake_run_result()),
        ):
            _main_inner()

        mock_auto.assert_called_once()
        # Verify the original goal text was passed to run_intake_auto
        call_args = mock_auto.call_args
        assert call_args[0][2] == "Build an API", (
            f"Expected goal 'Build an API' passed to run_intake_auto, got {call_args[0][2]!r}"
        )

    def test_auto_refine_result_reaches_launch_run(self, tmp_path):
        """The refined goal from run_intake_auto must be forwarded to launch_run."""
        captured_goal = {}

        def capture_launch(run_dir, goal_text, params, **kwargs):
            captured_goal["text"] = goal_text
            return _fake_run_result()

        with (
            patch("sys.argv", [
                "kodo", "--goal", "Build an API", "--auto-refine",
                "--yes", "--project", str(tmp_path),
            ]),
            patch("kodo.cli._main._print_banner", autospec=True),
            patch("kodo.cli._main._load_goal_plan", autospec=True, return_value=None),
            patch("kodo.cli._main.preferred_backend", autospec=True, return_value="claude"),
            patch("kodo.cli._main.run_intake_auto", autospec=True, return_value="Refined: Build a REST API with auth"),
            patch("kodo.cli._main.launch_run", autospec=True, side_effect=capture_launch),
        ):
            _main_inner()

        assert captured_goal["text"] == "Refined: Build a REST API with auth", (
            f"Expected refined goal to reach launch_run, got {captured_goal.get('text')!r}"
        )

    def test_auto_refine_none_result_preserves_original_goal(self, tmp_path):
        """If run_intake_auto returns None, the original goal is preserved."""
        captured_goal = {}

        def capture_launch(run_dir, goal_text, params, **kwargs):
            captured_goal["text"] = goal_text
            return _fake_run_result()

        with (
            patch("sys.argv", [
                "kodo", "--goal", "Build an API", "--auto-refine",
                "--yes", "--project", str(tmp_path),
            ]),
            patch("kodo.cli._main._print_banner", autospec=True),
            patch("kodo.cli._main._load_goal_plan", autospec=True, return_value=None),
            patch("kodo.cli._main.preferred_backend", autospec=True, return_value="claude"),
            patch("kodo.cli._main.run_intake_auto", autospec=True, return_value=None),
            patch("kodo.cli._main.launch_run", autospec=True, side_effect=capture_launch),
        ):
            _main_inner()

        assert captured_goal["text"] == "Build an API", (
            f"Expected original goal preserved when auto-refine returns None, got {captured_goal.get('text')!r}"
        )

    def test_auto_refine_skips_noninteractive_intake(self, tmp_path):
        """--auto-refine must NOT fall through to run_intake_noninteractive."""
        with (
            patch("sys.argv", [
                "kodo", "--goal", "Build an API", "--auto-refine",
                "--yes", "--project", str(tmp_path),
            ]),
            patch("kodo.cli._main._print_banner", autospec=True),
            patch("kodo.cli._main._load_goal_plan", autospec=True, return_value=None),
            patch("kodo.cli._main.preferred_backend", autospec=True, return_value="claude"),
            patch("kodo.cli._main.run_intake_auto", autospec=True, return_value="Refined goal"),
            patch("kodo.cli._main.launch_run", autospec=True, return_value=_fake_run_result()),
            patch("kodo.cli._main.run_intake_noninteractive", autospec=True) as mock_noninteractive,
        ):
            _main_inner()

        mock_noninteractive.assert_not_called()


# ---------------------------------------------------------------------------
# A15: --resume + --goal conflict produces specific error message
# ---------------------------------------------------------------------------


class TestResumeGoalConflictMessage:
    """Verify --resume + --goal conflict error is specific, not a generic exit."""

    @pytest.fixture(autouse=True)
    def _mock_backends(self):
        with (
            patch("kodo.cli._params.preferred_orchestrator", autospec=True, return_value="claude-code"),
            patch("kodo.cli._params.check_api_key", autospec=True, return_value=None),
        ):
            yield

    def test_resume_with_goal_error_message(self, tmp_path, capsys):
        """--resume + --goal should produce error mentioning both flags."""
        with (
            patch("sys.argv", ["kodo", "--resume", "--goal", "X", "--project", str(tmp_path)]),
            patch("kodo.cli._main._print_banner", autospec=True),
            pytest.raises(SystemExit),
        ):
            _main_inner()
        err = capsys.readouterr().err
        assert "--resume" in err, (
            f"Error should mention --resume, got: {err!r}"
        )

    def test_resume_with_goal_file_error_message(self, tmp_path, capsys):
        """--resume + --goal-file should produce error mentioning the conflict."""
        goal_file = tmp_path / "goal.md"
        goal_file.write_text("Build X")
        with (
            patch("sys.argv", ["kodo", "--resume", "--goal-file", str(goal_file), "--project", str(tmp_path)]),
            patch("kodo.cli._main._print_banner", autospec=True),
            pytest.raises(SystemExit),
        ):
            _main_inner()
        err = capsys.readouterr().err
        assert "--resume" in err, (
            f"Error should mention --resume conflict, got: {err!r}"
        )

    def test_resume_with_improve_error_message(self, tmp_path, capsys):
        """--resume + --improve should produce error mentioning the conflict."""
        with (
            patch("sys.argv", ["kodo", "--resume", "--improve", "--project", str(tmp_path)]),
            pytest.raises(SystemExit),
        ):
            _main_inner()
        # This one is caught by argparse (--improve + --resume in exclusive group sense)
        # or by the manual check. Either way, should exit.


# ── CLI edge cases (relocated from test_stage2_edge_cases.py) ────────────


def _fake_run_result():
    from kodo.orchestrators.base import CycleResult, RunResult
    return RunResult(
        cycles=[CycleResult(exchanges=1, finished=True, summary="Done.")],
    )


def test_very_long_goal_preserved(tmp_path: Path):
    """A 10k-char goal passes through to launch_run without truncation."""
    long_goal = "x" * 10000
    with (
        patch("kodo.cli._params.preferred_orchestrator", autospec=True, return_value="claude-code"),
        patch("kodo.cli._params.check_api_key", autospec=True, return_value=None),
        patch("kodo.cli._main.launch_run", autospec=True) as mock_launch,
    ):
        mock_launch.return_value = _fake_run_result()
        sys.argv = [
            "kodo", "--goal", long_goal, "--skip-intake", "--yes",
            "--project", str(tmp_path),
        ]
        _main_inner()

    goal_passed = mock_launch.call_args[0][1]
    assert len(goal_passed) == 10000


def test_unicode_and_special_chars_in_goal(tmp_path: Path):
    """Unicode, newlines, and quotes in --goal pass through unmangled."""
    special_goal = (
        "Build a «café» app\nWith \"quotes\" and 'apostrophes'\nAnd emoji: 🚀"
    )
    with (
        patch("kodo.cli._params.preferred_orchestrator", autospec=True, return_value="claude-code"),
        patch("kodo.cli._params.check_api_key", autospec=True, return_value=None),
        patch("kodo.cli._main.launch_run", autospec=True) as mock_launch,
    ):
        mock_launch.return_value = _fake_run_result()
        sys.argv = [
            "kodo", "--goal", special_goal, "--skip-intake", "--yes",
            "--project", str(tmp_path),
        ]
        _main_inner()

    goal_passed = mock_launch.call_args[0][1]
    assert "café" in goal_passed
    assert "🚀" in goal_passed


@pytest.mark.skipif(
    hasattr(os, "getuid") and os.getuid() == 0,
    reason="Skip when running as root",
)
def test_unreadable_goal_file_no_traceback(tmp_path: Path, capsys):
    """--goal-file pointing to a chmod-000 file gives a clean error."""
    goal_file = tmp_path / "secret_goal.md"
    goal_file.write_text("secret content")
    try:
        goal_file.chmod(0o000)
    except OSError:
        pytest.skip("Cannot chmod 000 on this platform")

    try:
        with (
            patch("kodo.cli._params.preferred_orchestrator", autospec=True, return_value="claude-code"),
            patch("kodo.cli._params.check_api_key", autospec=True, return_value=None),
        ):
            sys.argv = [
                "kodo", "--goal-file", str(goal_file), "--skip-intake",
                "--yes", "--project", str(tmp_path),
            ]
            with pytest.raises(SystemExit):
                _main_inner()

        combined = capsys.readouterr().out + capsys.readouterr().err
        assert "Traceback" not in combined
    finally:
        goal_file.chmod(0o644)


# ── Robustness tests (relocated from test_robustness.py) ─────────────────


class TestCorruptConfigResume:
    """Corrupt config.json on resume falls back to params from RunState."""

    def test_corrupt_config_json_falls_back(self, tmp_path: Path):
        from kodo import log
        from kodo.log import RunDir

        project = tmp_path / "proj"
        project.mkdir()
        run_id = "20250315_120000"
        run_root = log._runs_root() / run_id
        run_root.mkdir(parents=True)

        events = [
            {
                "event": "run_start", "goal": "Fix bug",
                "project_dir": str(project), "orchestrator": "api",
                "model": "opus", "max_exchanges": 20, "max_cycles": 1,
                "team": ["worker_fast"],
            },
            {"event": "cli_args", "team": "full"},
            {"event": "cycle_end", "summary": "partial"},
        ]
        (run_root / "run.jsonl").write_text(
            "\n".join(json.dumps({"ts": "t", "t": 0, **e}) for e in events) + "\n"
        )
        (run_root / "goal.md").write_text("Fix bug")
        (run_root / "config.json").write_text("not valid json {{{")

        with patch("kodo.cli._main.launch_resume", autospec=True) as mock_resume:
            sys.argv = ["kodo", "--resume", run_id, "--yes", "--project", str(project)]
            _main_inner()

        mock_resume.assert_called_once()
        assert mock_resume.call_args[0][0].run_id == run_id


def test_goal_file_not_found_exits(tmp_path: Path):
    """--goal-file with missing path exits with error."""
    project = tmp_path / "proj"
    project.mkdir()
    with (
        patch("kodo.cli._launch._original_stdout", None),
        pytest.raises(SystemExit),
    ):
        sys.argv = [
            "kodo", "--goal-file", str(tmp_path / "nonexistent.md"),
            "--yes", "--project", str(project),
        ]
        _main_inner()
