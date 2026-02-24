"""Tests for the intake interview flow in kodo.cli."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.conftest import FakeSession, make_scripted_session
from kodo.cli import run_intake_auto, run_intake_chat
from kodo.log import RunDir


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Temporary project directory."""
    return tmp_path


class TestIntakeInterviewLoop:
    """The interview should continue until /done or empty line, not exit on file creation."""

    def test_continues_after_response_until_done(self, project):
        """Bug regression: interview must not exit just because agent responded."""
        run_dir = RunDir.create(project, "test")
        session = make_scripted_session(
            responses=["What tech stack?", "Got it, any constraints?", "Thanks!"],
            project_dir=project,
        )

        # User answers question 1, then types /done
        inputs = iter(["We use React", "/done"])

        with (
            patch("kodo.cli._intake.make_session", return_value=session),
            patch("builtins.input", side_effect=lambda *a: next(inputs)),
        ):
            run_intake_chat("claude", run_dir, "Build a web app", staged=False)

        # Session should have received: initial goal, user answer, finalize message
        assert session.stats.queries == 3

    def test_continues_after_response_until_empty_line(self, project):
        """Empty line should also end the interview."""
        run_dir = RunDir.create(project, "test")
        session = make_scripted_session(
            responses=["What tech stack?", "Summary written."],
            project_dir=project,
        )

        inputs = iter(["React and Node", ""])

        with (
            patch("kodo.cli._intake.make_session", return_value=session),
            patch("builtins.input", side_effect=lambda *a: next(inputs)),
        ):
            run_intake_chat("claude", run_dir, "Build an API", staged=False)

        # initial + user answer + finalize
        assert session.stats.queries == 3

    def test_multiple_exchanges_before_done(self, project):
        """User should be able to have multiple exchanges."""
        run_dir = RunDir.create(project, "test")
        session = make_scripted_session(
            responses=["Q1?", "Q2?", "Q3?", "Finalizing..."],
            project_dir=project,
        )

        inputs = iter(["answer1", "answer2", "answer3", "/done"])

        with (
            patch("kodo.cli._intake.make_session", return_value=session),
            patch("builtins.input", side_effect=lambda *a: next(inputs)),
        ):
            run_intake_chat("claude", run_dir, "My goal", staged=False)

        # initial + 3 answers + finalize
        assert session.stats.queries == 5

    def test_auto_exits_when_file_written_on_first_turn(self, project):
        """When the agent writes the output file on the first turn, the
        interview should auto-exit without waiting for user input."""
        run_dir = RunDir.create(project, "test")
        session = make_scripted_session(
            responses=[
                "Looks clear! I've written the plan.",
            ],
            project_dir=project,
            write_file={
                "on_query": 0,
                "path": str(run_dir.goal_plan_file),
                "content": json.dumps(
                    {
                        "context": "Rust game",
                        "stages": [
                            {
                                "index": 1,
                                "name": "Stage 1",
                                "description": "Do stuff",
                                "acceptance_criteria": "Done",
                                "browser_testing": False,
                            }
                        ],
                    }
                ),
            },
        )

        with (
            patch("kodo.cli._intake.make_session", return_value=session),
            patch(
                "builtins.input", side_effect=AssertionError("should not prompt user")
            ),
        ):
            result = run_intake_chat("claude", run_dir, "Build a game", staged=True)

        # Only the initial query — no user input waited for
        assert session.stats.queries == 1
        assert result is not None


class TestIntakeOutputFile:
    """Test file detection and finalization behavior."""

    def test_finalize_query_sent_when_no_file_on_done(self, project):
        """When user types /done without file existing, send finalize message."""
        run_dir = RunDir.create(project, "test")
        # File written on query index 2 (the finalize query)
        session = make_scripted_session(
            responses=["What framework?", "Writing output..."],
            project_dir=project,
            write_file={
                "on_query": 2,
                "path": str(run_dir.goal_refined_file),
                "content": "Refined goal text",
            },
        )

        inputs = iter(["Django", "/done"])

        with (
            patch("kodo.cli._intake.make_session", return_value=session),
            patch("builtins.input", side_effect=lambda *a: next(inputs)),
        ):
            result = run_intake_chat("claude", run_dir, "Build a web app", staged=False)

        assert result == "Refined goal text"
        assert session.stats.queries == 3  # initial + answer + finalize

    def test_staged_returns_goal_plan(self, project):
        """Staged intake should parse JSON into GoalPlan."""
        run_dir = RunDir.create(project, "test")
        plan_json = json.dumps(
            {
                "context": "Rust game project",
                "stages": [
                    {
                        "index": 1,
                        "name": "Setup",
                        "description": "Initial setup",
                        "acceptance_criteria": "Project compiles",
                        "browser_testing": False,
                    }
                ],
            }
        )

        session = make_scripted_session(
            responses=["Questions?", "Let me write the plan."],
            project_dir=project,
            write_file={
                "on_query": 1,
                "path": str(run_dir.goal_plan_file),
                "content": plan_json,
            },
        )

        inputs = iter(["just do it", "/done"])

        with (
            patch("kodo.cli._intake.make_session", return_value=session),
            patch("builtins.input", side_effect=lambda *a: next(inputs)),
        ):
            result = run_intake_chat("claude", run_dir, "Build a game", staged=True)

        assert result is not None
        assert len(result.stages) == 1
        assert result.stages[0].name == "Setup"

    def test_returns_none_when_finalize_fails(self, project):
        """If even finalize doesn't produce a file, return None."""
        run_dir = RunDir.create(project, "test")
        session = make_scripted_session(
            responses=["What's the goal about?", "I see, thanks."],
            project_dir=project,
        )

        inputs = iter(["something", "/done"])

        with (
            patch("kodo.cli._intake.make_session", return_value=session),
            patch("builtins.input", side_effect=lambda *a: next(inputs)),
        ):
            result = run_intake_chat("claude", run_dir, "Vague goal", staged=False)

        assert result is None


class TestIntakeEdgeCases:
    """Edge cases: ctrl-C, EOF, etc."""

    def test_keyboard_interrupt_triggers_finalize(self, project):
        """Ctrl-C should exit loop gracefully and attempt finalize."""
        run_dir = RunDir.create(project, "test")
        session = make_scripted_session(
            responses=["Tell me more?", "Finalizing..."],
            project_dir=project,
        )

        with (
            patch("kodo.cli._intake.make_session", return_value=session),
            patch("builtins.input", side_effect=KeyboardInterrupt),
        ):
            run_intake_chat("claude", run_dir, "My goal", staged=False)

        # initial + finalize (no user answers since input raised immediately)
        assert session.stats.queries == 2

    def test_eof_triggers_finalize(self, project):
        """EOF should exit loop gracefully and attempt finalize."""
        run_dir = RunDir.create(project, "test")
        session = make_scripted_session(
            responses=["Tell me more?", "Finalizing..."],
            project_dir=project,
        )

        with (
            patch("kodo.cli._intake.make_session", return_value=session),
            patch("builtins.input", side_effect=EOFError),
        ):
            run_intake_chat("claude", run_dir, "My goal", staged=False)

        assert session.stats.queries == 2


class TestAutoRefine:
    """Tests for run_intake_auto — automated goal refinement."""

    def test_returns_file_content_when_session_writes_file(self, project):
        """If the session writes goal-refined.md, return its content."""
        run_dir = RunDir.create(project, "test_auto")
        session = make_scripted_session(
            responses=["Analysis: looks good"],
            project_dir=project,
            write_file={
                "on_query": 0,
                "path": str(run_dir.goal_refined_file),
                "content": "Refined: build a REST API with auth",
            },
        )

        with patch("kodo.cli._intake.make_session", return_value=session):
            result = run_intake_auto("claude", run_dir, "Build an API")

        assert result == "Refined: build a REST API with auth"
        assert session.stats.queries == 1

    def test_falls_back_to_response_text_when_no_file(self, project):
        """If session doesn't write a file, wrap its response as refinement."""
        run_dir = RunDir.create(project, "test_auto_fallback")
        session = FakeSession(
            response_text="Implicit: needs pagination and rate limiting"
        )

        with patch("kodo.cli._intake.make_session", return_value=session):
            result = run_intake_auto("claude", run_dir, "Build an API")

        assert result is not None
        assert "Build an API" in result
        assert "Implicit: needs pagination and rate limiting" in result
        # Fallback should also persist to disk
        assert run_dir.goal_refined_file.exists()

    def test_returns_none_on_empty_response(self, project):
        """If session returns empty text and no file, return None."""
        run_dir = RunDir.create(project, "test_auto_empty")
        session = FakeSession(response_text="")

        with patch("kodo.cli._intake.make_session", return_value=session):
            result = run_intake_auto("claude", run_dir, "Build an API")

        assert result is None


class TestIntakeChatSessionError:
    """session.query exceptions in the conversation loop should be caught."""

    def test_session_error_logged_and_loop_continues(self, project, capsys):
        """If session.query raises during conversation, log error and continue."""
        run_dir = RunDir.create(project, "test_err")

        call_count = 0

        class ErrorThenOkSession(FakeSession):
            def query(self, prompt, project_dir_arg, *, max_turns=10):
                nonlocal call_count
                call_count += 1
                # First call (initial query) succeeds
                if call_count == 1:
                    return super().query(prompt, project_dir_arg, max_turns=max_turns)
                # Second call (first user answer) raises
                if call_count == 2:
                    self._stats.queries += 1
                    raise ConnectionError("network down")
                # Third call (second user answer) succeeds
                return super().query(prompt, project_dir_arg, max_turns=max_turns)

        session = ErrorThenOkSession(response_text="Got it.")

        # User gives two answers then types /done
        inputs = iter(["answer1", "answer2", "/done"])

        with (
            patch("kodo.cli._intake.make_session", return_value=session),
            patch("builtins.input", side_effect=lambda *a: next(inputs)),
        ):
            run_intake_chat("claude", run_dir, "Build a thing", staged=False)

        captured = capsys.readouterr()
        assert "Session error" in captured.out
        assert "network down" in captured.out
        # Loop should have continued: initial + error-attempt + answer2 + finalize
        assert call_count == 4

    def test_session_error_does_not_crash_loop(self, project):
        """Even if every in-loop query fails, the loop exits cleanly."""
        run_dir = RunDir.create(project, "test_err_all")

        call_count = 0

        class LoopErrorSession(FakeSession):
            def query(self, prompt, project_dir_arg, *, max_turns=10):
                nonlocal call_count
                call_count += 1
                # First call (initial query) succeeds
                if call_count == 1:
                    return super().query(prompt, project_dir_arg, max_turns=max_turns)
                # In-loop calls (2, 3) raise; finalize (4) succeeds
                if call_count <= 3:
                    self._stats.queries += 1
                    raise RuntimeError("boom")
                return super().query(prompt, project_dir_arg, max_turns=max_turns)

        session = LoopErrorSession(response_text="Questions?")
        inputs = iter(["a", "b", "/done"])

        with (
            patch("kodo.cli._intake.make_session", return_value=session),
            patch("builtins.input", side_effect=lambda *a: next(inputs)),
        ):
            # Should not raise — loop catches errors and continues
            result = run_intake_chat("claude", run_dir, "My goal", staged=False)

        # No output file → returns None
        assert result is None
        # initial + 2 errors (caught) + finalize = 4 calls
        assert call_count == 4
