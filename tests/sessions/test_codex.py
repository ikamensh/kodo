"""Tests for kodo.sessions.codex.CodexSession.

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
from kodo.sessions.codex import CodexSession
from tests.mocks.codex_process import MockCodexProcess


def _make_popen_factory(**defaults):
    """Return a factory that creates MockCodexProcess with given defaults."""

    def factory(cmd, **kwargs):
        return MockCodexProcess(cmd, **defaults, **kwargs)

    return factory


def test_session_id_captured_for_resume(tmp_path: Path):
    """Codex has no resume subcommand — second query just passes the new prompt."""
    log.init(RunDir.create(tmp_path, "codex_resume"))
    session = CodexSession(model="o4-mini")

    with patch(
        "kodo.sessions.base.subprocess.Popen",
        _make_popen_factory(result_text="ok", session_id="thread-xyz"),
    ):
        session.query("first", tmp_path, max_turns=10)

    assert session.session_id == "thread-xyz"

    calls = []
    original_factory = _make_popen_factory(result_text="ok2", session_id="thread-xyz")

    def capturing_factory(cmd, **kwargs):
        calls.append(cmd)
        return original_factory(cmd, **kwargs)

    with patch("kodo.sessions.base.subprocess.Popen", capturing_factory):
        session.query("second", tmp_path, max_turns=10)

    assert "second" in calls[0]
    assert "resume" not in calls[0]


def test_bad_model_returns_error(tmp_path: Path):
    """Unit-test version of live TestCodexSession.test_bad_model_returns_error."""
    log.init(RunDir.create(tmp_path, "codex_bad_model"))
    session = CodexSession(model="nonexistent-model-xyz")

    with patch(
        "kodo.sessions.base.subprocess.Popen",
        _make_popen_factory(error_message="model does not exist"),
    ):
        result = session.query("do stuff", tmp_path, max_turns=5)

    assert result.is_error is True
    assert result.text
    assert "not supported" in result.text or "does not exist" in result.text


def test_cost_bucket_is_codex_subscription():
    """cost_bucket property returns 'codex_subscription'."""
    session = CodexSession(model="o4-mini")
    assert session.cost_bucket == "codex_subscription"


def test_session_id_property():
    """session_id property returns current session ID."""
    session = CodexSession(model="o4-mini")
    assert session.session_id is None

    session._session_id = "thread-123"
    assert session.session_id == "thread-123"


def test_custom_sandbox_parameter():
    """Can initialize session with custom sandbox."""
    session = CodexSession(model="o4-mini", sandbox="danger-full-access")
    assert session._sandbox == "danger-full-access"


def test_nested_message_format(tmp_path: Path):
    """Codex nested message format {"msg": {...}} is handled."""
    log.init(RunDir.create(tmp_path, "codex_nested"))
    session = CodexSession(model="o4-mini")

    with patch(
        "kodo.sessions.base.subprocess.Popen",
        _make_popen_factory(
            result_text="nested result",
            session_id="t1",
            nested_format=True,
        ),
    ):
        result = session.query("task", tmp_path, max_turns=10)

    assert result.text == "nested result"


def test_legacy_turn_completed_tokens(tmp_path: Path):
    """Legacy turn.completed format for token counts is handled."""
    log.init(RunDir.create(tmp_path, "codex_legacy_tokens"))
    session = CodexSession(model="o4-mini")

    with patch(
        "kodo.sessions.base.subprocess.Popen",
        _make_popen_factory(
            result_text="done",
            session_id="t1",
            legacy_tokens=True,
            input_tokens=300,
            output_tokens=150,
        ),
    ):
        result = session.query("task", tmp_path, max_turns=10)

    assert result.input_tokens == 300
    assert result.output_tokens == 150


def test_legacy_item_completed_format(tmp_path: Path):
    """Legacy item.completed format for result text is handled."""
    log.init(RunDir.create(tmp_path, "codex_legacy_item"))
    session = CodexSession(model="o4-mini")

    with patch(
        "kodo.sessions.base.subprocess.Popen",
        _make_popen_factory(
            result_text="assistant response",
            session_id="t1",
            legacy_item=True,
        ),
    ):
        result = session.query("task", tmp_path, max_turns=10)

    assert result.text == "assistant response"


def test_current_item_completed_agent_message_format(tmp_path: Path):
    """Codex 0.139 emits item.completed with item.type=agent_message."""
    log.init(RunDir.create(tmp_path, "codex_current_item"))
    session = CodexSession(model="o4-mini")

    with patch(
        "kodo.sessions.base.subprocess.Popen",
        _make_popen_factory(
            result_text="",
            session_id="t1",
            extra_messages=[
                {
                    "type": "item.completed",
                    "item": {
                        "id": "item_0",
                        "type": "agent_message",
                        "text": "assistant response",
                    },
                }
            ],
        ),
    ):
        result = session.query("task", tmp_path, max_turns=10)

    assert result.text == "assistant response"


def test_error_message_captured(tmp_path: Path):
    """Error messages from Codex are captured and surfaced."""
    log.init(RunDir.create(tmp_path, "codex_error_msg"))
    session = CodexSession(model="o4-mini")

    with patch(
        "kodo.sessions.base.subprocess.Popen",
        _make_popen_factory(
            result_text="",
            session_id="t1",
            error_message="API request failed: status 429",
            returncode=0,  # Codex exits 0 even on API failure
        ),
    ):
        result = session.query("task", tmp_path, max_turns=10)

    assert result.is_error is True
    assert "429" in result.text or "failed" in result.text.lower()


def test_background_event_errors_captured(tmp_path: Path):
    """background_event messages with errors are captured."""
    log.init(RunDir.create(tmp_path, "codex_bg_error"))
    session = CodexSession(model="o4-mini")

    with patch(
        "kodo.sessions.base.subprocess.Popen",
        _make_popen_factory(
            result_text="",
            session_id="t1",
            background_error="error: Retry failed: status 500",
            returncode=0,
        ),
    ):
        result = session.query("task", tmp_path, max_turns=10)

    # Codex detects errors even with returncode=0 when error_messages exist
    assert result.is_error is True
    assert (
        "500" in result.text
        or "retry" in result.text.lower()
        or "failed" in result.text.lower()
    )


def test_model_not_supported_preserves_provider_message(tmp_path: Path):
    """Model errors stay intact instead of adding misleading login advice."""
    log.init(RunDir.create(tmp_path, "codex_unsupported"))
    session = CodexSession(model="bad-model")

    with patch(
        "kodo.sessions.base.subprocess.Popen",
        _make_popen_factory(
            error_message="model not supported for your account",
            returncode=0,
        ),
    ):
        result = session.query("task", tmp_path, max_turns=10)

    assert result.is_error is True
    assert result.text == "model not supported for your account"


def test_command_construction(tmp_path: Path):
    """Verify command is constructed correctly with all parameters."""
    log.init(RunDir.create(tmp_path, "codex_cmd"))
    session = CodexSession(model="o4-mini", sandbox="danger-full-access")

    calls = []

    def capturing_factory(cmd, **kwargs):
        calls.append(cmd)
        return MockCodexProcess(cmd, result_text="ok", session_id="t1", **kwargs)

    with patch("kodo.sessions.base.subprocess.Popen", capturing_factory):
        session.query("test task", tmp_path, max_turns=10)

    cmd = calls[0]
    assert "codex" in cmd
    assert "exec" in cmd
    assert "--full-auto" not in cmd
    assert "--json" in cmd
    assert "--sandbox" in cmd
    assert "danger-full-access" in cmd
    assert "-m" in cmd
    assert "o4-mini" in cmd
    assert str(tmp_path) in cmd
