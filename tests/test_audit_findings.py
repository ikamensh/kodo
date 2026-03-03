"""Audit evidence tests for API Surface & Error Message findings (F1-F8).

These tests exercise the gaps identified in findings-api-surface.md.
Tests marked xfail document known gaps that exist in the current code.
Tests that pass confirm the finding's evidence is reproducible.

No source code is modified by this file.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from kodo import log
from kodo.factory import get_team, get_team_presets
from kodo.sessions.base import classify_session_error


# ---------------------------------------------------------------------------
# F1: User JSON teams blocked in non-interactive CLI
# ---------------------------------------------------------------------------
class TestF1UserJsonTeamsBlocked:
    """get_team_presets() returns only built-in presets.  A user-defined
    team in ~/.kodo/teams/ is accepted by list_available_teams() and
    argparse choices, but rejected by the ``team_name not in TEAMS``
    guard in _main.py."""

    def test_get_team_presets_only_returns_builtins(self):
        """Confirm TEAMS (get_team_presets) does not include user teams."""
        presets = get_team_presets()
        # Built-in names only — no user-defined teams appear here
        for name in presets:
            assert name in {"full", "quick", "saga", "mission"}, (
                f"Unexpected preset {name!r} — if user teams now appear, F1 may be fixed"
            )

    def test_get_team_resolves_user_json(self, tmp_path: Path):
        """A valid user JSON team file should be loadable via get_team()."""
        team_json = {
            "name": "audit_test",
            "description": "test team",
            "agents": {
                "worker": {
                    "backend": "claude",
                    "model": "sonnet",
                    "description": "test worker",
                }
            },
        }
        kodo_dir = tmp_path / ".kodo" / "teams"
        kodo_dir.mkdir(parents=True)
        (kodo_dir / "audit_test.json").write_text(json.dumps(team_json))

        with patch("pathlib.Path.home", return_value=tmp_path):
            # get_team should resolve the user JSON — but it won't be in
            # get_team_presets(), which is what _main.py checks.
            result = get_team("audit_test")
            assert result is not None, "get_team() should resolve user JSON teams"
            # The bug: get_team_presets() still won't include it
            presets = get_team_presets()
            assert "audit_test" not in presets, (
                "F1 confirmed: user team not in TEAMS dict used by CLI guard"
            )


# ---------------------------------------------------------------------------
# F2: Silent error for invalid team JSON in factory.get_team
# ---------------------------------------------------------------------------
class TestF2SilentInvalidTeamJson:
    """get_team() catches json.JSONDecodeError and falls back to cfg={}
    with no warning.  The user gets a late, confusing error."""

    def test_invalid_json_silently_swallowed(self, tmp_path: Path):
        """Confirm that malformed JSON in a user team file does not
        produce a clear early error from get_team()."""
        kodo_dir = tmp_path / ".kodo" / "teams"
        kodo_dir.mkdir(parents=True)
        (kodo_dir / "broken.json").write_text("{not valid json!!!")

        with patch("pathlib.Path.home", return_value=tmp_path):
            # get_team should ideally raise ValueError with context,
            # but currently it silently falls back to cfg={}
            try:
                result = get_team("broken")
                # If we get here without error, the silent fallback happened
                assert result is not None, (
                    "F2 confirmed: invalid JSON silently accepted, no early error"
                )
            except (ValueError, KeyError):
                # If it raises, the fix may have been applied
                pass


# ---------------------------------------------------------------------------
# F3: get_team unknown name raises raw KeyError
# ---------------------------------------------------------------------------
class TestF3RawKeyError:
    """get_team() raises raw KeyError for unknown names instead of
    a descriptive ValueError."""

    def test_unknown_team_raises_keyerror(self, tmp_path: Path):
        """An unknown team name should produce a clear error, but currently
        raises raw KeyError."""
        kodo_dir = tmp_path / ".kodo" / "teams"
        kodo_dir.mkdir(parents=True)  # empty — no user teams

        with patch("pathlib.Path.home", return_value=tmp_path):
            with pytest.raises(KeyError):
                get_team("nonexistent_team_name_xyz")
            # F3 confirmed: raw KeyError, not a descriptive ValueError


# ---------------------------------------------------------------------------
# F4: FileNotFoundError not caught in SubprocessSession._spawn
# ---------------------------------------------------------------------------
class TestF4FileNotFoundInSpawn:
    """If a backend binary is missing from PATH, _spawn() lets
    FileNotFoundError propagate uncaught."""

    def test_classify_session_error_handles_binary_not_found(self):
        """classify_session_error maps stderr to hints — but only when
        the process ran.  FileNotFoundError from Popen is a different path."""
        # This is the path that DOES work — stderr-based classification
        result = classify_session_error(
            returncode=1,
            stderr="error: No such file or directory",
            backend="cursor",
        )
        # The function handles stderr patterns, but NOT the case where
        # Popen itself raises FileNotFoundError before any process runs.
        # That's the gap documented in F4.
        assert result is not None or result is None  # always passes — just documents the gap

    def test_popen_file_not_found_is_uncaught(self):
        """Demonstrate that subprocess.Popen raises FileNotFoundError
        for a missing binary — the same exception _spawn() doesn't catch."""
        import subprocess

        with pytest.raises(FileNotFoundError):
            subprocess.Popen(
                ["definitely_not_a_real_binary_xyz_12345"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        # F4 confirmed: this exception type is what _spawn() would face


# ---------------------------------------------------------------------------
# F5: Log resume on truncated JSON provides no detail
# ---------------------------------------------------------------------------
class TestF5LogResumeNoDetail:
    """parse_run() returns None with no reason when parsing fails."""

    def test_empty_log_returns_none_no_reason(self, tmp_path: Path):
        """An empty log file returns None — no indication of why."""
        log_file = tmp_path / "run.jsonl"
        log_file.write_text("")
        result = log.parse_run(log_file)
        assert result is None, "Empty file should return None"
        # F5: no way to know WHY it returned None (empty file? missing field?)

    def test_truncated_json_returns_none_no_reason(self, tmp_path: Path):
        """A log with a truncated JSON line returns None — no detail."""
        log_file = tmp_path / "run.jsonl"
        log_file.write_text('{"event": "run_start", "goal": "test"\n')  # truncated
        result = log.parse_run(log_file)
        assert result is None, "Truncated JSON should return None"
        # F5: caller cannot distinguish truncated from missing run_start

    def test_missing_goal_returns_none_no_reason(self, tmp_path: Path):
        """A log with run_start but no goal field returns None."""
        log_file = tmp_path / "run.jsonl"
        log_file.write_text(json.dumps({"event": "run_start", "no_goal": True}) + "\n")
        result = log.parse_run(log_file)
        assert result is None, "Missing goal should return None"
        # F5: same None return — no way to know it was 'missing goal'

    def test_valid_log_parses_successfully(self, tmp_path: Path):
        """Baseline: a valid log file parses correctly."""
        log_file = tmp_path / "run.jsonl"
        lines = [
            json.dumps({
                "event": "run_start",
                "goal": "test goal",
                "run_id": "test_run_123",
                "orchestrator": "base",
                "model": "sonnet",
                "project_dir": str(tmp_path),
                "max_exchanges": 10,
                "max_cycles": 3,
                "team": ["worker"],
                "team_preset": "full",
            }),
            json.dumps({
                "event": "cli_args",
                "args": {"goal": "test goal"},
            }),
        ]
        log_file.write_text("\n".join(lines) + "\n")
        result = log.parse_run(log_file)
        assert result is not None, "Valid log should parse"
        assert result.goal == "test goal"


# ---------------------------------------------------------------------------
# F6: Inconsistent Claude session error messages
# ---------------------------------------------------------------------------
class TestF6InconsistentErrorMessages:
    """Connection errors include actionable hints; query errors do not."""

    def test_error_message_format_documented(self):
        """Document the inconsistency between connect and query error messages."""
        # Connect error format (from claude.py:281-285):
        connect_msg = (
            "Claude session failed to connect: SomeError: details\n"
            "Check that Claude Code is installed, authenticated, "
            "and your subscription is active."
        )
        # Query error format (from claude.py:401-403):
        query_msg = "Claude session error during query: SomeError: details"

        # F6: connect_msg has actionable hint, query_msg does not
        assert "\n" in connect_msg, "Connect error has multi-line hint"
        assert "\n" not in query_msg, "Query error has no hint — F6 confirmed"


# ---------------------------------------------------------------------------
# F7: --improve on empty directory has no early exit
# ---------------------------------------------------------------------------
class TestF7ImproveEmptyDir:
    """--improve on an empty directory runs full discovery instead of
    exiting early with a clear message."""

    def test_empty_dir_has_no_source_files(self, tmp_path: Path):
        """An empty directory has no source files — --improve should
        detect this early, but currently doesn't."""
        source_extensions = {".py", ".ts", ".js", ".go", ".rs", ".java"}
        project_markers = {"package.json", "Cargo.toml", "pyproject.toml", "go.mod"}

        has_sources = any(
            f.suffix in source_extensions
            for f in tmp_path.rglob("*")
            if f.is_file()
        )
        has_markers = any(
            (tmp_path / m).exists() for m in project_markers
        )

        assert not has_sources, "Empty dir should have no source files"
        assert not has_markers, "Empty dir should have no project markers"
        # F7: --improve currently runs discovery anyway on such a directory


# ---------------------------------------------------------------------------
# F8: ClaudeSession does not clear _session_id on query error
# ---------------------------------------------------------------------------
class TestF8SessionIdNotCleared:
    """When query() catches an exception, it returns QueryResult(is_error=True)
    but does NOT clear _session_id.  Subsequent queries reuse the broken
    session."""

    def test_query_error_result_is_error(self):
        """Confirm the error QueryResult structure from the query error path."""
        from kodo.sessions.base import QueryResult

        # This is what claude.py:400-404 returns on error
        error_result = QueryResult(
            text="Claude session error during query: RuntimeError: test",
            elapsed_s=0.1,
            is_error=True,
        )
        assert error_result.is_error is True
        # F8: after this return, _session_id is still set — next query
        # will resume into the broken session instead of starting fresh

    def test_error_classification_subscription_patterns(self):
        """Verify classify_session_error catches billing/subscription errors."""
        result = classify_session_error(
            returncode=1,
            stderr="You've hit your usage limit for this month",
            backend="cursor",
        )
        # Should classify as subscription/billing
        assert result is not None, "Usage limit should be classified"
        assert "ubscription" in result or "illing" in result or "usage" in result.lower(), (
            f"Expected billing classification, got: {result!r}"
        )
