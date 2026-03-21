"""Tests for kodo.cli._intake — goal input, parsing, sessions, and offer menu."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kodo.cli._intake import (
    _build_intake_prompt,
    _close_session,
    _load_goal_plan,
    _looks_staged,
    _offer_intake,
    _parse_goal_plan,
    _read_intake_output,
    _run_parallelism_pass,
    _stdin_has_data,
    get_goal,
    run_single_turn_plan,
)
from kodo.log import RunDir
from kodo.orchestrators.base import GoalPlan
from kodo.sessions.base import Session
from tests.conftest import make_scripted_session


# ---------------------------------------------------------------------------
# _close_session
# ---------------------------------------------------------------------------


class TestCloseSession:
    def test_normal_close(self):
        """terminate + close called without error."""
        session = MagicMock()
        _close_session(session)
        session.terminate.assert_called_once()
        session.close.assert_called_once()

    def test_terminate_error_ignored(self):
        """OSError from terminate is caught and logged."""
        session = MagicMock()
        session.terminate.side_effect = OSError("terminated already")
        _close_session(session)
        session.close.assert_called_once()

    def test_close_error_ignored(self):
        """RuntimeError from close is caught and logged."""
        session = MagicMock()
        session.close.side_effect = RuntimeError("already closed")
        _close_session(session)
        session.terminate.assert_called_once()


# ---------------------------------------------------------------------------
# _build_intake_prompt
# ---------------------------------------------------------------------------


class TestBuildIntakePrompt:
    def test_staged_prompt(self):
        prompt = _build_intake_prompt("/tmp/goal-plan.json", staged=True)
        assert "stages" in prompt
        assert "/tmp/goal-plan.json" in prompt

    def test_non_staged_prompt(self):
        prompt = _build_intake_prompt("/tmp/goal-refined.md", staged=False)
        assert "refined" in prompt
        assert "/tmp/goal-refined.md" in prompt


# ---------------------------------------------------------------------------
# _stdin_has_data
# ---------------------------------------------------------------------------


class TestStdinHasData:
    def test_no_data_returns_false(self):
        """With no data on stdin (timeout), returns False."""
        # In test environment, stdin likely has no pending data
        result = _stdin_has_data(timeout=0.01)
        assert result is False

    def test_handles_value_error(self):
        """ValueError from select → False."""
        with patch("select.select", autospec=True, side_effect=ValueError):
            result = _stdin_has_data()
        assert result is False

    def test_handles_os_error(self):
        """OSError from select → False."""
        with patch("select.select", autospec=True, side_effect=OSError):
            result = _stdin_has_data()
        assert result is False


# ---------------------------------------------------------------------------
# get_goal
# ---------------------------------------------------------------------------


class TestGetGoal:
    def test_single_line_goal(self, capsys):
        """Single line followed by empty line."""
        inputs = iter(["Build a REST API", ""])

        with patch(
            "builtins.input", autospec=True, side_effect=lambda *a: next(inputs)
        ):
            with patch(
                "kodo.cli._intake._stdin_has_data", autospec=True, return_value=False
            ):
                result = get_goal()

        assert result == "Build a REST API"

    def test_multiline_goal(self, capsys):
        """Multiple lines followed by empty line."""
        inputs = iter(["Build an API", "with auth", "and tests", ""])

        with (
            patch("builtins.input", autospec=True, side_effect=lambda *a: next(inputs)),
            patch(
                "kodo.cli._intake._stdin_has_data", autospec=True, return_value=False
            ),
        ):
            result = get_goal()

        assert "Build an API" in result
        assert "with auth" in result
        assert "and tests" in result

    def test_eof_ends_input(self, capsys):
        """EOFError should end input gracefully."""
        inputs_called = 0

        def mock_input(*a):
            nonlocal inputs_called
            inputs_called += 1
            if inputs_called == 1:
                return "Build something"
            raise EOFError

        with patch("builtins.input", autospec=True, side_effect=mock_input):
            result = get_goal()

        assert result == "Build something"

    def test_empty_goal_exits(self, capsys):
        """Empty goal should exit with code 1."""
        with (
            patch("builtins.input", autospec=True, side_effect=lambda *a: ""),
            patch(
                "kodo.cli._intake._stdin_has_data", autospec=True, return_value=False
            ),
            pytest.raises(SystemExit, match="1"),
        ):
            get_goal()

    def test_paste_detection_continues_through_blank(self, capsys):
        """When stdin has buffered data, blank line doesn't end input."""
        inputs = iter(["Line 1", "", "Line 3", ""])
        stdin_data = iter([True, False])  # first blank: buffered; second: not

        with (
            patch("builtins.input", autospec=True, side_effect=lambda *a: next(inputs)),
            patch(
                "kodo.cli._intake._stdin_has_data",
                autospec=True,
                side_effect=lambda **kw: next(stdin_data),
            ),
        ):
            result = get_goal()

        assert "Line 1" in result
        assert "Line 3" in result


# ---------------------------------------------------------------------------
# _looks_staged
# ---------------------------------------------------------------------------


class TestLooksStaged:
    def test_numbered_steps_detected(self):
        text = "1. Setup environment\n2. Write tests\n3. Deploy"
        assert _looks_staged(text) is True

    def test_single_numbered_line_not_staged(self):
        text = "1. Just one step"
        assert _looks_staged(text) is False

    def test_plain_text_not_staged(self):
        text = "Build a REST API with authentication"
        assert _looks_staged(text) is False

    def test_exactly_two_numbered_lines_is_staged(self):
        """Boundary: exactly 2 numbered items meets the threshold."""
        text = "1) First step\n2) Second step"
        assert _looks_staged(text) is True

    def test_parenthetical_numbering_detected(self):
        """Detects 1) 2) 3) style numbering."""
        text = "1) Build models\n2) Write API\n3) Deploy"
        assert _looks_staged(text) is True

    def test_indented_numbering_detected(self):
        """Detects indented numbered lists."""
        text = "  1. First\n  2. Second\n  3. Third"
        assert _looks_staged(text) is True


# ---------------------------------------------------------------------------
# _parse_goal_plan
# ---------------------------------------------------------------------------


class TestParseGoalPlan:
    def test_valid_plan(self):
        raw = {
            "context": "Test project",
            "stages": [
                {
                    "index": 1,
                    "name": "Setup",
                    "description": "Initialize project",
                    "acceptance_criteria": "Project compiles",
                },
            ],
        }
        plan = _parse_goal_plan(raw)
        assert plan.context == "Test project"
        assert len(plan.stages) == 1
        assert plan.stages[0].name == "Setup"
        assert plan.stages[0].description == "Initialize project"
        assert plan.stages[0].acceptance_criteria == "Project compiles"
        assert plan.stages[0].index == 1

    def test_empty_context_returns_empty(self):
        raw = {"context": "", "stages": []}
        plan = _parse_goal_plan(raw)
        assert plan.stages == []

    def test_missing_context_returns_empty(self):
        raw = {"stages": []}
        plan = _parse_goal_plan(raw)
        assert plan.stages == []

    def test_skips_non_dict_stages(self):
        raw = {"context": "Test", "stages": ["not a dict", 42]}
        plan = _parse_goal_plan(raw)
        assert plan.stages == []

    def test_skips_incomplete_stages(self):
        raw = {
            "context": "Test",
            "stages": [
                {"index": 1, "name": "S1"},  # missing description and criteria
                {
                    "index": 2,
                    "name": "S2",
                    "description": "D",
                    "acceptance_criteria": "C",
                },
            ],
        }
        plan = _parse_goal_plan(raw)
        assert len(plan.stages) == 1
        assert plan.stages[0].name == "S2"

    def test_invalid_index_raises(self):
        raw = {
            "context": "Test",
            "stages": [
                {
                    "index": "abc",
                    "name": "S",
                    "description": "D",
                    "acceptance_criteria": "C",
                },
            ],
        }
        with pytest.raises(ValueError, match="positive integer"):
            _parse_goal_plan(raw)

    def test_negative_index_raises(self):
        raw = {
            "context": "Test",
            "stages": [
                {
                    "index": -1,
                    "name": "S",
                    "description": "D",
                    "acceptance_criteria": "C",
                },
            ],
        }
        with pytest.raises(ValueError, match="positive"):
            _parse_goal_plan(raw)

    def test_duplicate_index_raises(self):
        raw = {
            "context": "Test",
            "stages": [
                {
                    "index": 1,
                    "name": "S1",
                    "description": "D1",
                    "acceptance_criteria": "C1",
                },
                {
                    "index": 1,
                    "name": "S2",
                    "description": "D2",
                    "acceptance_criteria": "C2",
                },
            ],
        }
        with pytest.raises(ValueError, match="Duplicate"):
            _parse_goal_plan(raw)

    def test_parallel_group_parsed(self):
        raw = {
            "context": "Test",
            "stages": [
                {
                    "index": 1,
                    "name": "S",
                    "description": "D",
                    "acceptance_criteria": "C",
                    "parallel_group": 2,
                },
            ],
        }
        plan = _parse_goal_plan(raw)
        assert plan.stages[0].parallel_group == 2

    def test_letter_parallel_group_coerced(self):
        """Letter parallel_group values like 'A', 'B' are coerced to ints."""
        raw = {
            "context": "Test",
            "stages": [
                {
                    "index": 1,
                    "name": "S1",
                    "description": "D",
                    "acceptance_criteria": "C",
                    "parallel_group": "A",
                },
                {
                    "index": 2,
                    "name": "S2",
                    "description": "D",
                    "acceptance_criteria": "C",
                    "parallel_group": "B",
                },
                {
                    "index": 3,
                    "name": "S3",
                    "description": "D",
                    "acceptance_criteria": "C",
                    "parallel_group": "A",
                },
            ],
        }
        plan = _parse_goal_plan(raw)
        assert plan.stages[0].parallel_group == plan.stages[2].parallel_group
        assert plan.stages[0].parallel_group != plan.stages[1].parallel_group

    def test_parallel_group_string_coerced_to_int(self):
        """parallel_group string "1" is coerced to int."""
        raw = {
            "context": "Test",
            "stages": [
                {
                    "index": 1,
                    "name": "S",
                    "description": "D",
                    "acceptance_criteria": "C",
                    "parallel_group": "1",
                },
            ],
        }
        plan = _parse_goal_plan(raw)
        assert plan.stages[0].parallel_group == 1
        assert isinstance(plan.stages[0].parallel_group, int)

    def test_browser_testing_parsed(self):
        raw = {
            "context": "Test",
            "stages": [
                {
                    "index": 1,
                    "name": "S",
                    "description": "D",
                    "acceptance_criteria": "C",
                    "browser_testing": True,
                },
            ],
        }
        plan = _parse_goal_plan(raw)
        assert plan.stages[0].browser_testing is True

    def test_persist_changes_parsed(self):
        raw = {
            "context": "Test",
            "stages": [
                {
                    "index": 1,
                    "name": "S",
                    "description": "D",
                    "acceptance_criteria": "C",
                    "persist_changes": True,
                },
            ],
        }
        plan = _parse_goal_plan(raw)
        assert plan.stages[0].persist_changes is True

    def test_skipped_stage_with_name_logged(self):
        """Stage with index+name but missing description should be logged and skipped."""
        raw = {
            "context": "Test",
            "stages": [
                {"index": 1, "name": "Incomplete Stage"},  # missing desc + criteria
            ],
        }
        plan = _parse_goal_plan(raw)
        assert plan.stages == []

    def test_string_index_coerced_to_int(self):
        """String index "1" is coerced to int."""
        raw = {
            "context": "Test",
            "stages": [
                {
                    "index": "1",
                    "name": "S",
                    "description": "D",
                    "acceptance_criteria": "C",
                },
            ],
        }
        plan = _parse_goal_plan(raw)
        assert plan.stages[0].index == 1
        assert isinstance(plan.stages[0].index, int)

    def test_zero_index_raises(self):
        """index=0 is invalid (must be positive)."""
        raw = {
            "context": "Test",
            "stages": [
                {
                    "index": 0,
                    "name": "S",
                    "description": "D",
                    "acceptance_criteria": "C",
                },
            ],
        }
        with pytest.raises(ValueError, match="positive"):
            _parse_goal_plan(raw)

    def test_multi_stage_preserves_order_and_fields(self):
        """Multiple stages preserve order, description, and acceptance_criteria."""
        raw = {
            "context": "Multi",
            "stages": [
                {
                    "index": 1,
                    "name": "A",
                    "description": "desc-A",
                    "acceptance_criteria": "ac-A",
                },
                {
                    "index": 2,
                    "name": "B",
                    "description": "desc-B",
                    "acceptance_criteria": "ac-B",
                },
            ],
        }
        plan = _parse_goal_plan(raw)
        assert len(plan.stages) == 2
        assert plan.stages[0].description == "desc-A"
        assert plan.stages[0].acceptance_criteria == "ac-A"
        assert plan.stages[1].description == "desc-B"
        assert plan.stages[1].acceptance_criteria == "ac-B"


# ---------------------------------------------------------------------------
# _load_goal_plan
# ---------------------------------------------------------------------------


class TestLoadGoalPlan:
    def test_loads_valid_plan(self, tmp_path):
        run_dir = RunDir.create(tmp_path, "test")
        plan_data = {
            "context": "Test",
            "stages": [
                {
                    "index": 1,
                    "name": "S1",
                    "description": "D",
                    "acceptance_criteria": "C",
                },
            ],
        }
        run_dir.goal_plan_file.write_text(json.dumps(plan_data))

        result = _load_goal_plan(run_dir)
        assert result is not None
        assert len(result.stages) == 1

    def test_returns_none_when_no_file(self, tmp_path):
        run_dir = RunDir.create(tmp_path, "test")
        assert _load_goal_plan(run_dir) is None

    def test_returns_none_on_invalid_json(self, tmp_path):
        run_dir = RunDir.create(tmp_path, "test")
        run_dir.goal_plan_file.write_text("not json!")
        assert _load_goal_plan(run_dir) is None

    def test_returns_none_on_non_dict(self, tmp_path):
        run_dir = RunDir.create(tmp_path, "test")
        run_dir.goal_plan_file.write_text('"just a string"')
        assert _load_goal_plan(run_dir) is None

    def test_returns_none_on_empty_stages(self, tmp_path):
        run_dir = RunDir.create(tmp_path, "test")
        run_dir.goal_plan_file.write_text(json.dumps({"context": "Test", "stages": []}))
        assert _load_goal_plan(run_dir) is None


# ---------------------------------------------------------------------------
# _read_intake_output
# ---------------------------------------------------------------------------


class TestReadIntakeOutput:
    def test_reads_staged_plan(self, tmp_path):
        plan_data = {
            "context": "Test",
            "stages": [
                {
                    "index": 1,
                    "name": "S1",
                    "description": "D",
                    "acceptance_criteria": "C",
                },
            ],
        }
        output_file = tmp_path / "goal-plan.json"
        output_file.write_text(json.dumps(plan_data))

        result = _read_intake_output(output_file, staged=True)
        assert isinstance(result, GoalPlan)
        assert len(result.stages) == 1

    def test_reads_refined_goal(self, tmp_path):
        output_file = tmp_path / "goal-refined.md"
        output_file.write_text("Refined goal text")

        result = _read_intake_output(output_file, staged=False)
        assert result == "Refined goal text"

    def test_returns_none_on_empty_refined(self, tmp_path):
        output_file = tmp_path / "goal-refined.md"
        output_file.write_text("")

        result = _read_intake_output(output_file, staged=False)
        assert result is None

    def test_returns_none_on_unreadable_refined(self, tmp_path):
        output_file = tmp_path / "goal-refined.md"
        output_file.write_text("content")

        original_read_text = Path.read_text

        def patched_read_text(self, *args, **kwargs):
            if str(self) == str(output_file):
                raise OSError("permission denied")
            return original_read_text(self, *args, **kwargs)

        with patch.object(Path, "read_text", patched_read_text):
            result = _read_intake_output(output_file, staged=False)
        assert result is None

    def test_returns_none_on_invalid_json_staged(self, tmp_path, capsys):
        output_file = tmp_path / "goal-plan.json"
        output_file.write_text("not json!")

        result = _read_intake_output(output_file, staged=True)
        assert result is None
        assert "could not read" in capsys.readouterr().out

    def test_runs_parallelism_pass_when_no_parallel_groups(self, tmp_path):
        """When plan has no parallel groups and session is provided, runs parallelism pass."""
        plan_data = {
            "context": "Test",
            "stages": [
                {
                    "index": 1,
                    "name": "S1",
                    "description": "D",
                    "acceptance_criteria": "C",
                },
            ],
        }
        output_file = tmp_path / "goal-plan.json"
        output_file.write_text(json.dumps(plan_data))

        session = MagicMock(spec=Session)
        project_dir = tmp_path

        with patch(
            "kodo.cli._intake._run_parallelism_pass", autospec=True
        ) as mock_pass:
            _read_intake_output(
                output_file,
                staged=True,
                session=session,
                project_dir=project_dir,
            )

        mock_pass.assert_called_once()

    def test_skips_parallelism_pass_when_parallel_groups_exist(self, tmp_path):
        """When plan already has parallel groups, skip parallelism pass."""
        plan_data = {
            "context": "Test",
            "stages": [
                {
                    "index": 1,
                    "name": "S1",
                    "description": "D",
                    "acceptance_criteria": "C",
                    "parallel_group": 1,
                },
            ],
        }
        output_file = tmp_path / "goal-plan.json"
        output_file.write_text(json.dumps(plan_data))

        session = MagicMock(spec=Session)
        project_dir = tmp_path

        with patch(
            "kodo.cli._intake._run_parallelism_pass", autospec=True
        ) as mock_pass:
            _read_intake_output(
                output_file,
                staged=True,
                session=session,
                project_dir=project_dir,
            )

        mock_pass.assert_not_called()


# ---------------------------------------------------------------------------
# _run_parallelism_pass
# ---------------------------------------------------------------------------


class TestRunParallelismPass:
    def test_normal_pass(self, tmp_path):
        """Parallelism pass sends query to session."""
        session = MagicMock()
        _run_parallelism_pass(session, tmp_path)
        session.query.assert_called_once()

    def test_exception_handled(self, tmp_path, capsys):
        """Exception during parallelism pass is caught and reported."""
        session = MagicMock()
        session.query.side_effect = RuntimeError("session died")

        _run_parallelism_pass(session, tmp_path)
        out = capsys.readouterr().out
        assert "Parallelism pass failed" in out


# ---------------------------------------------------------------------------
# run_single_turn_plan
# ---------------------------------------------------------------------------


class TestRunSingleTurnPlan:
    def test_no_backend_returns_none(self, tmp_path):
        """When no backend is available, returns None."""
        run_dir = RunDir.create(tmp_path, "test")

        with patch(
            "kodo.cli._intake.preferred_backend", autospec=True, return_value=None
        ):
            result = run_single_turn_plan(
                run_dir, system_prompt="test", initial_message="go"
            )

        assert result is None

    def test_file_written_on_first_query(self, tmp_path):
        """When plan file is written on first query, returns plan."""
        run_dir = RunDir.create(tmp_path, "test")
        plan_data = {
            "context": "Test",
            "stages": [
                {
                    "index": 1,
                    "name": "S1",
                    "description": "D",
                    "acceptance_criteria": "C",
                },
            ],
        }
        session = make_scripted_session(
            ["Plan created!"],
            tmp_path,
            write_file={
                "on_query": 0,
                "path": str(run_dir.goal_plan_file),
                "content": json.dumps(plan_data),
            },
        )

        with (
            patch(
                "kodo.cli._intake.preferred_backend",
                autospec=True,
                return_value="claude",
            ),
            patch("kodo.cli._intake.make_session", autospec=True, return_value=session),
        ):
            result = run_single_turn_plan(
                run_dir, system_prompt="test", initial_message="go"
            )

        assert result is not None
        assert isinstance(result, GoalPlan)

    def test_finalize_fallback_when_no_file_first_query(self, tmp_path):
        """When file not written on first query, sends finalize query."""
        run_dir = RunDir.create(tmp_path, "test")
        plan_data = {
            "context": "Test",
            "stages": [
                {
                    "index": 1,
                    "name": "S1",
                    "description": "D",
                    "acceptance_criteria": "C",
                },
            ],
        }
        session = make_scripted_session(
            ["Analyzing...", "Plan written!"],
            tmp_path,
            write_file={
                "on_query": 1,
                "path": str(run_dir.goal_plan_file),
                "content": json.dumps(plan_data),
            },
        )

        with (
            patch(
                "kodo.cli._intake.preferred_backend",
                autospec=True,
                return_value="claude",
            ),
            patch("kodo.cli._intake.make_session", autospec=True, return_value=session),
        ):
            result = run_single_turn_plan(
                run_dir, system_prompt="test", initial_message="go"
            )

        assert result is not None
        # First query + finalize + parallelism pass
        assert session.stats.queries == 3


# ---------------------------------------------------------------------------
# _offer_intake
# ---------------------------------------------------------------------------


class TestOfferIntake:
    def test_no_backend_skips(self, tmp_path, capsys):
        """No available backend → returns (None, None)."""
        run_dir = RunDir.create(tmp_path, "test")

        with patch(
            "kodo.cli._intake.preferred_backend", autospec=True, return_value=None
        ):
            result, session = _offer_intake(run_dir, "Build X")

        assert result is None
        assert session is None
        assert "Skipping" in capsys.readouterr().out

    def test_skip_option_returns_none(self, tmp_path):
        """User selects 'Skip' → (None, None)."""
        run_dir = RunDir.create(tmp_path, "test")

        with (
            patch(
                "kodo.cli._intake.preferred_backend",
                autospec=True,
                return_value="claude",
            ),
            patch("kodo.cli._intake._select_one", autospec=True, return_value="Skip"),
        ):
            result, session = _offer_intake(run_dir, "Build X")

        assert result is None
        assert session is None

    def test_quick_refine_calls_auto(self, tmp_path):
        """User selects 'Quick refine' → calls run_intake_auto."""
        run_dir = RunDir.create(tmp_path, "test")

        with (
            patch(
                "kodo.cli._intake.preferred_backend",
                autospec=True,
                return_value="claude",
            ),
            patch(
                "kodo.cli._intake._select_one",
                autospec=True,
                return_value="Quick refine — surfaces implicit constraints, no conversation",
            ),
            patch(
                "builtins.input", autospec=True, return_value=""
            ),  # Y default for permissions confirm
            patch(
                "kodo.cli._intake.run_intake_auto",
                autospec=True,
                return_value="Refined goal",
            ) as mock_auto,
        ):
            result, session = _offer_intake(run_dir, "Build X")

        assert result == "Refined goal"
        assert session is None
        mock_auto.assert_called_once()

    def test_interview_with_staged_goal(self, tmp_path):
        """Interview option with staged-looking goal defaults to staged=True."""
        run_dir = RunDir.create(tmp_path, "test")

        with (
            patch(
                "kodo.cli._intake.preferred_backend",
                autospec=True,
                return_value="claude",
            ),
            patch(
                "kodo.cli._intake._select_one",
                autospec=True,
                return_value="Interview — interactive Q&A, optionally break into stages",
            ),
            patch(
                "kodo.cli._intake.available_backend_names",
                autospec=True,
                return_value=["Claude"],
            ),
            patch(
                "builtins.input", autospec=True, return_value=""
            ),  # Y default for staged
            patch(
                "kodo.cli._intake.run_intake_chat",
                autospec=True,
                return_value=(None, None),
            ) as mock_chat,
        ):
            _offer_intake(run_dir, "1. Setup\n2. Build\n3. Deploy")

        mock_chat.assert_called_once()
        # staged should be True (user hit Enter on Y default for staged-looking goal)
        assert (
            mock_chat.call_args[1].get(
                "staged",
                mock_chat.call_args[0][3] if len(mock_chat.call_args[0]) > 3 else None,
            )
            is True
        )

    def test_interview_with_multiple_backends(self, tmp_path):
        """When multiple backends available, user selects one."""
        run_dir = RunDir.create(tmp_path, "test")

        select_returns = iter(
            [
                "Interview — interactive Q&A, optionally break into stages",
                "Claude",  # backend selection
            ]
        )

        with (
            patch(
                "kodo.cli._intake.preferred_backend",
                autospec=True,
                return_value="claude",
            ),
            patch(
                "kodo.cli._intake._select_one",
                autospec=True,
                side_effect=lambda *a, **kw: next(select_returns),
            ),
            patch(
                "kodo.cli._intake.available_backend_names",
                autospec=True,
                return_value=["Claude", "Cursor"],
            ),
            patch(
                "builtins.input", autospec=True, side_effect=["", "n"]
            ),  # confirm permissions, don't stage
            patch(
                "kodo.cli._intake.run_intake_chat",
                autospec=True,
                return_value=(None, None),
            ) as mock_chat,
        ):
            _offer_intake(run_dir, "Build something simple")

        mock_chat.assert_called_once()
        # Backend should be "claude" (mapped from "Claude")
        assert mock_chat.call_args[0][0] == "claude"
