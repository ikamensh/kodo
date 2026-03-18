"""Tests for team composition under different backend availability scenarios.

These test the USER-FACING guarantee: kodo should assemble a working team
from whatever backends are installed, and fail clearly when none are.

The priority tables in factory.py ensure every role is filled by the
best available backend.  These tests verify the priority order and that
all backend combinations produce a viable team.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from unittest.mock import patch

import pytest

from kodo.factory import (
    _build_team_full,
    _build_team_quick,
    _quick_system_prompt,
    check_api_key,
    check_backend_status,
)


@contextmanager
def _backends(claude=False, cursor=False, codex=False, gemini=False, kimi=False):
    """Patch all has_* helpers at once."""
    with ExitStack() as stack:
        stack.enter_context(
            patch("kodo.factory.has_claude", autospec=True, return_value=claude)
        )
        stack.enter_context(
            patch("kodo.factory.has_cursor", autospec=True, return_value=cursor)
        )
        stack.enter_context(
            patch("kodo.factory.has_codex", autospec=True, return_value=codex)
        )
        stack.enter_context(
            patch("kodo.factory.has_gemini_cli", autospec=True, return_value=gemini)
        )
        stack.enter_context(
            patch("kodo.factory.has_kimi", autospec=True, return_value=kimi)
        )
        stack.enter_context(patch("kodo.factory.make_session", autospec=True))
        yield


# ---------------------------------------------------------------------------
# Every single-backend scenario should produce a viable team
# ---------------------------------------------------------------------------


class TestSingleBackend:
    """Any single backend should fill both worker roles."""

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"claude": True},
            {"cursor": True},
            {"codex": True},
            {"gemini": True},
        ],
    )
    def test_single_backend_fills_both_workers(self, kwargs):
        with _backends(**kwargs):
            team = _build_team_full()
        assert "worker_fast" in team
        assert "worker_smart" in team

    def test_no_backends_raises(self):
        with _backends(), pytest.raises(RuntimeError, match="No worker backends"):
            _build_team_full()


# ---------------------------------------------------------------------------
# Full team priority verification
# ---------------------------------------------------------------------------


class TestFullTeamComposition:
    """The full team should adapt to which backends are installed."""

    def test_all_backends_available(self):
        with _backends(claude=True, cursor=True, codex=True, gemini=True):
            team = _build_team_full()
        assert "worker_fast" in team
        assert "worker_smart" in team
        assert "architect" in team
        assert "tester" in team

    def test_gemini_only_full_has_full_team(self):
        """Gemini-only should get all non-browser roles."""
        with _backends(gemini=True):
            team = _build_team_full()
        assert "worker_fast" in team
        assert "worker_smart" in team
        assert "architect" in team
        assert "tester" in team

    def test_browser_tester_only_with_cursor(self):
        """tester_browser requires cursor (only backend with chrome support)."""
        with _backends(gemini=True):
            team = _build_team_full()
        assert "tester_browser" not in team

        with _backends(cursor=True):
            team = _build_team_full()
        assert "tester_browser" in team

    def test_cursor_preferred_over_codex_for_fast_worker(self):
        """When both cursor and codex exist, cursor should win worker_fast."""
        with _backends(cursor=True, codex=True):
            team = _build_team_full()
        assert "worker_fast" in team

    def test_claude_preferred_for_smart_worker(self):
        """When claude and gemini both exist, claude should win worker_smart."""
        with _backends(claude=True, gemini=True):
            team = _build_team_full()
        assert "worker_smart" in team

    def test_codex_plus_gemini(self):
        """Codex + Gemini: codex=fast, gemini=smart/architect/tester."""
        with _backends(codex=True, gemini=True):
            team = _build_team_full()
        assert "worker_fast" in team
        assert "worker_smart" in team
        assert "architect" in team
        assert "tester" in team


# ---------------------------------------------------------------------------
# Quick team
# ---------------------------------------------------------------------------


class TestQuickTeamComposition:
    """Quick team has no architect/tester — just workers."""

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"claude": True},
            {"cursor": True},
            {"codex": True},
            {"gemini": True},
        ],
    )
    def test_any_single_backend_gives_both_workers(self, kwargs):
        with _backends(**kwargs):
            team = _build_team_quick()
        assert "worker_fast" in team
        assert "worker_smart" in team

    def test_quick_has_no_architect_or_tester(self):
        with _backends(claude=True, cursor=True):
            team = _build_team_quick()
        assert "architect" not in team
        assert "tester" not in team

    def test_no_backends_raises(self):
        with _backends(), pytest.raises(RuntimeError, match="No worker backends"):
            _build_team_quick()


# ---------------------------------------------------------------------------
# Quick system prompt adapts to backends
# ---------------------------------------------------------------------------


class TestQuickPrompt:
    def test_any_backend_gives_both_workers_in_prompt(self):
        """With priority tables, any backend fills both roles."""
        with _backends(cursor=True):
            prompt = _quick_system_prompt()
        assert "fast worker" in prompt
        assert "smart worker" in prompt

    def test_gemini_only_prompt_has_both_workers(self):
        with _backends(gemini=True):
            prompt = _quick_system_prompt()
        assert "fast worker" in prompt
        assert "smart worker" in prompt


# ---------------------------------------------------------------------------
# API key validation
# ---------------------------------------------------------------------------


class TestCheckApiKey:
    def test_claude_code_orchestrator_needs_no_key(self):
        assert check_api_key("claude-code", "opus") is None

    def test_gemini_model_needs_gemini_key(self):
        with patch.dict("os.environ", {}, clear=True):
            result = check_api_key("api", "gemini-flash")
        assert result is not None
        assert "GEMINI_API_KEY" in result

    def test_gemini_key_accepted(self):
        with patch.dict("os.environ", {"GEMINI_API_KEY": "test"}):
            assert check_api_key("api", "gemini-flash") is None

    def test_google_key_accepted_for_gemini(self):
        with patch.dict("os.environ", {"GOOGLE_API_KEY": "test"}):
            assert check_api_key("api", "gemini-pro") is None

    def test_ollama_local_needs_no_api_key(self):
        with patch(
            "kodo.models.list_ollama_models", autospec=True, return_value=["llama3.2"]
        ):
            assert check_api_key("api", "ollama-local") is None

    def test_explicit_ollama_model_needs_no_api_key(self):
        with patch.dict("os.environ", {}, clear=True):
            assert check_api_key("api", "ollama:qwen2.5-coder:14b") is None

    def test_claude_model_needs_anthropic_key(self):
        with patch.dict("os.environ", {}, clear=True):
            result = check_api_key("api", "opus")
        assert result is not None
        assert "ANTHROPIC_API_KEY" in result

    def test_anthropic_key_accepted(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test"}):
            assert check_api_key("api", "opus") is None


# ---------------------------------------------------------------------------
# Backend health status checks
# ---------------------------------------------------------------------------


class TestCheckBackendStatus:
    """check_backend_status() runs the preflight command and classifies output."""

    def test_healthy_backend(self):
        from subprocess import CompletedProcess

        result = CompletedProcess(
            ["claude", "--version"], 0, stdout="CLI 2.5.0\n", stderr=""
        )
        with patch("kodo.factory.subprocess.run", autospec=True, return_value=result):
            version, warning = check_backend_status("claude")
        assert version == "CLI 2.5.0"
        assert warning is None

    def test_auth_pattern_in_stderr(self):
        """Auth errors in stderr should produce a warning even on rc 0."""
        from subprocess import CompletedProcess

        result = CompletedProcess(
            ["claude", "--version"],
            0,
            stdout="CLI 2.5.0\n",
            stderr="Warning: authentication failed for current session\n",
        )
        with patch("kodo.factory.subprocess.run", autospec=True, return_value=result):
            version, warning = check_backend_status("claude")
        assert version == "CLI 2.5.0"
        assert warning is not None
        assert "Authentication" in warning

    def test_subscription_pattern_in_stderr(self):
        """Subscription/quota errors should produce a warning."""
        from subprocess import CompletedProcess

        result = CompletedProcess(
            ["claude", "--version"],
            0,
            stdout="CLI 2.5.0\n",
            stderr="quota exceeded\n",
        )
        with patch("kodo.factory.subprocess.run", autospec=True, return_value=result):
            version, warning = check_backend_status("claude")
        assert version == "CLI 2.5.0"
        assert warning is not None
        assert "Quota" in warning or "billing" in warning.lower()

    def test_nonzero_exit_with_auth_error(self):
        """Non-zero exit + auth pattern → version error + auth warning."""
        from subprocess import CompletedProcess

        result = CompletedProcess(
            ["claude", "--version"],
            1,
            stdout="",
            stderr="401 Unauthorized\n",
        )
        with patch("kodo.factory.subprocess.run", autospec=True, return_value=result):
            version, warning = check_backend_status("claude")
        assert "error" in version
        assert warning is not None
        assert "Authentication" in warning

    def test_nonzero_exit_generic(self):
        """Non-zero exit without known pattern → includes stderr snippet."""
        from subprocess import CompletedProcess

        result = CompletedProcess(
            ["claude", "--version"],
            42,
            stdout="",
            stderr="something unexpected\n",
        )
        with patch("kodo.factory.subprocess.run", autospec=True, return_value=result):
            version, warning = check_backend_status("claude")
        assert "error (exit 42)" in version
        assert warning is not None
        assert "something unexpected" in warning

    def test_timeout(self):
        from subprocess import TimeoutExpired

        with patch(
            "kodo.factory.subprocess.run",
            autospec=True,
            side_effect=TimeoutExpired("cmd", 15),
        ):
            version, warning = check_backend_status("claude")
        assert version == "timeout"
        assert warning is not None
        assert "timed out" in warning

    def test_os_error(self):
        with patch(
            "kodo.factory.subprocess.run",
            autospec=True,
            side_effect=OSError("permission denied"),
        ):
            version, warning = check_backend_status("claude")
        assert version == "error"
        assert "permission denied" in warning

    def test_unknown_backend(self):
        """Backend with no preflight command returns '?' and no warning."""
        version, warning = check_backend_status("nonexistent")
        assert version == "?"
        assert warning is None
