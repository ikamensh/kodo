"""Regression tests for bugs found during audit (2026-02-26).

Each test documents the original issue and prevents recurrence.
"""

from __future__ import annotations

import json
import stat
from unittest.mock import patch

import pytest

from kodo import log
from kodo.cli._main import _main_inner
from kodo.orchestrators.verification import _check_passed


# Issue #1 (--skip-intake / --auto-refine without --goal) is now fixed and
# covered by tests/cli/test_cli_main.py::TestFlagValidation. Skipped tests removed.


# ---------------------------------------------------------------------------
# Issue #4: parse_run crashes on PermissionError
# ---------------------------------------------------------------------------


class TestParseRunPermissionError:
    """parse_run and list_runs should not crash on unreadable log files."""

    def test_parse_run_returns_none_on_permission_error(self, tmp_path):
        """parse_run should return None (not crash) on PermissionError."""
        d = log._runs_root() / "perm_test"
        d.mkdir(parents=True)
        log_file = d / "log.jsonl"
        log_file.write_text('{"event":"run_start"}\n')

        log_file.chmod(0o000)
        try:
            result = log.parse_run(log_file)
            assert result is None
        finally:
            log_file.chmod(stat.S_IRUSR | stat.S_IWUSR)

    def test_list_runs_skips_unreadable_log(self, tmp_path):
        """An unreadable log.jsonl should not prevent listing other runs."""
        proj = str(tmp_path)

        # Create a good run
        good = log._runs_root() / "good_run"
        good.mkdir(parents=True)
        events = [
            {
                "ts": "t",
                "t": 0,
                "event": "run_start",
                "orchestrator": "api",
                "model": "m",
                "max_exchanges": 30,
                "max_cycles": 5,
                "team": [],
                "goal": "good goal",
                "project_dir": proj,
            },
            {"ts": "t", "t": 0, "event": "cli_args", "team": "full"},
        ]
        (good / "log.jsonl").write_text(
            "\n".join(json.dumps(e) for e in events) + "\n"
        )

        # Create a run with unreadable log
        bad = log._runs_root() / "bad_perm_run"
        bad.mkdir(parents=True)
        bad_log = bad / "log.jsonl"
        bad_log.write_text('{"event":"run_start"}\n')
        bad_log.chmod(0o000)

        try:
            runs = log.list_runs()
            # Should still return the good run
            assert any(r.run_id == "good_run" for r in runs)
        except PermissionError:
            pytest.fail(
                "list_runs crashed with PermissionError on unreadable log file"
            )
        finally:
            bad_log.chmod(stat.S_IRUSR | stat.S_IWUSR)


# ---------------------------------------------------------------------------
# Issue #5: _check_passed fooled by LLM quoting
# ---------------------------------------------------------------------------


class TestCheckPassedQuoting:
    """_check_passed should not be tricked by quoted or attributed mentions."""

    def test_direct_pass(self):
        assert _check_passed("ALL CHECKS PASS") is True

    def test_direct_fail(self):
        assert _check_passed("Tests are failing, 3 errors found") is False

    def test_not_all_checks_pass_rejected(self):
        assert _check_passed("NOT ALL CHECKS PASS — 2 failures") is False

    def test_not_minor_issues_fixed_rejected(self):
        assert _check_passed("NOT MINOR ISSUES FIXED — review needed") is False

    def test_quoted_pass_rejected(self):
        """A report quoting 'ALL CHECKS PASS' but failing should be rejected."""
        report = (
            "The agent said 'ALL CHECKS PASS' but I found 3 failing tests "
            "in test_auth.py. This is incorrect."
        )
        assert _check_passed(report) is False

    def test_attributed_pass_rejected(self):
        """Mentioning the phrase in a negative context should be rejected."""
        report = (
            "I cannot say ALL CHECKS PASS because there are unresolved "
            "issues in the authentication module."
        )
        assert _check_passed(report) is False

    def test_minor_issues_fixed_accepted(self):
        assert _check_passed("MINOR ISSUES FIXED") is True

    def test_code_block_pass_rejected(self):
        """Signal phrase inside a fenced code block should be rejected."""
        report = (
            "The test output shows:\n"
            "```\n"
            "ALL CHECKS PASS\n"
            "```\n"
            "But actually 2 tests are still failing."
        )
        assert _check_passed(report) is False

    def test_inline_code_pass_rejected(self):
        """Signal phrase inside inline code should be rejected."""
        report = "The output contained `ALL CHECKS PASS` but tests fail."
        assert _check_passed(report) is False

    def test_double_quoted_pass_rejected(self):
        """Signal phrase inside double quotes should be rejected."""
        report = 'The agent output "ALL CHECKS PASS" but 3 tests are broken.'
        assert _check_passed(report) is False

    def test_pass_after_period_accepted(self):
        """Signal phrase at start of a new sentence should be accepted."""
        report = "I have reviewed all test results. ALL CHECKS PASS"
        assert _check_passed(report) is True

    def test_pass_on_own_line_accepted(self):
        """Signal phrase on its own line should be accepted."""
        report = "Review complete.\nALL CHECKS PASS\n"
        assert _check_passed(report) is True


# ---------------------------------------------------------------------------
# Issue #9: Saved config with invalid team name crashes with KeyError
# ---------------------------------------------------------------------------


class TestInvalidSavedTeamConfig:
    """Loading a saved config with a bad team name should not crash."""

    def test_invalid_team_in_config_does_not_crash(self, tmp_path, capsys):
        """Config with unknown team should fall through to interactive selection,
        not raise KeyError."""
        from kodo.cli._params import _load_or_select_params

        cfg_dir = tmp_path / ".kodo"
        cfg_dir.mkdir()
        config = {
            "team": "nonexistent_team_v99",
            "orchestrator": "api",
            "orchestrator_model": "gemini-flash",
            "max_exchanges": 20,
            "max_cycles": 3,
        }
        (cfg_dir / "config.json").write_text(json.dumps(config))

        # Should print a warning and fall through to select_params (which
        # we short-circuit by raising SystemExit via the mocked select_params)
        with (
            patch(
                "kodo.cli._params.select_params", autospec=True,
                side_effect=SystemExit(99),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            _load_or_select_params(tmp_path)

        # Verify it fell through to select_params (exit code 99 from our mock)
        assert exc_info.value.code == 99
        captured = capsys.readouterr().out
        assert "unknown team" in captured


# ---------------------------------------------------------------------------
# Issue #22: Resume of completed run allowed via explicit ID
# ---------------------------------------------------------------------------


class TestResumeCompletedRun:
    """--resume <id> on a finished run should warn or refuse."""

    @pytest.mark.skip(reason="Issue #22 not yet fixed")
    def test_resume_finished_run_refused(self, tmp_path):
        """Resuming a completed run should error, not re-execute."""
        d = log._runs_root() / "finished_run"
        d.mkdir(parents=True)
        events = [
            {
                "ts": "t",
                "t": 0,
                "event": "run_start",
                "orchestrator": "api",
                "model": "m",
                "max_exchanges": 30,
                "max_cycles": 5,
                "team": [],
                "goal": "done goal",
                "project_dir": str(tmp_path),
            },
            {"ts": "t", "t": 0, "event": "cli_args", "team": "full"},
            {"ts": "t", "t": 0, "event": "cycle_end", "summary": "done"},
            {"ts": "t", "t": 0, "event": "run_end"},
        ]
        (d / "log.jsonl").write_text(
            "\n".join(json.dumps(e) for e in events) + "\n"
        )

        with (
            patch("sys.argv", ["kodo", "--resume", "finished_run", "--project", str(tmp_path)]),
            pytest.raises(SystemExit),
        ):
            _main_inner()
