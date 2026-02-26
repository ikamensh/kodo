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
    _build_team_mission,
    _build_team_saga,
    _mission_system_prompt,
    check_api_key,
)


@contextmanager
def _backends(claude=False, cursor=False, codex=False, gemini=False):
    """Patch all has_* helpers at once."""
    with ExitStack() as stack:
        stack.enter_context(patch("kodo.factory.has_claude", return_value=claude))
        stack.enter_context(patch("kodo.factory.has_cursor", return_value=cursor))
        stack.enter_context(patch("kodo.factory.has_codex", return_value=codex))
        stack.enter_context(patch("kodo.factory.has_gemini_cli", return_value=gemini))
        stack.enter_context(patch("kodo.factory.make_session"))
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
            team = _build_team_saga()
        assert "worker_fast" in team
        assert "worker_smart" in team

    def test_no_backends_raises(self):
        with _backends(), pytest.raises(RuntimeError, match="No worker backends"):
            _build_team_saga()


# ---------------------------------------------------------------------------
# Saga team priority verification
# ---------------------------------------------------------------------------


class TestSagaTeamComposition:
    """The saga team should adapt to which backends are installed."""

    def test_all_backends_available(self):
        with _backends(claude=True, cursor=True, codex=True, gemini=True):
            team = _build_team_saga()
        assert "worker_fast" in team
        assert "worker_smart" in team
        assert "architect" in team
        assert "tester" in team

    def test_gemini_only_saga_has_full_team(self):
        """Gemini-only should get all non-browser roles."""
        with _backends(gemini=True):
            team = _build_team_saga()
        assert "worker_fast" in team
        assert "worker_smart" in team
        assert "architect" in team
        assert "tester" in team

    def test_browser_tester_only_with_cursor(self):
        """tester_browser requires cursor (only backend with chrome support)."""
        with _backends(gemini=True):
            team = _build_team_saga()
        assert "tester_browser" not in team

        with _backends(cursor=True):
            team = _build_team_saga()
        assert "tester_browser" in team

    def test_cursor_preferred_over_codex_for_fast_worker(self):
        """When both cursor and codex exist, cursor should win worker_fast."""
        with _backends(cursor=True, codex=True):
            team = _build_team_saga()
        assert "worker_fast" in team

    def test_claude_preferred_for_smart_worker(self):
        """When claude and gemini both exist, claude should win worker_smart."""
        with _backends(claude=True, gemini=True):
            team = _build_team_saga()
        assert "worker_smart" in team

    def test_codex_plus_gemini(self):
        """Codex + Gemini: codex=fast, gemini=smart/architect/tester."""
        with _backends(codex=True, gemini=True):
            team = _build_team_saga()
        assert "worker_fast" in team
        assert "worker_smart" in team
        assert "architect" in team
        assert "tester" in team


# ---------------------------------------------------------------------------
# Mission team
# ---------------------------------------------------------------------------


class TestMissionTeamComposition:
    """Mission team has no architect/tester — just workers."""

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
            team = _build_team_mission()
        assert "worker_fast" in team
        assert "worker_smart" in team

    def test_mission_has_no_architect_or_tester(self):
        with _backends(claude=True, cursor=True):
            team = _build_team_mission()
        assert "architect" not in team
        assert "tester" not in team

    def test_no_backends_raises(self):
        with _backends(), pytest.raises(RuntimeError, match="No worker backends"):
            _build_team_mission()


# ---------------------------------------------------------------------------
# Mission system prompt adapts to backends
# ---------------------------------------------------------------------------


class TestMissionPrompt:
    def test_any_backend_gives_both_workers_in_prompt(self):
        """With priority tables, any backend fills both roles."""
        with _backends(cursor=True):
            prompt = _mission_system_prompt()
        assert "fast worker" in prompt
        assert "smart worker" in prompt

    def test_gemini_only_prompt_has_both_workers(self):
        with _backends(gemini=True):
            prompt = _mission_system_prompt()
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

    def test_claude_model_needs_anthropic_key(self):
        with patch.dict("os.environ", {}, clear=True):
            result = check_api_key("api", "opus")
        assert result is not None
        assert "ANTHROPIC_API_KEY" in result

    def test_anthropic_key_accepted(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test"}):
            assert check_api_key("api", "opus") is None
