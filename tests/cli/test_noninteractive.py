"""Tests for non-interactive CLI mode."""

from __future__ import annotations

import json
import sys
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.conftest import make_scripted_session
from kodo.cli import (
    ProjectType,
    _build_improve_plan,
    _build_params_from_flags,
    _detect_project_type,
    _extract_section,
    run_intake_noninteractive,
    _main_inner,
)
from kodo.log import RunDir


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
        mode=None,
        exchanges=None,
        cycles=None,
        orchestrator=None,
        orchestrator_model=None,
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
            patch("kodo.cli.has_claude", return_value=True),
            patch("kodo.cli.check_api_key", return_value=None),
        ):
            yield

    def test_defaults_to_saga_mode(self, project):
        args = _make_args()
        params = _build_params_from_flags(args, project)
        assert params["mode"] == "saga"

    def test_explicit_mode(self, project):
        args = _make_args(mode="mission")
        params = _build_params_from_flags(args, project)
        assert params["mode"] == "mission"

    def test_exchanges_falls_back_to_mode_default(self, project):
        args = _make_args()
        params = _build_params_from_flags(args, project)
        assert params["max_exchanges"] == 30  # saga default

    def test_exchanges_override(self, project):
        args = _make_args(exchanges=50)
        params = _build_params_from_flags(args, project)
        assert params["max_exchanges"] == 50

    def test_cycles_falls_back_to_mode_default(self, project):
        args = _make_args()
        params = _build_params_from_flags(args, project)
        assert params["max_cycles"] == 5  # saga default

    def test_mission_mode_defaults(self, project):
        args = _make_args(mode="mission")
        params = _build_params_from_flags(args, project)
        assert params["max_exchanges"] == 20
        assert params["max_cycles"] == 1

    def test_orchestrator_auto_detects_api_for_gemini(self, project):
        args = _make_args(orchestrator_model="gemini-flash")
        params = _build_params_from_flags(args, project)
        assert params["orchestrator"] == "api"

    def test_orchestrator_explicit(self, project):
        args = _make_args(orchestrator="api", orchestrator_model="opus")
        params = _build_params_from_flags(args, project)
        assert params["orchestrator"] == "api"

    def test_saves_config_to_disk(self, project):
        args = _make_args()
        _build_params_from_flags(args, project)
        config_path = project / ".kodo" / "config.json"
        assert config_path.exists()
        saved = json.loads(config_path.read_text())
        assert saved["mode"] == "saga"

    def test_api_key_validation_exits(self, project):
        args = _make_args(orchestrator="api", orchestrator_model="opus")
        with patch("kodo.cli.check_api_key", return_value="ANTHROPIC_API_KEY not set"):
            with pytest.raises(SystemExit):
                _build_params_from_flags(args, project)


# ---------------------------------------------------------------------------
# TestNonInteractiveGoalInput
# ---------------------------------------------------------------------------


class TestNonInteractiveGoalInput:
    @pytest.fixture(autouse=True)
    def _fake_backends(self):
        with (
            patch("kodo.cli.has_claude", return_value=True),
            patch("kodo.cli.check_api_key", return_value=None),
        ):
            yield

    def test_inline_goal(self, project):
        """--goal 'text' passes goal_text correctly through to launch."""
        with (
            patch("kodo.cli.launch_run") as mock_launch,
            patch("kodo.cli.run_intake_noninteractive", return_value=None),
        ):
            sys.argv = ["kodo", "--goal", "Build a web app", str(project)]
            _main_inner()
            goal_arg = mock_launch.call_args[0][1]
            assert goal_arg == "Build a web app"

    def test_goal_file(self, project):
        """--goal-file reads from the file."""
        goal_file = project / "my-goal.md"
        goal_file.write_text("Build an API server")

        with (
            patch("kodo.cli.launch_run") as mock_launch,
            patch("kodo.cli.run_intake_noninteractive", return_value=None),
        ):
            sys.argv = ["kodo", "--goal-file", str(goal_file), str(project)]
            _main_inner()
            goal_arg = mock_launch.call_args[0][1]
            assert goal_arg == "Build an API server"

    def test_goal_file_not_found(self, project):
        with pytest.raises(SystemExit):
            sys.argv = [
                "kodo",
                "--goal-file",
                str(project / "nonexistent.md"),
                str(project),
            ]
            _main_inner()

    def test_goal_file_empty(self, project):
        goal_file = project / "empty.md"
        goal_file.write_text("   ")

        with pytest.raises(SystemExit):
            sys.argv = ["kodo", "--goal-file", str(goal_file), str(project)]
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
            patch("kodo.cli.make_session", return_value=session),
            patch("kodo.cli.has_claude", return_value=True),
        ):
            result = run_intake_noninteractive(run_dir, "Build something")

        assert result is not None
        assert len(result.stages) == 1
        assert session.stats.queries == 1

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
            patch("kodo.cli.make_session", return_value=session),
            patch("kodo.cli.has_claude", return_value=True),
        ):
            result = run_intake_noninteractive(run_dir, "Build something")

        assert result is not None
        assert session.stats.queries == 2

    def test_returns_none_when_no_file_written(self, project):
        run_dir = RunDir.create(project, "test")
        session = make_scripted_session(["Hmm.", "Still nothing."], project)

        with (
            patch("kodo.cli.make_session", return_value=session),
            patch("kodo.cli.has_claude", return_value=True),
        ):
            result = run_intake_noninteractive(run_dir, "Vague goal")

        assert result is None

    def test_returns_none_when_no_backend(self, project):
        run_dir = RunDir.create(project, "test")
        with (
            patch("kodo.cli.has_claude", return_value=False),
            patch("kodo.cli.has_cursor", return_value=False),
        ):
            result = run_intake_noninteractive(run_dir, "Build something")

        assert result is None

    def test_no_input_calls(self, project):
        """Non-interactive intake must never call input()."""
        run_dir = RunDir.create(project, "test")
        session = make_scripted_session(["Done."], project)

        with (
            patch("kodo.cli.make_session", return_value=session),
            patch("kodo.cli.has_claude", return_value=True),
            patch("builtins.input", side_effect=AssertionError("input() called")),
        ):
            run_intake_noninteractive(run_dir, "Build something")


# ---------------------------------------------------------------------------
# TestNonInteractiveEndToEnd
# ---------------------------------------------------------------------------


class TestNonInteractiveEndToEnd:
    @pytest.fixture(autouse=True)
    def _fake_backends(self):
        with (
            patch("kodo.cli.has_claude", return_value=True),
            patch("kodo.cli.check_api_key", return_value=None),
        ):
            yield

    def test_no_interactive_prompts(self, project):
        """The full non-interactive flow must never call input() or questionary."""
        with (
            patch("kodo.cli.launch_run") as mock_launch,
            patch("kodo.cli.run_intake_noninteractive", return_value=None),
            patch(
                "builtins.input",
                side_effect=AssertionError("input() should not be called"),
            ),
            patch(
                "questionary.select",
                side_effect=AssertionError("questionary should not be called"),
            ),
        ):
            sys.argv = ["kodo", "--goal", "Build X", str(project)]
            _main_inner()
            mock_launch.assert_called_once()

    def test_params_passed_through(self, project):
        """CLI flags should be reflected in the params passed to launch_run."""
        with (
            patch("kodo.cli.launch_run") as mock_launch,
            patch("kodo.cli.run_intake_noninteractive", return_value=None),
        ):
            sys.argv = [
                "kodo",
                "--goal",
                "Build X",
                "--mode",
                "mission",
                "--exchanges",
                "42",
                "--cycles",
                "7",
                "--orchestrator",
                "api",
                "--orchestrator-model",
                "gemini-pro",
                str(project),
            ]
            _main_inner()

            params = mock_launch.call_args[0][2]
            assert params["mode"] == "mission"
            assert params["max_exchanges"] == 42
            assert params["max_cycles"] == 7
            assert params["orchestrator"] == "api"
            assert params["orchestrator_model"] == "gemini-pro"

    def test_skip_intake_flag(self, project):
        """--skip-intake should prevent intake from running."""
        with (
            patch("kodo.cli.launch_run"),
            patch("kodo.cli.run_intake_noninteractive") as mock_intake,
        ):
            sys.argv = [
                "kodo",
                "--goal",
                "Simple fix",
                "--skip-intake",
                str(project),
            ]
            _main_inner()
            mock_intake.assert_not_called()

    def test_resume_with_goal_errors(self):
        """--resume + --goal should be rejected."""
        with pytest.raises(SystemExit):
            sys.argv = ["kodo", "--resume", "--goal", "Build X"]
            _main_inner()

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
            patch("kodo.cli.launch_run") as mock_launch,
            patch("kodo.cli.run_intake_noninteractive") as mock_intake,
            patch("kodo.cli.RunDir.create", side_effect=create_with_plan),
        ):
            sys.argv = ["kodo", "--goal", "Build X", str(project)]
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
            patch("kodo.cli.has_claude", return_value=True),
            patch("kodo.cli.check_api_key", return_value=None),
        ):
            yield

    def test_improve_populates_goal_from_template(self, project):
        """--improve should construct goal text from _IMPROVE_GOAL template."""
        with (
            patch("kodo.cli.launch_run") as mock_launch,
            patch("kodo.cli.run_intake_noninteractive", return_value=None),
        ):
            sys.argv = ["kodo", "--improve", str(project)]
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
            patch("kodo.cli.launch_run"),
            patch("kodo.cli.run_intake_noninteractive") as mock_intake,
        ):
            sys.argv = ["kodo", "--improve", str(project)]
            _main_inner()
            mock_intake.assert_not_called()

    def test_improve_defaults_to_saga_mode(self, project):
        """--improve should default mode to saga."""
        with (
            patch("kodo.cli.launch_run") as mock_launch,
            patch("kodo.cli.run_intake_noninteractive", return_value=None),
        ):
            sys.argv = ["kodo", "--improve", str(project)]
            _main_inner()
            params = mock_launch.call_args[0][2]
            assert params["mode"] == "saga"

    def test_improve_respects_explicit_mode(self, project):
        """--improve should not override an explicitly set --mode."""
        with (
            patch("kodo.cli.launch_run") as mock_launch,
            patch("kodo.cli.run_intake_noninteractive", return_value=None),
        ):
            sys.argv = ["kodo", "--improve", "--mode", "mission", str(project)]
            _main_inner()
            params = mock_launch.call_args[0][2]
            assert params["mode"] == "mission"

    def test_improve_no_interactive_prompts(self, project):
        """--improve must never call input() or questionary."""
        with (
            patch("kodo.cli.launch_run"),
            patch("kodo.cli.run_intake_noninteractive", return_value=None),
            patch(
                "builtins.input",
                side_effect=AssertionError("input() should not be called"),
            ),
            patch(
                "questionary.select",
                side_effect=AssertionError("questionary should not be called"),
            ),
        ):
            sys.argv = ["kodo", "--improve", str(project)]
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
            patch("kodo.cli.launch_run") as mock_launch,
            patch("kodo.cli.run_intake_noninteractive", return_value=None),
        ):
            sys.argv = ["kodo", "--improve", str(project)]
            _main_inner()
            plan = mock_launch.call_args.kwargs.get(
                "plan",
                mock_launch.call_args[0][3]
                if len(mock_launch.call_args[0]) > 3
                else None,
            )
            assert plan is not None
            assert len(plan.stages) == 4

    def test_improve_plan_stage_order(self, project):
        """--improve stages should follow the right sequence."""
        with (
            patch("kodo.cli.launch_run") as mock_launch,
            patch("kodo.cli.run_intake_noninteractive", return_value=None),
        ):
            sys.argv = ["kodo", "--improve", str(project)]
            _main_inner()
            plan = mock_launch.call_args.kwargs.get(
                "plan",
                mock_launch.call_args[0][3]
                if len(mock_launch.call_args[0]) > 3
                else None,
            )
            names = [s.name for s in plan.stages]
            assert names == [
                "Baseline & Static Analysis",
                "Happy Path Integration Testing",
                "Exploratory & Adversarial Testing",
                "Fix & Report",
            ]


# ---------------------------------------------------------------------------
# TestBuildImprovePlan
# ---------------------------------------------------------------------------


class TestBuildImprovePlan:
    """Tests for _build_improve_plan() structure."""

    def test_has_four_stages(self):
        plan = _build_improve_plan("/tmp/report.md")
        assert len(plan.stages) == 4

    def test_stages_have_sequential_indices(self):
        plan = _build_improve_plan("/tmp/report.md")
        assert [s.index for s in plan.stages] == [1, 2, 3, 4]

    def test_report_path_in_final_stage(self):
        plan = _build_improve_plan("/tmp/my-report.md")
        last = plan.stages[-1]
        assert "/tmp/my-report.md" in last.description
        assert "/tmp/my-report.md" in last.acceptance_criteria

    def test_time_guidance_in_integration_stages(self):
        """Stages 2 and 3 should include time efficiency guidance."""
        plan = _build_improve_plan("/tmp/report.md")
        for stage in plan.stages[1:3]:
            assert "Mock or stub" in stage.description
            assert "30 seconds" in stage.description

    def test_time_guidance_not_in_static_stage(self):
        """Stage 1 (static analysis) should not have time guidance."""
        plan = _build_improve_plan("/tmp/report.md")
        assert "Mock or stub" not in plan.stages[0].description

    def test_all_stages_have_acceptance_criteria(self):
        plan = _build_improve_plan("/tmp/report.md")
        for stage in plan.stages:
            assert stage.acceptance_criteria, f"Stage {stage.index} missing criteria"

    def test_context_emphasizes_running_software(self):
        plan = _build_improve_plan("/tmp/report.md")
        assert "RUNNING" in plan.context

    def test_stages_2_and_3_are_parallel(self):
        """Stages 2 and 3 should share the same parallel_group."""
        plan = _build_improve_plan("/tmp/report.md")
        assert plan.stages[1].parallel_group == 1
        assert plan.stages[2].parallel_group == 1

    def test_stages_1_and_4_are_sequential(self):
        """Stages 1 and 4 should have no parallel_group (sequential)."""
        plan = _build_improve_plan("/tmp/report.md")
        assert plan.stages[0].parallel_group is None
        assert plan.stages[3].parallel_group is None

    def test_parallel_stage_descriptions_mention_findings_file(self):
        """Stage descriptions should tell agents where to write findings."""
        plan = _build_improve_plan("/tmp/report.md")
        assert "findings-happy-path.md" in plan.stages[1].description
        assert "findings-adversarial.md" in plan.stages[2].description

    def test_fix_stage_references_both_findings_files(self):
        """Stage 4 should tell agents to read both findings files."""
        plan = _build_improve_plan("/tmp/report.md")
        fix_stage = plan.stages[3]
        assert "findings-happy-path.md" in fix_stage.description
        assert "findings-adversarial.md" in fix_stage.description

    def test_parallel_stages_instruct_no_source_modification(self):
        """Read-only parallel stages should explicitly say not to modify code."""
        plan = _build_improve_plan("/tmp/report.md")
        for stage in plan.stages[1:3]:
            assert "Do NOT modify source code" in stage.description


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
# TestDetectProjectType
# ---------------------------------------------------------------------------


class TestDetectProjectType:
    def test_pyproject_library(self, tmp_path):
        """pyproject.toml with [project] but no [project.scripts] → LIBRARY."""
        (tmp_path / "pyproject.toml").write_text(
            "[project]\nname = 'mylib'\nversion = '1.0'\n"
        )
        assert _detect_project_type(tmp_path) == ProjectType.LIBRARY

    def test_pyproject_app_with_scripts(self, tmp_path):
        """pyproject.toml with [project.scripts] → APP."""
        (tmp_path / "pyproject.toml").write_text(
            "[project]\nname = 'myapp'\n\n[project.scripts]\nmyapp = 'myapp:main'\n"
        )
        assert _detect_project_type(tmp_path) == ProjectType.APP

    def test_package_json_library(self, tmp_path):
        """package.json with main but no bin → LIBRARY."""
        (tmp_path / "package.json").write_text(
            json.dumps({"name": "mylib", "main": "index.js"})
        )
        assert _detect_project_type(tmp_path) == ProjectType.LIBRARY

    def test_package_json_cli(self, tmp_path):
        """package.json with main and bin → APP."""
        (tmp_path / "package.json").write_text(
            json.dumps({"name": "mycli", "main": "index.js", "bin": {"mycli": "cli.js"}})
        )
        assert _detect_project_type(tmp_path) == ProjectType.APP

    def test_cargo_toml_lib(self, tmp_path):
        """Cargo.toml with [lib] but no [[bin]] → LIBRARY."""
        (tmp_path / "Cargo.toml").write_text(
            '[package]\nname = "mylib"\n\n[lib]\nname = "mylib"\n'
        )
        assert _detect_project_type(tmp_path) == ProjectType.LIBRARY

    def test_cargo_toml_bin(self, tmp_path):
        """Cargo.toml with [lib] and [[bin]] → APP."""
        (tmp_path / "Cargo.toml").write_text(
            '[package]\nname = "myapp"\n\n[lib]\nname = "myapp"\n\n[[bin]]\nname = "myapp"\n'
        )
        assert _detect_project_type(tmp_path) == ProjectType.APP

    def test_go_library(self, tmp_path):
        """go.mod without main.go or cmd/ → LIBRARY."""
        (tmp_path / "go.mod").write_text("module example.com/mylib\n")
        assert _detect_project_type(tmp_path) == ProjectType.LIBRARY

    def test_go_app(self, tmp_path):
        """go.mod with main.go → APP."""
        (tmp_path / "go.mod").write_text("module example.com/myapp\n")
        (tmp_path / "main.go").write_text("package main\n")
        assert _detect_project_type(tmp_path) == ProjectType.APP

    def test_empty_dir_defaults_to_app(self, tmp_path):
        """Empty directory → APP (safe fallback)."""
        assert _detect_project_type(tmp_path) == ProjectType.APP

    def test_python_package_with_examples(self, tmp_path):
        """Python package with __init__.py + examples/ but no metadata → LIBRARY."""
        pkg = tmp_path / "mypkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (tmp_path / "examples").mkdir()
        assert _detect_project_type(tmp_path) == ProjectType.LIBRARY

    def test_python_package_without_examples(self, tmp_path):
        """Python package with __init__.py but no examples/ or docs/ → APP."""
        pkg = tmp_path / "mypkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        assert _detect_project_type(tmp_path) == ProjectType.APP

    def test_package_json_exports(self, tmp_path):
        """package.json with exports but no bin → LIBRARY."""
        (tmp_path / "package.json").write_text(
            json.dumps({"name": "mylib", "exports": {".": "./src/index.js"}})
        )
        assert _detect_project_type(tmp_path) == ProjectType.LIBRARY


# ---------------------------------------------------------------------------
# TestLibraryImprovePlan
# ---------------------------------------------------------------------------


class TestLibraryImprovePlan:
    """Tests for _build_improve_plan() with library project type."""

    def test_has_four_stages(self):
        plan = _build_improve_plan(
            "/tmp/report.md", ProjectType.LIBRARY, Path("/tmp/mylib")
        )
        assert len(plan.stages) == 4

    def test_stages_have_sequential_indices(self):
        plan = _build_improve_plan(
            "/tmp/report.md", ProjectType.LIBRARY, Path("/tmp/mylib")
        )
        assert [s.index for s in plan.stages] == [1, 2, 3, 4]

    def test_has_consumer_project_stage(self):
        plan = _build_improve_plan(
            "/tmp/report.md", ProjectType.LIBRARY, Path("/tmp/mylib")
        )
        names = [s.name for s in plan.stages]
        assert "Consumer Project Testing" in names

    def test_has_api_misuse_stage(self):
        plan = _build_improve_plan(
            "/tmp/report.md", ProjectType.LIBRARY, Path("/tmp/mylib")
        )
        names = [s.name for s in plan.stages]
        assert "API Misuse & Error Quality" in names

    def test_stages_2_and_3_parallel(self):
        plan = _build_improve_plan(
            "/tmp/report.md", ProjectType.LIBRARY, Path("/tmp/mylib")
        )
        assert plan.stages[1].parallel_group == 1
        assert plan.stages[2].parallel_group == 1

    def test_stages_1_and_4_sequential(self):
        plan = _build_improve_plan(
            "/tmp/report.md", ProjectType.LIBRARY, Path("/tmp/mylib")
        )
        assert plan.stages[0].parallel_group is None
        assert plan.stages[3].parallel_group is None

    def test_consumer_stage_references_project_dir(self):
        plan = _build_improve_plan(
            "/tmp/report.md", ProjectType.LIBRARY, Path("/tmp/mylib")
        )
        consumer = plan.stages[1]
        assert "/tmp/mylib" in consumer.description

    def test_fix_stage_references_all_findings(self):
        plan = _build_improve_plan(
            "/tmp/report.md", ProjectType.LIBRARY, Path("/tmp/mylib")
        )
        fix_stage = plan.stages[3]
        assert "findings-api-audit.md" in fix_stage.description
        assert "findings-consumer-project.md" in fix_stage.description
        assert "findings-api-misuse.md" in fix_stage.description

    def test_fix_stage_has_dx_notes_section(self):
        plan = _build_improve_plan(
            "/tmp/report.md", ProjectType.LIBRARY, Path("/tmp/mylib")
        )
        fix_stage = plan.stages[3]
        assert "Developer Experience Notes" in fix_stage.description

    def test_context_mentions_library(self):
        plan = _build_improve_plan(
            "/tmp/report.md", ProjectType.LIBRARY, Path("/tmp/mylib")
        )
        assert "library" in plan.context.lower()

    def test_all_stages_have_acceptance_criteria(self):
        plan = _build_improve_plan(
            "/tmp/report.md", ProjectType.LIBRARY, Path("/tmp/mylib")
        )
        for stage in plan.stages:
            assert stage.acceptance_criteria, f"Stage {stage.index} missing criteria"

    def test_app_type_still_returns_app_plan(self):
        """Passing APP type returns the original app plan."""
        plan = _build_improve_plan("/tmp/report.md", ProjectType.APP)
        names = [s.name for s in plan.stages]
        assert "Happy Path Integration Testing" in names
