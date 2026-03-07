"""Tests for kodo.sessions.cursor.CursorSession.

Only session-specific behavior is tested here.  Base-class behaviour
(query lifecycle, reset, clone, system-prompt prepend, spawn errors,
error classification, empty-line / malformed-JSON skipping, token
extraction) is covered by tests/sessions/test_base.py.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from kodo import log
from kodo.log import RunDir
from kodo.sessions.cursor import CursorSession
from tests.mocks.cursor_process import MockCursorProcess


def _make_popen_factory(**defaults):
    """Return a factory that creates MockCursorProcess with given defaults."""

    def factory(cmd, **kwargs):
        return MockCursorProcess(cmd, **defaults, **kwargs)

    return factory


def test_chat_id_captured_for_resume(tmp_path: Path):
    """Second query includes --resume with the captured chat_id."""
    log.init(RunDir.create(tmp_path, "cursor_resume"))
    session = CursorSession(model="composer-1.5")

    with patch(
        "kodo.sessions.base.subprocess.Popen",
        _make_popen_factory(result_text="ok", chat_id="chat-xyz"),
    ):
        session.query("first", tmp_path, max_turns=10)

    calls = []
    original_factory = _make_popen_factory(result_text="ok2", chat_id="chat-xyz")

    def capturing_factory(cmd, **kwargs):
        calls.append(cmd)
        return original_factory(cmd, **kwargs)

    with patch("kodo.sessions.base.subprocess.Popen", capturing_factory):
        session.query("second", tmp_path, max_turns=10)

    assert "--resume" in calls[0]
    assert "chat-xyz" in calls[0]


def test_cost_bucket_is_cursor_subscription():
    """cost_bucket property returns 'cursor_subscription'."""
    session = CursorSession(model="composer-1.5")
    assert session.cost_bucket == "cursor_subscription"


def test_session_id_property():
    """session_id property returns current chat_id."""
    session = CursorSession(model="composer-1.5")
    assert session.session_id is None

    session._chat_id = "chat-123"
    assert session.session_id == "chat-123"


def test_resume_chat_id_initialization():
    """Can initialize session with resume_chat_id."""
    session = CursorSession(model="composer-1.5", resume_chat_id="existing-chat")
    assert session.session_id == "existing-chat"
