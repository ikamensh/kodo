"""Tests for non-interactive CLI mode."""

from __future__ import annotations

import json
import os
import sys
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import pytest

from kodo.cli import (
    _build_fallback_plan,
    _build_params_from_flags,
    _extract_section,
    _load_or_select_params,
    _main_inner,
    run_intake_noninteractive,
)
from kodo.cli._improve import _validate_improve_plan
from kodo.log import RunDir
from tests.conftest import make_scripted_session


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Temporary project directory."""
    return tmp_path


def _make_args(**overrides) -> Namespace:
    """Create an argparse Namespace with defaults for non-interactive mode."""
    defaults = dict(
        goal="Build something",
        goal_file=None,
        improve=False,
        team=None,
        exchanges=None,
        cycles=None,
        orchestrator=None,
        skip_intake=False,
        resume=None,
        project_dir=".",
    )
    defaults.update(overrides)
    return Namespace(**defaults)


# ---------------------------------------------------------------------------
# TestBuildParamsFromFlags
# ---------------------------------------------------------------------------


class TestBuildParamsFromFlags:
    @pytest.fixture(autouse=True)
    def _fake_backends(self):
        with (
            patch("kodo.cli._params.preferred_orchestrator", autospec=True, return_value="claude-code"),
            patch("kodo.cli._params.check_api_key", autospec=True, return_value=None),
        ):
            yield

    def test_defaults_to_full_mode(self, project):
        args = _make_args()
        params = _build_params_from_flags(args, project)
        assert params["team"] == "full"

    def test_explicit_mode(self, project):
        args = _make_args(team="quick")
        params = _build_params_from_flags(args, project)
        assert params["team"] == "quick"

    def test_exchanges_falls_back_to_mode_default(self, project):
        args = _make_args()
        params = _build_params_from_flags(args, project)
        assert params["max_exchanges"] == 30  # full default

    def test_exchanges_override(self, project):
        args = _make_args(exchanges=50)
        params = _build_params_from_flags(args, project)
        assert params["max_exchanges"] == 50

    def test_cycles_falls_back_to_mode_default(self, project):
        args = _make_args()
        params = _build_params_from_flags(args, project)
        assert params["max_cycles"] == 5  # full default

    def test_quick_mode_defaults(self, project):
        args = _make_args(team="quick")
        params = _build_params_from_flags(args, project)
        assert params["max_exchanges"] == 20
        assert params["max_cycles"] == 1

    def test_orchestrator_defaults_to_api_when_gemini_key_available(self, project):
        args = _make_args(orchestrator="gemini-flash")
        with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}):
            params = _build_params_from_flags(args, project)
        assert params["orchestrator"] == "api"

    def test_orchestrator_auto_detects_claude_code_without_api_keys(self, project):
        """Without any provider API keys and no explicit model, falls back to claude-code."""
        args = _make_args()  # no orchestrator specified
        # Simulate user env without any provider API keys
        _PROVIDER_KEYS = {
            "GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "GROQ_API_KEY",
            "OPENROUTER_API_KEY", "MISTRAL_API_KEY", "XAI_API_KEY",
        }
        env_no_keys = {
            k: v
            for k, v in os.environ.items()
            if k not in _PROVIDER_KEYS
        }
        with patch.dict("kodo.cli._params.os.environ", env_no_keys, clear=True):
            params = _build_params_from_flags(args, project)
        assert params["orchestrator"] == "claude-code"

    def test_improve_mode_uses_api_with_gemini_key(self, project):
        args = _make_args(improve=True, orchestrator="gemini-flash")
        with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}):
            params = _build_params_from_flags(args, project)
        assert params["orchestrator"] == "api"

    def test_orchestrator_explicit(self, project):
        args = _make_args(orchestrator="opus")
        params = _build_params_from_flags(args, project)
        assert params["orchestrator"] == "api"

    def test_saves_config_to_disk(self, project):
        args = _make_args()
        _build_params_from_flags(args, project)
        config_path = project / ".kodo" / "config.json"
        assert config_path.exists()
        saved = json.loads(config_path.read_text())
        assert saved["team"] == "full"

    def test_api_key_validation_exits(self, project):
        args = _make_args(orchestrator="opus")
        with (
            patch(  # noqa: autospec
                "kodo.cli._params.check_api_key",
                return_value="ANTHROPIC_API_KEY not set",
            ),
            patch("kodo.cli._launch._original_stdout", None),
        ):
            with pytest.raises(SystemExit):
                _build_params_from_flags(args, project)


def test_unreadable_config_falls_back_to_selection(project):
    """When config file exists but read raises OSError, kodo does not crash."""
    cfg = project / ".kodo" / "config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        json.dumps(
            {
                "team": "full",
                "orchestrator": "api",
                "orchestrator_model": "opus",
                "max_exchanges": 30,
                "max_cycles": 5,
            }
        )
    )

    original_read_text = Path.read_text

    def patched_read_text(self, *args, **kwargs):
        if self.resolve() == cfg.resolve():
            raise OSError(13, "Permission denied")
        return original_read_text(self, *args, **kwargs)

    with patch.object(Path, "read_text", patched_read_text):
        with patch("kodo.cli._params.select_params", autospec=True) as mock_select:
            mock_select.return_value = {
                "team": "full",
                "orchestrator": "api",
                "orchestrator_model": "opus",
                "max_exchanges": 30,
                "max_cycles": 5,
            }
            result = _load_or_select_params(project)
    assert result == mock_select.return_value
    mock_select.assert_called_once()


# ---------------------------------------------------------------------------
# TestNonInteractiveGoalInput
# ---------------------------------------------------------------------------


class TestNonInteractiveGoalInput:
    @pytest.fixture(autouse=True)
    def _fake_backends(self):
        with (
            patch("kodo.cli._params.preferred_orchestrator", autospec=True, return_value="claude-code"),
            patch("kodo.cli._params.check_api_key", autospec=True, return_value=None),
        ):
            yield

    def test_inline_goal(self, project):
        """--goal 'text' passes goal_text correctly through to launch."""
        with (
            patch("kodo.cli._main.launch_run", autospec=True) as mock_launch,
            patch("kodo.cli._main.run_intake_noninteractive", autospec=True, return_value=None),
        ):
            sys.argv = ["kodo", "--goal", "Build a web app", "--project", str(project)]
            _main_inner()
            goal_arg = mock_launch.call_args[0][1]
            assert goal_arg == "Build a web app"

    def test_goal_file(self, project):
        """--goal-file reads from the file."""
        goal_file = project / "my-goal.md"
        goal_file.write_text("Build an API server")

        with (
            patch("kodo.cli._main.launch_run", autospec=True) as mock_launch,
            patch("kodo.cli._main.run_intake_noninteractive", autospec=True, return_value=None),
        ):
            sys.argv = ["kodo", "--goal-file", str(goal_file), "--project", str(project)]
            _main_inner()
            goal_arg = mock_launch.call_args[0][1]
            assert goal_arg == "Build an API server"

    def test_goal_file_not_found(self, project):
        with pytest.raises(SystemExit):
            sys.argv = [
                "kodo",
                "--goal-file",
                str(project / "nonexistent.md"),
                "--project",
                str(project),
            ]
            _main_inner()

    def test_goal_file_empty(self, project):
        goal_file = project / "empty.md"
        goal_file.write_text("   ")

        with pytest.raises(SystemExit):
            sys.argv = ["kodo", "--goal-file", str(goal_file), "--project", str(project)]
            _main_inner()

    def test_goal_and_goal_file_mutually_exclusive(self):
        """argparse should reject both --goal and --goal-file."""
        with pytest.raises(SystemExit):
            sys.argv = ["kodo", "--goal", "X", "--goal-file", "y.md"]
            _main_inner()


# ---------------------------------------------------------------------------
# TestRunIntakeNoninteractive
# ---------------------------------------------------------------------------


class TestRunIntakeNoninteractive:
    def test_produces_plan_in_one_query(self, project):
        run_dir = RunDir.create(project, "test")
        plan_json = json.dumps(
            {
                "context": "Test",
                "stages": [
                    {
                        "index": 1,
                        "name": "S1",
                        "description": "Do it",
                        "acceptance_criteria": "Done",
                        "browser_testing": False,
                    }
                ],
            }
        )
        session = make_scripted_session(
            ["Plan written."],
            project,
            write_file={
                "on_query": 0,
                "path": str(run_dir.goal_plan_file),
                "content": plan_json,
            },
        )

        with (
            patch("kodo.cli._intake.make_session", autospec=True, return_value=session),
            patch("kodo.cli._intake.preferred_backend", autospec=True, return_value="claude"),
        ):
            result = run_intake_noninteractive(run_dir, "Build something")

        assert result is not None
        assert len(result.stages) == 1
        # 1 planning query + 1 parallelism pass
        assert session.stats.queries == 2

    def test_finalize_fallback(self, project):
        """If first query doesn't produce file, sends finalize query."""
        run_dir = RunDir.create(project, "test")
        plan_json = json.dumps(
            {
                "context": "Test",
                "stages": [
                    {
                        "index": 1,
                        "name": "S1",
                        "description": "Do it",
                        "acceptance_criteria": "Done",
                        "browser_testing": False,
                    }
                ],
            }
        )
        session = make_scripted_session(
            ["Analyzing...", "Plan written."],
            project,
            write_file={
                "on_query": 1,
                "path": str(run_dir.goal_plan_file),
                "content": plan_json,
            },
        )

        with (
            patch("kodo.cli._intake.make_session", autospec=True, return_value=session),
            patch("kodo.cli._intake.preferred_backend", autospec=True, return_value="claude"),
        ):
            result = run_intake_noninteractive(run_dir, "Build something")

        assert result is not None
        # 1 planning + 1 finalize + 1 parallelism pass
        assert session.stats.queries == 3

    def test_returns_none_when_no_file_written(self, project):
        run_dir = RunDir.create(project, "test")
        session = make_scripted_session(["Hmm.", "Still nothing."], project)

        with (
            patch("kodo.cli._intake.make_session", autospec=True, return_value=session),
            patch("kodo.cli._intake.preferred_backend", autospec=True, return_value="claude"),
        ):
            result = run_intake_noninteractive(run_dir, "Vague goal")

        assert result is None

    def test_returns_none_when_no_backend(self, project):
        run_dir = RunDir.create(project, "test")
        with patch("kodo.cli._intake.preferred_backend", autospec=True, return_value=None):
            result = run_intake_noninteractive(run_dir, "Build something")

        assert result is None

    def test_no_input_calls(self, project):
        """Non-interactive intake must never call input()."""
        run_dir = RunDir.create(project, "test")
        session = make_scripted_session(["Done."], project)

        with (
            patch("kodo.cli._intake.make_session", autospec=True, return_value=session),
            patch("kodo.cli._intake.preferred_backend", autospec=True, return_value="claude"),
            patch("builtins.input", autospec=True, side_effect=AssertionError("input() called")),
        ):
            run_intake_noninteractive(run_dir, "Build something")


# ---------------------------------------------------------------------------
# TestNonInteractiveEndToEnd
# ---------------------------------------------------------------------------


class TestNonInteractiveEndToEnd:
    @pytest.fixture(autouse=True)
    def _fake_backends(self):
        with (
            patch("kodo.cli._params.preferred_orchestrator", autospec=True, return_value="claude-code"),
            patch("kodo.cli._params.check_api_key", autospec=True, return_value=None),
        ):
            yield

    def test_no_interactive_prompts(self, project):
        """The full non-interactive flow must never call input() or questionary."""
        with (
            patch("kodo.cli._main.launch_run", autospec=True) as mock_launch,
            patch("kodo.cli._main.run_intake_noninteractive", autospec=True, return_value=None),
            patch(
                "builtins.input", autospec=True,
                side_effect=AssertionError("input() should not be called"),
            ),
            patch(
                "questionary.select", autospec=True,
                side_effect=AssertionError("questionary should not be called"),
            ),
        ):
            sys.argv = ["kodo", "--goal", "Build X", "--project", str(project)]
            _main_inner()
            mock_launch.assert_called_once()

    def test_params_passed_through(self, project):
        """CLI flags should be reflected in the params passed to launch_run."""
        with (
            patch("kodo.cli._main.launch_run", autospec=True) as mock_launch,
            patch("kodo.cli._main.run_intake_noninteractive", autospec=True, return_value=None),
        ):
            sys.argv = [
                "kodo",
                "--goal",
                "Build X",
                "--team",
                "quick",
                "--exchanges",
                "42",
                "--cycles",
                "7",
                "--orchestrator",
                "gemini-pro",
                "--project",
                str(project),
            ]
            _main_inner()

            params = mock_launch.call_args[0][2]
            assert params["team"] == "quick"
            assert params["max_exchanges"] == 42
            assert params["max_cycles"] == 7
            assert params["orchestrator"] == "api"
            assert params["orchestrator_model"] == "gemini-pro"

    def test_skip_intake_flag(self, project):
        """--skip-intake should prevent intake from running."""
        with (
            patch("kodo.cli._main.launch_run", autospec=True),
            patch("kodo.cli._main.run_intake_noninteractive", autospec=True) as mock_intake,
        ):
            sys.argv = [
                "kodo",
                "--goal",
                "Simple fix",
                "--skip-intake",
                "--project",
                str(project),
            ]
            _main_inner()
            mock_intake.assert_not_called()

    def test_resume_with_goal_errors(self, project, capsys):
        """--resume + --goal should be rejected with a specific conflict error."""
        with pytest.raises(SystemExit):
            sys.argv = ["kodo", "--resume", "--goal", "Build X", "--project", str(project)]
            _main_inner()
        err = capsys.readouterr().err
        assert "--resume" in err, (
            f"Expected error mentioning --resume conflict, got: {err!r}"
        )

    def test_uses_existing_goal_plan(self, project):
        """If goal-plan.json exists in the run dir, non-interactive mode uses it."""
        plan = {
            "context": "Test",
            "stages": [
                {
                    "index": 1,
                    "name": "Stage 1",
                    "description": "Do it",
                    "acceptance_criteria": "Done",
                    "browser_testing": False,
                }
            ],
        }

        # Patch RunDir.create so we can pre-populate the goal plan file
        original_create = RunDir.create

        def create_with_plan(project_dir, run_id=None):
            rd = original_create(project_dir, run_id)
            rd.goal_plan_file.write_text(json.dumps(plan))
            return rd

        with (
            patch("kodo.cli._main.launch_run", autospec=True) as mock_launch,
            patch("kodo.cli._main.run_intake_noninteractive", autospec=True) as mock_intake,
            patch("kodo.cli._main.RunDir.create", autospec=True, side_effect=create_with_plan),
        ):
            sys.argv = ["kodo", "--goal", "Build X", "--project", str(project)]
            _main_inner()
            # Should use existing plan, not run intake
            mock_intake.assert_not_called()
            launched_plan = (
                mock_launch.call_args.kwargs.get("plan") or mock_launch.call_args[0][3]
            )
            assert len(launched_plan.stages) == 1


# ---------------------------------------------------------------------------
# TestImproveFlag
# ---------------------------------------------------------------------------


class TestImproveFlag:
    @pytest.fixture(autouse=True)
    def _fake_backends(self):
        with (
            patch("kodo.cli._params.preferred_orchestrator", autospec=True, return_value="claude-code"),
            patch("kodo.cli._params.check_api_key", autospec=True, return_value=None),
            patch("kodo.cli._main.run_improve_discovery", autospec=True, return_value=None),
        ):
            yield

    def test_improve_populates_goal_from_template(self, project):
        """--improve should construct goal text from _IMPROVE_GOAL template."""
        with (
            patch("kodo.cli._main.launch_run", autospec=True) as mock_launch,
            patch("kodo.cli._main.run_intake_noninteractive", autospec=True, return_value=None),
        ):
            sys.argv = ["kodo", "--improve", "--project", str(project)]
            _main_inner()
            goal_arg = mock_launch.call_args[0][1]
            assert (
                "improvement report" in goal_arg.lower()
                or "improve" in goal_arg.lower()
            )
            assert "improve-report.md" in goal_arg

    def test_improve_skips_intake(self, project):
        """--improve should skip intake interview."""
        with (
            patch("kodo.cli._main.launch_run", autospec=True),
            patch("kodo.cli._main.run_intake_noninteractive", autospec=True) as mock_intake,
        ):
            sys.argv = ["kodo", "--improve", "--project", str(project)]
            _main_inner()
            mock_intake.assert_not_called()

    def test_improve_defaults_to_full_mode(self, project):
        """--improve should default mode to full."""
        with (
            patch("kodo.cli._main.launch_run", autospec=True) as mock_launch,
            patch("kodo.cli._main.run_intake_noninteractive", autospec=True, return_value=None),
        ):
            sys.argv = ["kodo", "--improve", "--project", str(project)]
            _main_inner()
            params = mock_launch.call_args[0][2]
            assert params["team"] == "full"

    def test_improve_respects_explicit_team(self, project):
        """--improve should not override an explicitly set --team."""
        with (
            patch("kodo.cli._main.launch_run", autospec=True) as mock_launch,
            patch("kodo.cli._main.run_intake_noninteractive", autospec=True, return_value=None),
        ):
            sys.argv = ["kodo", "--improve", "--team", "quick", "--project", str(project)]
            _main_inner()
            params = mock_launch.call_args[0][2]
            assert params["team"] == "quick"

    def test_improve_no_interactive_prompts(self, project):
        """--improve must never call input() or questionary."""
        with (
            patch("kodo.cli._main.launch_run", autospec=True),
            patch("kodo.cli._main.run_intake_noninteractive", autospec=True, return_value=None),
            patch(
                "builtins.input", autospec=True,
                side_effect=AssertionError("input() should not be called"),
            ),
            patch(
                "questionary.select", autospec=True,
                side_effect=AssertionError("questionary should not be called"),
            ),
        ):
            sys.argv = ["kodo", "--improve", "--project", str(project)]
            _main_inner()

    def test_improve_mutually_exclusive_with_goal(self):
        """--improve and --goal should be mutually exclusive."""
        with pytest.raises(SystemExit):
            sys.argv = ["kodo", "--improve", "--goal", "Build X"]
            _main_inner()

    def test_improve_mutually_exclusive_with_goal_file(self, project):
        """--improve and --goal-file should be mutually exclusive."""
        goal_file = project / "g.md"
        goal_file.write_text("Build X")
        with pytest.raises(SystemExit):
            sys.argv = ["kodo", "--improve", "--goal-file", str(goal_file)]
            _main_inner()

    def test_improve_passes_staged_plan(self, project):
        """--improve should pass a GoalPlan (not None) to launch_run."""
        with (
            patch("kodo.cli._main.launch_run", autospec=True) as mock_launch,
            patch("kodo.cli._main.run_intake_noninteractive", autospec=True, return_value=None),
        ):
            sys.argv = ["kodo", "--improve", "--project", str(project)]
            _main_inner()
            plan = mock_launch.call_args.kwargs.get(
                "plan",
                mock_launch.call_args[0][3]
                if len(mock_launch.call_args[0]) > 3
                else None,
            )
            assert plan is not None
            assert len(plan.stages) == 7

    def test_improve_plan_stage_order(self, project):
        """--improve stages should follow the right sequence."""
        with (
            patch("kodo.cli._main.launch_run", autospec=True) as mock_launch,
            patch("kodo.cli._main.run_intake_noninteractive", autospec=True, return_value=None),
        ):
            sys.argv = ["kodo", "--improve", "--project", str(project)]
            _main_inner()
            plan = mock_launch.call_args.kwargs.get(
                "plan",
                mock_launch.call_args[0][3]
                if len(mock_launch.call_args[0]) > 3
                else None,
            )
            names = [s.name for s in plan.stages]
            assert names == [
                "Test Tool Forge",
                "Baseline & Static Analysis",
                "Happy Path Integration Testing",
                "Exploratory & Adversarial Testing",
                "Architecture & Simplification Audit",
                "Triage & Verify",
                "Fix & Report",
            ]

    def test_improve_with_buggy_project_uses_fallback_and_starts_cycle(self):
        """--improve on buggy_project: discovery returns None, falls back, launches with 5-stage plan."""
        buggy_project = (
            Path(__file__).resolve().parent.parent / "fixtures" / "buggy_project"
        )
        if not buggy_project.exists():
            pytest.skip("fixtures/buggy_project not found")

        with (
            patch("kodo.cli._main.launch_run", autospec=True) as mock_launch,
            patch("kodo.cli._main.run_improve_discovery", return_value=None),  # noqa: autospec
            patch("kodo.cli._params.preferred_orchestrator", return_value="claude-code"),  # noqa: autospec
            patch("kodo.cli._params.check_api_key", return_value=None),  # noqa: autospec
            patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}),
        ):
            sys.argv = ["kodo", "--improve", "--project", str(buggy_project)]
            _main_inner()

        mock_launch.assert_called_once()
        call_args = mock_launch.call_args
        goal_text = call_args[0][1]
        params = call_args[0][2]
        plan = call_args.kwargs.get("plan") or (
            call_args[0][3] if len(call_args[0]) > 3 else None
        )

        assert (
            "improve" in goal_text.lower() or "improvement report" in goal_text.lower()
        )
        assert params["team"] == "full"
        assert params["orchestrator"] == "api"
        assert plan is not None
        assert len(plan.stages) == 7
        assert plan.stages[0].name == "Test Tool Forge"


# ---------------------------------------------------------------------------
# TestBuildImprovePlan
# ---------------------------------------------------------------------------


class TestBuildFallbackPlan:
    """Tests for _build_fallback_plan() structure."""

    def test_has_seven_stages(self):
        plan = _build_fallback_plan("/tmp/report.md")
        assert len(plan.stages) == 7

    def test_stages_have_sequential_indices(self):
        plan = _build_fallback_plan("/tmp/report.md")
        assert [s.index for s in plan.stages] == [1, 2, 3, 4, 5, 6, 7]

    def test_first_stage_is_test_tool_forge(self):
        """Stage 1 should be Test Tool Forge with persist_changes=True."""
        plan = _build_fallback_plan("/tmp/report.md")
        forge = plan.stages[0]
        assert forge.name == "Test Tool Forge"
        assert forge.persist_changes is True
        assert forge.parallel_group is None
        assert "findings-test-tool-forge.md" in forge.description

    def test_report_path_in_final_stage(self):
        plan = _build_fallback_plan("/tmp/my-report.md")
        last = plan.stages[-1]
        assert "/tmp/my-report.md" in last.description
        assert "/tmp/my-report.md" in last.acceptance_criteria

    def test_time_guidance_in_integration_stages(self):
        """Stages 3 and 4 should include time efficiency guidance."""
        plan = _build_fallback_plan("/tmp/report.md")
        for stage in plan.stages[2:4]:
            assert "Mock or stub" in stage.description
            assert "30 seconds" in stage.description

    def test_time_guidance_not_in_forge_or_static_stage(self):
        """Stage 1 (forge) and Stage 2 (static analysis) should not have time guidance."""
        plan = _build_fallback_plan("/tmp/report.md")
        assert "Mock or stub" not in plan.stages[0].description
        assert "Mock or stub" not in plan.stages[1].description

    def test_all_stages_have_acceptance_criteria(self):
        plan = _build_fallback_plan("/tmp/report.md")
        for stage in plan.stages:
            assert stage.acceptance_criteria, f"Stage {stage.index} missing criteria"

    def test_context_emphasizes_running_software(self):
        plan = _build_fallback_plan("/tmp/report.md")
        assert "RUNNING" in plan.context

    def test_stages_3_4_5_are_parallel(self):
        """Stages 3, 4, and 5 should share the same parallel_group."""
        plan = _build_fallback_plan("/tmp/report.md")
        assert plan.stages[2].parallel_group == 1
        assert plan.stages[3].parallel_group == 1
        assert plan.stages[4].parallel_group == 1

    def test_stages_1_2_6_and_7_are_sequential(self):
        """Stages 1, 2, 6, and 7 should have no parallel_group (sequential)."""
        plan = _build_fallback_plan("/tmp/report.md")
        assert plan.stages[0].parallel_group is None
        assert plan.stages[1].parallel_group is None
        assert plan.stages[5].parallel_group is None
        assert plan.stages[6].parallel_group is None

    def test_parallel_stage_descriptions_mention_findings_file(self):
        """Stage descriptions should tell agents where to write findings."""
        plan = _build_fallback_plan("/tmp/report.md")
        assert "findings-happy-path.md" in plan.stages[2].description
        assert "findings-adversarial.md" in plan.stages[3].description
        assert "findings-architecture.md" in plan.stages[4].description

    def test_fix_stage_references_all_findings_files(self):
        """Final stage should tell agents to read all findings files."""
        plan = _build_fallback_plan("/tmp/report.md")
        fix_stage = plan.stages[6]
        assert "findings-test-tool-forge.md" in fix_stage.description
        assert "findings-happy-path.md" in fix_stage.description
        assert "findings-adversarial.md" in fix_stage.description
        assert "findings-architecture.md" in fix_stage.description

    def test_parallel_stages_instruct_no_source_modification(self):
        """Read-only parallel stages should explicitly say not to modify code."""
        plan = _build_fallback_plan("/tmp/report.md")
        for stage in plan.stages[2:5]:
            assert "Do NOT modify source code" in stage.description

    def test_stages_1_3_4_5_6_have_quick_check_verification(self):
        """Analytical stages use quick-check verification (list of QuickCheck)."""
        plan = _build_fallback_plan("/tmp/report.md")
        for idx in [0, 2, 3, 4, 5]:  # stages 1, 3, 4, 5, 6
            stage = plan.stages[idx]
            assert isinstance(stage.verification, list), (
                f"Stage {stage.index} ({stage.name}) should have quick-check verification"
            )
            assert len(stage.verification) == 1

    def test_stage_2_has_skip_verification(self):
        """Baseline & Static Analysis uses skip verification."""
        plan = _build_fallback_plan("/tmp/report.md")
        assert plan.stages[1].verification == "skip"

    def test_stage_7_has_full_verification(self):
        """Fix & Report stage uses full agent-based verification."""
        plan = _build_fallback_plan("/tmp/report.md")
        assert plan.stages[6].verification == "full"


# ---------------------------------------------------------------------------
# TestExtractSection
# ---------------------------------------------------------------------------


class TestExtractSection:
    def test_extracts_auto_fixed(self):
        report = (
            "# Improve Report\n\n"
            "## Auto-fixed\n"
            "- foo.py:10 — removed unused import\n"
            "- bar.py:20 — fixed typo\n\n"
            "## Needs decision\n"
            "- baz.py:5 — consider refactoring\n"
        )
        section = _extract_section(report, "Auto-fixed")
        assert "foo.py:10" in section
        assert "bar.py:20" in section
        assert "baz.py:5" not in section

    def test_extracts_needs_decision(self):
        report = (
            "# Improve Report\n\n"
            "## Auto-fixed\n"
            "- foo.py:10 — removed unused import\n\n"
            "## Needs decision\n"
            "- baz.py:5 — consider refactoring\n"
            "- qux.py:99 — dead code\n"
        )
        section = _extract_section(report, "Needs decision")
        assert "baz.py:5" in section
        assert "qux.py:99" in section
        assert "foo.py:10" not in section

    def test_returns_empty_for_missing_section(self):
        report = "# Improve Report\n\n## Auto-fixed\n- x\n"
        assert _extract_section(report, "Needs decision") == ""


# ---------------------------------------------------------------------------
# TestValidateImprovePlan
# ---------------------------------------------------------------------------


class TestValidateImprovePlan:
    """Tests for _validate_improve_plan() post-processing."""

    def _make_plan(self, stage_names, parallel_groups=None):
        from kodo.orchestrators.base import GoalPlan, GoalStage

        stages = [
            GoalStage(
                index=i + 1,
                name=n,
                description=f"Do {n}",
                acceptance_criteria="Done",
                parallel_group=(parallel_groups or {}).get(n),
            )
            for i, n in enumerate(stage_names)
        ]
        return GoalPlan(context="test", stages=stages)

    def test_passes_through_complete_plan(self):
        plan = self._make_plan(
            ["Baseline", "Testing", "Triage & Verify", "Fix & Report"]
        )
        result = _validate_improve_plan(plan, "/tmp/report.md", "/tmp/run")
        assert len(result.stages) == 4

    def test_appends_triage_if_missing(self):
        plan = self._make_plan(["Baseline", "Testing", "Fix & Report"])
        result = _validate_improve_plan(plan, "/tmp/report.md", "/tmp/run")
        names = [s.name for s in result.stages]
        assert "Triage & Verify" in names
        assert len(result.stages) == 4

    def test_appends_fix_if_missing(self):
        plan = self._make_plan(["Baseline", "Testing", "Triage & Verify"])
        result = _validate_improve_plan(plan, "/tmp/report.md", "/tmp/run")
        names = [s.name for s in result.stages]
        assert "Fix & Report" in names
        assert len(result.stages) == 4

    def test_appends_both_if_missing(self):
        plan = self._make_plan(["Baseline", "Testing"])
        result = _validate_improve_plan(plan, "/tmp/report.md", "/tmp/run")
        names = [s.name for s in result.stages]
        assert "Triage & Verify" in names
        assert "Fix & Report" in names
        assert len(result.stages) == 4

    def test_recognizes_verify_in_name(self):
        """A stage named 'Verify Findings' should count as triage."""
        plan = self._make_plan(["Baseline", "Verify Findings", "Fix & Report"])
        result = _validate_improve_plan(plan, "/tmp/report.md", "/tmp/run")
        assert len(result.stages) == 3  # no extra triage appended

    def test_assigns_findings_paths_to_analysis_stages(self):
        """Each analysis stage gets a findings file path injected."""
        plan = self._make_plan(
            ["Baseline", "Edge Cases", "Triage & Verify", "Fix & Report"]
        )
        result = _validate_improve_plan(plan, "/tmp/report.md", "/tmp/run")
        assert "findings-baseline.md" in result.stages[0].description
        assert "findings-edge-cases.md" in result.stages[1].description

    def test_parallel_stages_get_no_modify_instruction(self):
        """Parallel stages get 'Do NOT modify source code' injected."""
        plan = self._make_plan(
            [
                "Baseline",
                "Happy Path",
                "Adversarial",
                "Triage & Verify",
                "Fix & Report",
            ],
            parallel_groups={"Happy Path": 1, "Adversarial": 1},
        )
        result = _validate_improve_plan(plan, "/tmp/report.md", "/tmp/run")
        assert "Do NOT modify source code" in result.stages[1].description
        assert "Do NOT modify source code" in result.stages[2].description
        # Sequential stage should NOT have that instruction
        assert "Do NOT modify source code" not in result.stages[0].description

    def test_triage_stage_gets_findings_refs(self):
        """Triage stage description references all findings files."""
        plan = self._make_plan(
            ["Baseline", "Testing", "Triage & Verify", "Fix & Report"]
        )
        result = _validate_improve_plan(plan, "/tmp/report.md", "/tmp/run")
        triage = result.stages[2]
        assert "findings-baseline.md" in triage.description
        assert "findings-testing.md" in triage.description

    def test_fix_stage_gets_findings_refs(self):
        """Fix stage description references all findings files."""
        plan = self._make_plan(
            ["Baseline", "Testing", "Triage & Verify", "Fix & Report"]
        )
        result = _validate_improve_plan(plan, "/tmp/report.md", "/tmp/run")
        fix = result.stages[3]
        assert "findings-baseline.md" in fix.description
        assert "findings-testing.md" in fix.description


# ---------------------------------------------------------------------------
# TestInputValidation — BUG-3/4/5: goal, exchanges, cycles
# ---------------------------------------------------------------------------


class TestInputValidation:
    @pytest.fixture(autouse=True)
    def _fake_backends(self):
        with (
            patch("kodo.cli._params.preferred_orchestrator", autospec=True, return_value="claude-code"),
            patch("kodo.cli._params.check_api_key", autospec=True, return_value=None),
        ):
            yield

    def test_empty_goal_rejected(self, project):
        """--goal '' should fail with a clear error."""
        with pytest.raises(SystemExit):
            sys.argv = ["kodo", "--goal", "", "--project", str(project)]
            _main_inner()

    def test_whitespace_only_goal_rejected(self, project):
        """--goal '   ' should fail with a clear error."""
        with pytest.raises(SystemExit):
            sys.argv = ["kodo", "--goal", "   \t\n  ", "--project", str(project)]
            _main_inner()

    def test_negative_exchanges_rejected(self, project):
        """--exchanges -5 should fail with a clear error."""
        with pytest.raises(SystemExit):
            sys.argv = [
                "kodo",
                "--goal",
                "Build X",
                "--exchanges",
                "-5",
                "--project",
                str(project),
            ]
            _main_inner()

    def test_zero_exchanges_rejected(self, project):
        """--exchanges 0 should fail with a clear error."""
        with pytest.raises(SystemExit):
            sys.argv = [
                "kodo",
                "--goal",
                "Build X",
                "--exchanges",
                "0",
                "--project",
                str(project),
            ]
            _main_inner()

    def test_negative_cycles_rejected(self, project):
        """--cycles -1 should fail with a clear error."""
        with pytest.raises(SystemExit):
            sys.argv = [
                "kodo",
                "--goal",
                "Build X",
                "--cycles",
                "-1",
                "--project",
                str(project),
            ]
            _main_inner()

    def test_zero_cycles_rejected(self, project):
        """--cycles 0 should fail with a clear error."""
        with pytest.raises(SystemExit):
            sys.argv = [
                "kodo",
                "--goal",
                "Build X",
                "--cycles",
                "0",
                "--project",
                str(project),
            ]
            _main_inner()


# ---------------------------------------------------------------------------
# TestTeamConfigErrors — BUG-2: malformed team.json
# ---------------------------------------------------------------------------


class TestTeamConfigErrors:
    @pytest.fixture(autouse=True)
    def _fake_backends(self):
        with (
            patch("kodo.cli._params.preferred_orchestrator", autospec=True, return_value="claude-code"),
            patch("kodo.cli._params.check_api_key", autospec=True, return_value=None),
        ):
            yield

    def test_malformed_team_json_handled(self, project):
        """Invalid JSON in team.json should produce a clear error, not a traceback."""
        kodo_dir = project / ".kodo"
        kodo_dir.mkdir(parents=True, exist_ok=True)
        (kodo_dir / "team.json").write_text("{invalid json!!")

        with pytest.raises(SystemExit):
            sys.argv = [
                "kodo",
                "--goal",
                "Build X",
                "--skip-intake",
                "--project",
                str(project),
            ]
            _main_inner()

    def test_team_json_missing_agents_handled(self, project):
        """team.json without 'agents' key should produce a clear error."""
        kodo_dir = project / ".kodo"
        kodo_dir.mkdir(parents=True, exist_ok=True)
        (kodo_dir / "team.json").write_text(json.dumps({"name": "broken"}))

        with pytest.raises(SystemExit):
            sys.argv = [
                "kodo",
                "--goal",
                "Build X",
                "--skip-intake",
                "--project",
                str(project),
            ]
            _main_inner()


# ---------------------------------------------------------------------------
# TestPermissionErrors — BUG-7: .kodo/ directory creation
# ---------------------------------------------------------------------------


class TestPermissionErrors:
    @pytest.fixture(autouse=True)
    def _fake_backends(self):
        with (
            patch("kodo.cli._params.preferred_orchestrator", autospec=True, return_value="claude-code"),
            patch("kodo.cli._params.check_api_key", autospec=True, return_value=None),
        ):
            yield

    def test_kodo_dir_permission_error_handled(self, project):
        """PermissionError from _save_config should produce a clear error."""
        with (
            patch(
                "kodo.cli._params._save_config", autospec=True,
                side_effect=PermissionError("mock permission denied"),
            ),
            pytest.raises(SystemExit),
        ):
            sys.argv = [
                "kodo",
                "--goal",
                "Build X",
                "--skip-intake",
                "--project",
                str(project),
            ]
            _main_inner()


# ---------------------------------------------------------------------------
# TestGoalMdOSErrorWarning — warns when goal.md is unreadable
# ---------------------------------------------------------------------------


class TestGoalMdOSErrorWarning:
    def test_warns_on_oserror_reading_goal_md(self, project, capsys):
        """When goal.md exists but can't be read, a warning is printed."""
        goal_file = project / "goal.md"
        goal_file.write_text("Some goal")

        original_read_text = Path.read_text

        def patched_read_text(self, *args, **kwargs):
            if self.name.lower() == "goal.md":
                raise OSError(13, "Permission denied")
            return original_read_text(self, *args, **kwargs)

        with (
            patch.object(Path, "read_text", patched_read_text),
            patch("kodo.cli._main.get_goal", autospec=True, return_value="Fallback goal"),
            patch("kodo.cli._main.launch_run", autospec=True),
            patch("kodo.cli._main._offer_intake", autospec=True, return_value=(None, None)),
            patch(
                "kodo.cli._main._load_or_select_params", autospec=True,
                return_value={
                    "team": "full",
                    "orchestrator": "api",
                    "orchestrator_model": "opus",
                    "max_exchanges": 30,
                    "max_cycles": 5,
                },
            ),
        ):
            sys.argv = ["kodo", "--yes", "--project", str(project)]
            _main_inner()

        captured = capsys.readouterr()
        assert "Warning" in captured.out
        assert "could not read" in captured.out


# ---------------------------------------------------------------------------
# TestInvalidModeDefensiveCheck — invalid mode in params
# ---------------------------------------------------------------------------


class TestInvalidModeDefensiveCheck:
    @pytest.fixture(autouse=True)
    def _fake_backends(self):
        with (
            patch("kodo.cli._params.preferred_orchestrator", autospec=True, return_value="claude-code"),
            patch("kodo.cli._params.check_api_key", autospec=True, return_value=None),
        ):
            yield

    def test_invalid_mode_in_params_exits(self, project):
        """If params contain an unknown mode, _main_inner exits with error."""
        bad_params = {
            "team": "turbo",
            "orchestrator": "api",
            "orchestrator_model": "opus",
            "max_exchanges": 30,
            "max_cycles": 5,
        }
        with (
            patch("kodo.cli._main._build_params_from_flags", autospec=True, return_value=bad_params),
            patch("kodo.cli._main.run_intake_noninteractive", autospec=True, return_value=None),
            patch("kodo.cli._launch._original_stdout", None),
            pytest.raises(SystemExit),
        ):
            sys.argv = ["kodo", "--goal", "Build something", "--project", str(project)]
            _main_inner()

    def test_missing_team_key_in_params_exits(self, project):
        """If params dict has no 'team' key, _main_inner exits with error."""
        bad_params = {
            "orchestrator": "api",
            "orchestrator_model": "opus",
            "max_exchanges": 30,
            "max_cycles": 5,
        }
        with (
            patch("kodo.cli._main._build_params_from_flags", autospec=True, return_value=bad_params),
            patch("kodo.cli._main.run_intake_noninteractive", autospec=True, return_value=None),
            patch("kodo.cli._launch._original_stdout", None),
            pytest.raises(SystemExit),
        ):
            sys.argv = ["kodo", "--goal", "Build something", "--project", str(project)]
            _main_inner()
