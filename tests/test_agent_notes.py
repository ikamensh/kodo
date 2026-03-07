"""Tests for per-agent notes instruction injection."""

from __future__ import annotations

from unittest.mock import patch

from kodo.factory import _build_team_core, _ARCHITECT_DESC, _WORKER_FAST_DESC, _TESTER_DESC


def _force_claude_available():
    """Patch all backends so only claude is available."""
    return patch.dict(
        "kodo.factory.available_backends.__wrapped__.__self__",  # won't work; use has_ patches
    )


def _build_with_claude(**kwargs):
    """Build a team with all backends forced to claude-available-only."""
    with (
        patch("kodo.factory.has_claude", return_value=True),
        patch("kodo.factory.has_cursor", return_value=False),
        patch("kodo.factory.has_codex", return_value=False),
        patch("kodo.factory.has_gemini_cli", return_value=False),
        patch("kodo.factory.has_kimi", return_value=False),
    ):
        return _build_team_core(**kwargs)


def test_notes_appended_to_worker():
    """Worker agents get the notes instruction in their system prompt."""
    team = _build_with_claude(
        worker_fast_desc=_WORKER_FAST_DESC,
        worker_smart_desc="Smart worker.",
    )
    prompt = team["worker_fast"].session.system_prompt
    assert ".kodo/worker_fast-notes.md" in prompt
    assert "persistent notes file" in prompt


def test_notes_appended_to_tester():
    """Tester agent gets notes instruction."""
    team = _build_with_claude(
        worker_fast_desc=_WORKER_FAST_DESC,
        worker_smart_desc="Smart worker.",
        tester_desc=_TESTER_DESC,
    )
    prompt = team["tester"].session.system_prompt
    assert ".kodo/tester-notes.md" in prompt


def test_notes_appended_to_architect():
    """Architect gets notes instruction and no old architecture.md reference."""
    team = _build_with_claude(
        worker_fast_desc=_WORKER_FAST_DESC,
        worker_smart_desc="Smart worker.",
        architect_desc=_ARCHITECT_DESC,
    )
    prompt = team["architect"].session.system_prompt
    assert ".kodo/architect-notes.md" in prompt
    assert "architecture.md" not in prompt


def test_notes_role_placeholder():
    """The {role} placeholder is replaced with the actual agent key."""
    team = _build_with_claude(
        worker_fast_desc=_WORKER_FAST_DESC,
        worker_smart_desc="Smart worker.",
    )
    # worker_fast should have worker_fast in its notes path
    prompt = team["worker_fast"].session.system_prompt
    assert ".kodo/worker_fast-notes.md" in prompt
    assert "{role}" not in prompt

    # worker_smart should have worker_smart in its notes path
    prompt = team["worker_smart"].session.system_prompt
    assert ".kodo/worker_smart-notes.md" in prompt
    assert "{role}" not in prompt


def test_notes_json_team():
    """JSON-defined teams get notes instruction injected."""
    from kodo.team_config import build_team_from_json

    config = {
        "agents": {
            "my_worker": {
                "backend": "claude",
                "model": "sonnet",
                "description": "A worker",
                "max_turns": 10,
            }
        }
    }
    with (
        patch("kodo.factory.available_backends", return_value={"claude": True}),
    ):
        team = build_team_from_json(config)

    prompt = team["my_worker"].session.system_prompt
    assert ".kodo/my_worker-notes.md" in prompt
    assert "persistent notes file" in prompt
