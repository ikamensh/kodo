"""Tests for CLI argument validation edge cases in kodo/cli/_main.py.

Covers:
- JSON mode active for early validation errors
- --project path must exist and be a directory
- --resume must not be an empty string
- --exchanges upper bound (max 1000)
- --cycles upper bound (max 100)
- --focus must not be empty or whitespace-only
- Orchestrator/model compatibility checks
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from kodo.cli._main import _main_inner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_and_capture_exit(argv: list[str]) -> int:
    """Run _main_inner and return the exit code."""
    with (
        patch("sys.argv", argv),
        pytest.raises(SystemExit) as exc_info,
    ):
        _main_inner()
    return exc_info.value.code


class _ValidationPassed(Exception):
    """Sentinel raised when early validation passes and we want to stop."""


def _collect_fail_calls(argv: list[str]) -> list[tuple[str, bool]]:
    """Run _main_inner with _fail mocked to raise SystemExit.

    Returns list of (message, json_mode_active) tuples. json_mode_active is
    True when _original_stdout was set (meaning json_output_redirect had been
    entered before _fail was called).

    To prevent the function from proceeding into heavy code (LLM calls etc.)
    after passing validation, _build_params_from_flags is also mocked.
    """
    import kodo.cli._launch as _launch_mod

    results: list[tuple[str, bool]] = []

    def fake_fail(msg, code=1):
        results.append((msg, _launch_mod._original_stdout is not None))
        raise SystemExit(code)

    # Reset global to avoid cross-test pollution
    _launch_mod._original_stdout = None

    with (
        patch("sys.argv", argv),
        patch("kodo.cli._main._fail", side_effect=fake_fail),
        patch("kodo.cli._main._print_banner"),
        # Block execution after validation to prevent LLM calls
        patch(
            "kodo.cli._main._build_params_from_flags",
            side_effect=_ValidationPassed("stopped after validation"),
        ),
        patch(
            "kodo.cli._main._load_or_select_params",
            side_effect=_ValidationPassed("stopped after validation"),
        ),
        # Also block resume path
        patch(
            "kodo.cli._main.log.find_incomplete_runs",
            side_effect=_ValidationPassed("stopped after validation"),
        ),
    ):
        try:
            _main_inner()
        except (SystemExit, _ValidationPassed, Exception):
            pass
        finally:
            # Always clean up to prevent cross-test pollution
            _launch_mod._original_stdout = None

    return results


def _get_fail_messages(argv: list[str]) -> list[str]:
    """Return just the message strings from _collect_fail_calls."""
    return [msg for msg, _ in _collect_fail_calls(argv)]


# ---------------------------------------------------------------------------
# 1. --json respected for early validation errors
# ---------------------------------------------------------------------------


class TestJsonModeEarlyValidation:
    """JSON mode must be active before early validation runs so _fail()
    emits JSON to stdout instead of plain text to stderr.

    We verify by checking that _original_stdout is non-None when _fail
    is called, meaning json_output_redirect() was entered first.
    """

    def test_json_empty_goal_activates_json_before_fail(self, tmp_path):
        """--json --goal '' should have JSON mode active when _fail runs."""
        results = _collect_fail_calls([
            "kodo", "--json", "--goal", "", "--project", str(tmp_path),
        ])
        assert results, "Expected _fail to be called"
        msg, was_json = results[0]
        assert was_json, "_original_stdout should be set (JSON mode active) before _fail"
        assert "--goal" in msg

    def test_json_exchanges_over_limit_activates_json(self, tmp_path):
        """--json --exchanges 9999 should have JSON mode active when _fail runs."""
        results = _collect_fail_calls([
            "kodo", "--json", "--goal", "test", "--exchanges", "9999",
            "--project", str(tmp_path),
        ])
        assert results, "Expected _fail to be called"
        msg, was_json = results[0]
        assert was_json, "_original_stdout should be set (JSON mode active) before _fail"
        assert "1000" in msg

    def test_json_cycles_over_limit_activates_json(self, tmp_path):
        """--json --cycles 999 should have JSON mode active when _fail runs."""
        results = _collect_fail_calls([
            "kodo", "--json", "--goal", "test", "--cycles", "999",
            "--project", str(tmp_path),
        ])
        assert results, "Expected _fail to be called"
        msg, was_json = results[0]
        assert was_json, "_original_stdout should be set (JSON mode active) before _fail"
        assert "100" in msg

    def test_json_invalid_project_activates_json(self, tmp_path):
        """--json with nonexistent --project should have JSON mode active."""
        bad_path = str(tmp_path / "does_not_exist")
        results = _collect_fail_calls([
            "kodo", "--json", "--goal", "test",
            "--project", bad_path,
        ])
        assert results, "Expected _fail to be called"
        msg, was_json = results[0]
        assert was_json, "_original_stdout should be set (JSON mode active) before _fail"
        assert "does not exist" in msg

    def test_non_json_mode_does_not_set_original_stdout(self, tmp_path):
        """Without --json, _original_stdout should NOT be set when _fail runs."""
        results = _collect_fail_calls([
            "kodo", "--goal", "", "--project", str(tmp_path),
        ])
        assert results, "Expected _fail to be called"
        _, was_json = results[0]
        assert not was_json, "_original_stdout should be None without --json"


# ---------------------------------------------------------------------------
# 2. --project validation
# ---------------------------------------------------------------------------


class TestProjectValidation:
    """--project must be an existing directory."""

    def test_project_nonexistent_path_fails(self, tmp_path):
        bad_path = str(tmp_path / "nope")
        code = _run_and_capture_exit([
            "kodo", "--goal", "test", "--project", bad_path,
        ])
        assert code != 0

    def test_project_is_file_fails(self, tmp_path):
        file_path = tmp_path / "afile.txt"
        file_path.write_text("hello")
        code = _run_and_capture_exit([
            "kodo", "--goal", "test", "--project", str(file_path),
        ])
        assert code != 0

    def test_project_valid_directory_passes_validation(self, tmp_path):
        """A valid directory should not trigger project-path validation failure."""
        msgs = _get_fail_messages([
            "kodo", "--goal", "test", "--yes", "--project", str(tmp_path),
        ])
        assert not any("--project path" in m for m in msgs)


# ---------------------------------------------------------------------------
# 3. --resume empty string
# ---------------------------------------------------------------------------


class TestResumeEmptyString:
    """--resume must not be an empty string."""

    def test_resume_empty_string_fails(self, tmp_path):
        code = _run_and_capture_exit([
            "kodo", "--resume", "", "--project", str(tmp_path),
        ])
        assert code != 0

    def test_resume_no_value_is_valid(self, tmp_path):
        """--resume with no value should set __latest__ (not empty string),
        and should not fail at the empty-string validation step."""
        msgs = _get_fail_messages([
            "kodo", "--resume", "--project", str(tmp_path),
        ])
        # Fails downstream (no incomplete runs), not because of empty string
        assert not any("empty string" in m for m in msgs)


# ---------------------------------------------------------------------------
# 4. --exchanges and --cycles upper bounds
# ---------------------------------------------------------------------------


class TestExchangesUpperBound:
    """--exchanges must not exceed 1000."""

    def test_exchanges_1001_fails(self, tmp_path):
        code = _run_and_capture_exit([
            "kodo", "--goal", "test", "--exchanges", "1001", "--project", str(tmp_path),
        ])
        assert code != 0

    def test_exchanges_1000_passes_validation(self, tmp_path):
        """--exchanges 1000 should be accepted (boundary)."""
        msgs = _get_fail_messages([
            "kodo", "--goal", "test", "--yes", "--exchanges", "1000",
            "--project", str(tmp_path),
        ])
        assert not any("exceed" in m for m in msgs)

    def test_exchanges_zero_fails(self, tmp_path):
        code = _run_and_capture_exit([
            "kodo", "--goal", "test", "--exchanges", "0", "--project", str(tmp_path),
        ])
        assert code != 0

    def test_exchanges_negative_fails(self, tmp_path):
        code = _run_and_capture_exit([
            "kodo", "--goal", "test", "--exchanges", "-5", "--project", str(tmp_path),
        ])
        assert code != 0


class TestCyclesUpperBound:
    """--cycles must not exceed 100."""

    def test_cycles_101_fails(self, tmp_path):
        code = _run_and_capture_exit([
            "kodo", "--goal", "test", "--cycles", "101", "--project", str(tmp_path),
        ])
        assert code != 0

    def test_cycles_100_passes_validation(self, tmp_path):
        """--cycles 100 should be accepted (boundary)."""
        msgs = _get_fail_messages([
            "kodo", "--goal", "test", "--yes", "--cycles", "100",
            "--project", str(tmp_path),
        ])
        assert not any("exceed" in m for m in msgs)

    def test_cycles_zero_fails(self, tmp_path):
        code = _run_and_capture_exit([
            "kodo", "--goal", "test", "--cycles", "0", "--project", str(tmp_path),
        ])
        assert code != 0


# ---------------------------------------------------------------------------
# 5. --focus empty string
# ---------------------------------------------------------------------------


class TestFocusValidation:
    """--focus must not be empty or whitespace-only."""

    def test_focus_empty_string_fails(self, tmp_path):
        code = _run_and_capture_exit([
            "kodo", "--improve", "--focus", "", "--project", str(tmp_path),
        ])
        assert code != 0

    def test_focus_whitespace_only_fails(self, tmp_path):
        code = _run_and_capture_exit([
            "kodo", "--improve", "--focus", "   ", "--project", str(tmp_path),
        ])
        assert code != 0

    def test_focus_without_improve_fails(self, tmp_path):
        """--focus without --improve should fail."""
        code = _run_and_capture_exit([
            "kodo", "--goal", "test", "--focus", "security", "--project", str(tmp_path),
        ])
        assert code != 0


# ---------------------------------------------------------------------------
# 6. Orchestrator / model compatibility
# ---------------------------------------------------------------------------


class TestOrchestratorModelCompatibility:
    """Gemini models should not be used with claude-code and vice versa."""

    def test_claude_code_with_gemini_pro_fails(self, tmp_path):
        code = _run_and_capture_exit([
            "kodo", "--goal", "test",
            "--orchestrator", "claude-code",
            "--orchestrator-model", "gemini-pro",
            "--project", str(tmp_path),
        ])
        assert code != 0

    def test_claude_code_with_gemini_flash_fails(self, tmp_path):
        code = _run_and_capture_exit([
            "kodo", "--goal", "test",
            "--orchestrator", "claude-code",
            "--orchestrator-model", "gemini-flash",
            "--project", str(tmp_path),
        ])
        assert code != 0

    def test_gemini_cli_with_opus_fails(self, tmp_path):
        code = _run_and_capture_exit([
            "kodo", "--goal", "test",
            "--orchestrator", "gemini-cli",
            "--orchestrator-model", "opus",
            "--project", str(tmp_path),
        ])
        assert code != 0

    def test_gemini_cli_with_sonnet_fails(self, tmp_path):
        code = _run_and_capture_exit([
            "kodo", "--goal", "test",
            "--orchestrator", "gemini-cli",
            "--orchestrator-model", "sonnet",
            "--project", str(tmp_path),
        ])
        assert code != 0

    def test_claude_code_with_opus_passes_validation(self, tmp_path):
        """claude-code + opus should be compatible (no compatibility error)."""
        msgs = _get_fail_messages([
            "kodo", "--goal", "test", "--yes",
            "--orchestrator", "claude-code",
            "--orchestrator-model", "opus",
            "--project", str(tmp_path),
        ])
        assert not any("incompatible" in m for m in msgs)

    def test_gemini_cli_with_gemini_pro_passes_validation(self, tmp_path):
        """gemini-cli + gemini-pro should be compatible."""
        msgs = _get_fail_messages([
            "kodo", "--goal", "test", "--yes",
            "--orchestrator", "gemini-cli",
            "--orchestrator-model", "gemini-pro",
            "--project", str(tmp_path),
        ])
        assert not any("incompatible" in m for m in msgs)

    def test_api_with_any_model_passes_validation(self, tmp_path):
        """api orchestrator accepts all models (no compatibility restriction)."""
        for model in ("opus", "sonnet", "gemini-pro", "gemini-flash"):
            msgs = _get_fail_messages([
                "kodo", "--goal", "test", "--yes",
                "--orchestrator", "api",
                "--orchestrator-model", model,
                "--project", str(tmp_path),
            ])
            assert not any("incompatible" in m for m in msgs), (
                f"api + {model} should be compatible"
            )

    def test_no_orchestrator_flag_skips_compat_check(self, tmp_path):
        """When --orchestrator is not specified, skip compatibility check."""
        msgs = _get_fail_messages([
            "kodo", "--goal", "test", "--yes",
            "--orchestrator-model", "gemini-flash",
            "--project", str(tmp_path),
        ])
        assert not any("incompatible" in m for m in msgs)


# ---------------------------------------------------------------------------
# 7. Error message content verification
# ---------------------------------------------------------------------------


class TestErrorMessages:
    """Verify error messages from _fail() contain useful information."""

    def _get_first_fail(self, argv: list[str]) -> str:
        """Run _main_inner and return the first _fail() message."""
        msgs = _get_fail_messages(argv)
        assert msgs, "Expected _fail to be called"
        return msgs[0]

    def test_exchanges_over_limit_message(self, tmp_path):
        msg = self._get_first_fail([
            "kodo", "--goal", "test", "--exchanges", "2000", "--project", str(tmp_path),
        ])
        assert "1000" in msg

    def test_cycles_over_limit_message(self, tmp_path):
        msg = self._get_first_fail([
            "kodo", "--goal", "test", "--cycles", "200", "--project", str(tmp_path),
        ])
        assert "100" in msg

    def test_project_not_exist_message(self, tmp_path):
        bad = str(tmp_path / "nope")
        msg = self._get_first_fail([
            "kodo", "--goal", "test", "--project", bad,
        ])
        assert "does not exist" in msg

    def test_project_is_file_message(self, tmp_path):
        f = tmp_path / "afile"
        f.write_text("x")
        msg = self._get_first_fail([
            "kodo", "--goal", "test", "--project", str(f),
        ])
        assert "not a directory" in msg

    def test_resume_empty_message(self, tmp_path):
        msg = self._get_first_fail([
            "kodo", "--resume", "", "--project", str(tmp_path),
        ])
        assert "empty string" in msg

    def test_focus_empty_message(self, tmp_path):
        msg = self._get_first_fail([
            "kodo", "--improve", "--focus", "", "--project", str(tmp_path),
        ])
        assert "empty" in msg

    def test_compat_claude_code_gemini_message(self, tmp_path):
        msg = self._get_first_fail([
            "kodo", "--goal", "test",
            "--orchestrator", "claude-code",
            "--orchestrator-model", "gemini-flash",
            "--project", str(tmp_path),
        ])
        assert "incompatible" in msg
        assert "claude-code" in msg

    def test_compat_gemini_cli_claude_message(self, tmp_path):
        msg = self._get_first_fail([
            "kodo", "--goal", "test",
            "--orchestrator", "gemini-cli",
            "--orchestrator-model", "opus",
            "--project", str(tmp_path),
        ])
        assert "incompatible" in msg
        assert "gemini-cli" in msg
