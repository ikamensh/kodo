"""Smoke tests for CLI entry point: --help, --version, flag conflicts, edge cases."""

from __future__ import annotations

import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from kodo.cli._main import _main_inner, main

# ---------------------------------------------------------------------------
# --help and --version (in-process)
# ---------------------------------------------------------------------------


class TestHelpAndVersion:
    def test_help_exits_zero(self, capsys):
        with (
            patch("sys.argv", ["kodo", "--help"]),
            pytest.raises(SystemExit) as exc_info,
        ):
            _main_inner()
        assert exc_info.value.code == 0

    def test_help_contains_key_sections(self, capsys):
        with (
            patch("sys.argv", ["kodo", "--help"]),
            pytest.raises(SystemExit),
        ):
            _main_inner()
        out = capsys.readouterr().out
        assert "--goal" in out
        assert "--resume" in out
        assert "--improve" in out
        assert "kodo runs" in out

    def test_version_prints_version(self, capsys):
        from kodo import __version__

        with (
            patch("sys.argv", ["kodo", "--version"]),
            pytest.raises(SystemExit) as exc_info,
        ):
            _main_inner()
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert __version__ in out


# ---------------------------------------------------------------------------
# --help and --version (subprocess — validates entry point + packaging)
# ---------------------------------------------------------------------------


class TestEntryPointSubprocess:
    def test_help_subprocess(self):
        result = subprocess.run(
            [sys.executable, "-m", "kodo.cli._main", "--help"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0
        assert "--goal" in result.stdout

    def test_version_subprocess(self):
        from kodo import __version__

        result = subprocess.run(
            [sys.executable, "-m", "kodo.cli._main", "--version"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0
        assert __version__ in result.stdout


# ---------------------------------------------------------------------------
# Flag conflicts and validation
# ---------------------------------------------------------------------------


class TestFlagConflicts:
    @pytest.fixture(autouse=True)
    def _fake_backends(self):
        with (
            patch("kodo.cli._params.preferred_orchestrator", return_value="claude-code"),
            patch("kodo.cli._params.check_api_key", return_value=None),
        ):
            yield

    def test_goal_and_improve_mutually_exclusive(self):
        """argparse enforces --goal and --improve are mutually exclusive."""
        with (
            patch("sys.argv", ["kodo", "--goal", "X", "--improve"]),
            pytest.raises(SystemExit) as exc_info,
        ):
            _main_inner()
        assert exc_info.value.code == 2  # argparse exits with 2

    def test_goal_and_goal_file_mutually_exclusive(self):
        with (
            patch("sys.argv", ["kodo", "--goal", "X", "--goal-file", "f.md"]),
            pytest.raises(SystemExit) as exc_info,
        ):
            _main_inner()
        assert exc_info.value.code == 2

    def test_resume_with_goal_fails(self, tmp_path):
        with (
            patch("sys.argv", ["kodo", "--resume", "--goal", "X", "--project", str(tmp_path)]),
            pytest.raises(SystemExit),
        ):
            _main_inner()

    def test_empty_goal_fails(self, tmp_path):
        with (
            patch("sys.argv", ["kodo", "--goal", "   ", "--project", str(tmp_path)]),
            pytest.raises(SystemExit),
        ):
            _main_inner()

    def test_negative_exchanges_fails(self, tmp_path):
        with (
            patch(
                "sys.argv", ["kodo", "--goal", "X", "--exchanges", "-1", "--project", str(tmp_path)]
            ),
            pytest.raises(SystemExit),
        ):
            _main_inner()

    def test_zero_cycles_fails(self, tmp_path):
        with (
            patch("sys.argv", ["kodo", "--goal", "X", "--cycles", "0", "--project", str(tmp_path)]),
            pytest.raises(SystemExit),
        ):
            _main_inner()

    def test_invalid_team_fails(self, tmp_path):
        with (
            patch(
                "sys.argv",
                ["kodo", "--goal", "X", "--team", "nonexistent", "--project", str(tmp_path)],
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            _main_inner()
        assert exc_info.value.code == 2  # argparse rejects invalid choice

    def test_goal_file_not_found_fails(self, tmp_path):
        with (
            patch(
                "sys.argv",
                ["kodo", "--goal-file", str(tmp_path / "nope.md"), "--project", str(tmp_path)],
            ),
            pytest.raises(SystemExit),
        ):
            _main_inner()

    def test_empty_goal_file_fails(self, tmp_path):
        goal_file = tmp_path / "empty.md"
        goal_file.write_text("")
        with (
            patch("sys.argv", ["kodo", "--goal-file", str(goal_file), "--project", str(tmp_path)]),
            pytest.raises(SystemExit),
        ):
            _main_inner()


# ---------------------------------------------------------------------------
# --no-auto-commit flag
# ---------------------------------------------------------------------------


class TestNoAutoCommit:
    @pytest.fixture(autouse=True)
    def _fake_backends(self):
        with (
            patch("kodo.cli._params.preferred_orchestrator", return_value="claude-code"),
            patch("kodo.cli._params.check_api_key", return_value=None),
        ):
            yield

    def test_no_auto_commit_passed_to_launch(self, tmp_path):
        """--no-auto-commit sets auto_commit=False in params."""
        captured_params = {}

        def fake_launch(run_dir, goal_text, params, **kwargs):
            captured_params.update(params)
            # Return a minimal RunResult
            return MagicMock(
                finished=True,
                cycles=[],
                total_exchanges=0,
                total_cost_usd=0,
                summary="done",
                stage_results=[],
            )

        with (
            patch(
                "sys.argv",
                [
                    "kodo",
                    "--goal",
                    "Build X",
                    "--yes",
                    "--skip-intake",
                    "--no-auto-commit",
                    "--project",
                    str(tmp_path),
                ],
            ),
            patch("kodo.cli._main.launch_run", side_effect=fake_launch),
        ):
            _main_inner()

        assert captured_params.get("auto_commit") is False


# ---------------------------------------------------------------------------
# --resume from CLI entry
# ---------------------------------------------------------------------------


class TestResumeCLI:
    def test_resume_no_incomplete_runs(self, tmp_path):
        with (
            patch("sys.argv", ["kodo", "--resume", "--project", str(tmp_path)]),
            patch("kodo.cli._main.log.find_incomplete_runs", return_value=[]),
            pytest.raises(SystemExit),
        ):
            _main_inner()

    def test_resume_specific_run_not_found(self, tmp_path):
        with (
            patch("sys.argv", ["kodo", "--resume", "bogus_run_id", "--project", str(tmp_path)]),
            pytest.raises(SystemExit),
        ):
            _main_inner()

    def test_resume_latest_calls_launch_resume(self, tmp_path):
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

        fake_result = MagicMock(
            finished=True,
            cycles=[],
            total_exchanges=5,
            total_cost_usd=0.01,
            summary="done",
            stage_results=[],
        )

        # --resume (no value) uses __latest__ path; project_dir is positional
        with (
            patch("sys.argv", ["kodo", "--resume", "--yes", "--project", str(tmp_path)]),
            patch("kodo.cli._main.log.find_incomplete_runs", return_value=[fake_state]),
            patch(
                "kodo.cli._main.launch_resume", return_value=fake_result
            ) as mock_resume,
        ):
            _main_inner()

        mock_resume.assert_called_once()


# ---------------------------------------------------------------------------
# KeyboardInterrupt and exception handling in main()
# ---------------------------------------------------------------------------


class TestMainWrapper:
    def test_keyboard_interrupt_exits_130(self):
        with (
            patch("kodo.cli._main._main_inner", side_effect=KeyboardInterrupt),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()
        assert exc_info.value.code == 130

    def test_json_mode_wraps_exception(self, capsys):
        with (
            patch("sys.argv", ["kodo", "--json"]),
            patch("kodo.cli._main._main_inner", side_effect=RuntimeError("boom")),
            pytest.raises(SystemExit),
        ):
            main()
