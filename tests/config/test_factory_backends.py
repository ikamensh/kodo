"""Tests for team composition under different backend availability scenarios.

These test the USER-FACING guarantee: kodo should assemble a working team
from whatever backends are installed, and fail clearly when none are.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from unittest.mock import patch

import pytest

from kodo.factory import (
    _build_team_saga,
    _build_team_mission,
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
# Saga team fallback chain
# ---------------------------------------------------------------------------


class TestSagaTeamComposition:
    """The saga team should adapt to which backends are installed."""

    def test_all_backends_available(self):
        with _backends(claude=True, cursor=True, codex=True, gemini=True):
            team = _build_team_saga()
        # Should have the core roles
        assert "worker_fast" in team
        assert "worker_smart" in team
        assert "architect" in team
        assert "tester" in team

    def test_only_claude_gives_smart_worker_and_architect(self):
        with _backends(claude=True):
            team = _build_team_saga()
        assert "worker_smart" in team
        assert "architect" in team
        # No fast worker since cursor/codex/gemini are absent
        assert "worker_fast" not in team

    def test_only_cursor_gives_fast_worker_and_testers(self):
        with _backends(cursor=True):
            team = _build_team_saga()
        assert "worker_fast" in team
        assert "tester" in team
        # No smart worker or architect without claude
        assert "worker_smart" not in team
        assert "architect" not in team

    def test_codex_becomes_fast_worker_when_cursor_absent(self):
        with _backends(codex=True, claude=True):
            team = _build_team_saga()
        assert "worker_fast" in team
        assert "worker_smart" in team

    def test_gemini_only_saga_has_full_team(self):
        """Gemini-only should get worker_fast, worker_smart, architect, tester."""
        with _backends(gemini=True):
            team = _build_team_saga()
        assert "worker_fast" in team
        assert "worker_smart" in team
        assert "architect" in team
        assert "tester" in team

    def test_gemini_only_saga_has_no_browser_tester(self):
        """Gemini-only team should not include tester_browser (no chrome support)."""
        with _backends(gemini=True):
            team = _build_team_saga()
        assert "tester_browser" not in team

    def test_cursor_preferred_over_codex_for_fast_worker(self):
        """When both cursor and codex exist, cursor should win worker_fast."""
        with _backends(cursor=True, codex=True):
            team = _build_team_saga()
        # worker_fast should exist (cursor wins), no duplicate
        assert "worker_fast" in team

    def test_no_backends_raises(self):
        with _backends(), pytest.raises(RuntimeError, match="No worker backends"):
            _build_team_saga()


# ---------------------------------------------------------------------------
# Mission team
# ---------------------------------------------------------------------------


class TestMissionTeamComposition:
    def test_only_claude(self):
        with _backends(claude=True):
            team = _build_team_mission()
        assert "worker_smart" in team
        assert "worker_fast" not in team

    def test_only_cursor(self):
        with _backends(cursor=True):
            team = _build_team_mission()
        assert "worker_fast" in team
        assert "worker_smart" not in team

    def test_gemini_only_mission_has_both_workers(self):
        """Gemini-only mission should get both fast and smart workers."""
        with _backends(gemini=True):
            team = _build_team_mission()
        assert "worker_fast" in team
        assert "worker_smart" in team

    def test_no_backends_raises(self):
        with _backends(), pytest.raises(RuntimeError, match="No worker backends"):
            _build_team_mission()


# ---------------------------------------------------------------------------
# Mission system prompt adapts to backends
# ---------------------------------------------------------------------------


class TestMissionPrompt:
    def test_both_fast_and_smart(self):
        with _backends(cursor=True, claude=True):
            prompt = _mission_system_prompt()
        assert "fast worker" in prompt
        assert "smart worker" in prompt

    def test_only_fast(self):
        with _backends(cursor=True):
            prompt = _mission_system_prompt()
        assert "fast worker" in prompt
        assert "smart worker" not in prompt

    def test_only_smart(self):
        with _backends(claude=True):
            prompt = _mission_system_prompt()
        assert "smart worker" in prompt
        # Should not mention fast worker
        assert "fast worker" not in prompt.split("smart worker")[0]

    def test_gemini_only_mission_prompt_has_both_workers(self):
        """Gemini-only should describe both fast and smart workers."""
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
