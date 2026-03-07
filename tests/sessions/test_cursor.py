"""Tests for kodo.sessions.cursor.CursorSession."""

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


def test_query_returns_result(tmp_path: Path):
    log.init(RunDir.create(tmp_path, "cursor_test"))
    session = CursorSession(model="composer-1.5")

    with patch(
        "kodo.sessions.base.subprocess.Popen",
        _make_popen_factory(result_text="All done!", chat_id="c1"),
    ):
        result = session.query("do stuff", tmp_path, max_turns=10)

    assert result.text == "All done!"
    assert result.is_error is False
    assert session.stats.queries == 1


def test_chat_id_captured_for_resume(tmp_path: Path):
    log.init(RunDir.create(tmp_path, "cursor_resume"))
    session = CursorSession(model="composer-1.5")

    with patch(
        "kodo.sessions.base.subprocess.Popen",
        _make_popen_factory(result_text="ok", chat_id="chat-xyz"),
    ):
        session.query("first", tmp_path, max_turns=10)

    # Second query should include --resume
    calls = []
    original_factory = _make_popen_factory(result_text="ok2", chat_id="chat-xyz")

    def capturing_factory(cmd, **kwargs):
        calls.append(cmd)
        return original_factory(cmd, **kwargs)

    with patch("kodo.sessions.base.subprocess.Popen", capturing_factory):
        session.query("second", tmp_path, max_turns=10)

    assert "--resume" in calls[0]
    assert "chat-xyz" in calls[0]


def test_system_prompt_prepended_once(tmp_path: Path):
    log.init(RunDir.create(tmp_path, "cursor_sysprompt"))
    session = CursorSession(model="composer-1.5", system_prompt="Be helpful.")

    procs = []

    def capturing_factory(cmd, **kwargs):
        proc = MockCursorProcess(cmd, result_text="ok", chat_id="c1", **kwargs)
        procs.append(proc)
        return proc

    with patch("kodo.sessions.base.subprocess.Popen", capturing_factory):
        session.query("task1", tmp_path, max_turns=10)
        session.query("task2", tmp_path, max_turns=10)

    # First query should have system prompt in the prompt
    assert "Be helpful." in procs[0].prompt
    # Second query should NOT have system prompt
    assert "Be helpful." not in procs[1].prompt


def test_error_on_nonzero_returncode(tmp_path: Path):
    log.init(RunDir.create(tmp_path, "cursor_error"))
    session = CursorSession(model="composer-1.5")

    with patch(
        "kodo.sessions.base.subprocess.Popen",
        _make_popen_factory(
            result_text="", chat_id="c1", returncode=1, stderr_text="fatal error\n"
        ),
    ):
        result = session.query("fail", tmp_path, max_turns=10)

    assert result.is_error is True


def test_reset_starts_fresh_session(tmp_path: Path):
    """After reset(), the next query starts a new chat (no --resume flag)."""
    log.init(RunDir.create(tmp_path, "cursor_reset"))
    session = CursorSession(model="composer-1.5")

    with patch(
        "kodo.sessions.base.subprocess.Popen",
        _make_popen_factory(result_text="ok", chat_id="c1"),
    ):
        session.query("task", tmp_path, max_turns=10)

    assert session.stats.queries == 1
    assert session.session_id == "c1"

    session.reset()
    assert session.stats.queries == 0

    # After reset, next query should NOT resume the old chat
    calls = []
    original_factory = _make_popen_factory(result_text="ok2", chat_id="c2")

    def capturing_factory(cmd, **kwargs):
        calls.append(cmd)
        return original_factory(cmd, **kwargs)

    with patch("kodo.sessions.base.subprocess.Popen", capturing_factory):
        session.query("new task", tmp_path, max_turns=10)

    assert "--resume" not in calls[0]


def test_clone_creates_fresh_session(tmp_path: Path):
    """clone() creates a new session with same config but no state."""
    log.init(RunDir.create(tmp_path, "cursor_clone"))
    session = CursorSession(
        model="composer-1.5",
        system_prompt="Test prompt",
        resume_chat_id="original-chat",
        timeout_s=3600,
    )

    clone = session.clone()

    assert clone.model == session.model
    assert clone.system_prompt == session.system_prompt
    assert clone._timeout_s == session._timeout_s
    # Clone should NOT have the chat_id (fresh state)
    assert clone._chat_id is None
    assert clone.stats.queries == 0


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


def test_json_decode_error_skipped(tmp_path: Path):
    """Malformed JSON lines are skipped without crashing."""
    log.init(RunDir.create(tmp_path, "cursor_bad_json"))
    session = CursorSession(model="composer-1.5")

    with patch(
        "kodo.sessions.base.subprocess.Popen",
        _make_popen_factory(
            result_text="final result",
            chat_id="c1",
            malformed_json=True,
        ),
    ):
        result = session.query("task", tmp_path, max_turns=10)

    # Should still get the result despite malformed JSON
    assert result.text == "final result"


def test_token_counts_accumulated(tmp_path: Path):
    """Token counts are accumulated from multiple messages."""
    log.init(RunDir.create(tmp_path, "cursor_tokens"))
    session = CursorSession(model="composer-1.5")

    with patch(
        "kodo.sessions.base.subprocess.Popen",
        _make_popen_factory(
            result_text="done",
            chat_id="c1",
            input_tokens=100,
            output_tokens=50,
        ),
    ):
        result = session.query("task", tmp_path, max_turns=10)

    assert result.input_tokens == 100
    assert result.output_tokens == 50
    assert session.stats.total_input_tokens == 100
    assert session.stats.total_output_tokens == 50


def test_spawn_error_returns_error_result(tmp_path: Path):
    """FileNotFoundError when spawning returns error QueryResult."""
    log.init(RunDir.create(tmp_path, "cursor_spawn_err"))
    session = CursorSession(model="composer-1.5")

    with patch(
        "kodo.sessions.base.subprocess.Popen",
        side_effect=FileNotFoundError("cursor-agent: command not found"),
    ):
        result = session.query("task", tmp_path, max_turns=10)

    assert result.is_error is True
    # classify_session_error will categorize this as "Binary not working"
    assert "binary" in result.text.lower() or "not found" in result.text.lower()


def test_permission_error_on_spawn(tmp_path: Path):
    """PermissionError when spawning returns error QueryResult."""
    log.init(RunDir.create(tmp_path, "cursor_perm_err"))
    session = CursorSession(model="composer-1.5")

    with patch(
        "kodo.sessions.base.subprocess.Popen",
        side_effect=PermissionError("Permission denied"),
    ):
        result = session.query("task", tmp_path, max_turns=10)

    assert result.is_error is True
    # classify_session_error categorizes permission as "Binary not working"
    assert "binary" in result.text.lower() or "permission" in result.text.lower()


def test_oserror_on_spawn(tmp_path: Path):
    """OSError when spawning returns error QueryResult."""
    log.init(RunDir.create(tmp_path, "cursor_os_err"))
    session = CursorSession(model="composer-1.5")

    with patch(
        "kodo.sessions.base.subprocess.Popen",
        side_effect=OSError("Resource temporarily unavailable"),
    ):
        result = session.query("task", tmp_path, max_turns=10)

    assert result.is_error is True
    assert result.elapsed_s >= 0


def test_error_classification_on_failure(tmp_path: Path):
    """classify_session_error hint is used when process fails without result."""
    log.init(RunDir.create(tmp_path, "cursor_classify"))
    session = CursorSession(model="composer-1.5")

    with patch(
        "kodo.sessions.base.subprocess.Popen",
        _make_popen_factory(
            result_text="",
            chat_id=None,
            returncode=1,
            stderr_text="Authentication failed: invalid API key",
        ),
    ):
        result = session.query("task", tmp_path, max_turns=10)

    assert result.is_error is True
    # Should have classified the error
    assert "authentication" in result.text.lower() or "api key" in result.text.lower()


def test_empty_lines_skipped(tmp_path: Path):
    """Empty lines in JSON stream are skipped."""
    log.init(RunDir.create(tmp_path, "cursor_empty"))
    session = CursorSession(model="composer-1.5")

    with patch(
        "kodo.sessions.base.subprocess.Popen",
        _make_popen_factory(
            result_text="success",
            chat_id="c1",
            empty_lines=True,
        ),
    ):
        result = session.query("task", tmp_path, max_turns=10)

    assert result.text == "success"
