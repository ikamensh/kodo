"""Tests for kodo.sessions.codex.CodexSession."""

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


def test_query_returns_result(tmp_path: Path):
    log.init(RunDir.create(tmp_path, "codex_test"))
    session = CodexSession(model="o4-mini")

    with patch(
        "kodo.sessions.base.subprocess.Popen",
        _make_popen_factory(result_text="All done!", session_id="t1"),
    ):
        result = session.query("do stuff", tmp_path, max_turns=10)

    assert result.text == "All done!"
    assert result.is_error is False
    assert session.stats.queries == 1


def test_session_id_captured_for_resume(tmp_path: Path):
    log.init(RunDir.create(tmp_path, "codex_resume"))
    session = CodexSession(model="o4-mini")

    with patch(
        "kodo.sessions.base.subprocess.Popen",
        _make_popen_factory(result_text="ok", session_id="thread-xyz"),
    ):
        session.query("first", tmp_path, max_turns=10)

    assert session.session_id == "thread-xyz"

    # Second query should pass the new prompt (codex CLI has no resume subcommand)
    calls = []
    original_factory = _make_popen_factory(result_text="ok2", session_id="thread-xyz")

    def capturing_factory(cmd, **kwargs):
        calls.append(cmd)
        return original_factory(cmd, **kwargs)

    with patch("kodo.sessions.base.subprocess.Popen", capturing_factory):
        session.query("second", tmp_path, max_turns=10)

    assert "second" in calls[0]
    assert "resume" not in calls[0]


def test_system_prompt_prepended_once(tmp_path: Path):
    log.init(RunDir.create(tmp_path, "codex_sysprompt"))
    session = CodexSession(model="o4-mini", system_prompt="Be helpful.")

    procs = []

    def capturing_factory(cmd, **kwargs):
        proc = MockCodexProcess(cmd, result_text="ok", session_id="t1", **kwargs)
        procs.append(proc)
        return proc

    with patch("kodo.sessions.base.subprocess.Popen", capturing_factory):
        session.query("task1", tmp_path, max_turns=10)
        session.query("task2", tmp_path, max_turns=10)

    # First query: system prompt is in the prompt
    assert procs[0].prompt is not None
    assert "Be helpful." in procs[0].prompt

    # Second query: system prompt NOT prepended again, just the new prompt
    assert procs[1].prompt is not None
    assert "Be helpful." not in procs[1].prompt
    assert "task2" in procs[1].prompt


def test_error_on_nonzero_returncode(tmp_path: Path):
    log.init(RunDir.create(tmp_path, "codex_error"))
    session = CodexSession(model="o4-mini")

    with patch(
        "kodo.sessions.base.subprocess.Popen",
        _make_popen_factory(
            result_text="", session_id="t1", returncode=1, stderr_text="fatal error\n"
        ),
    ):
        result = session.query("fail", tmp_path, max_turns=10)

    assert result.is_error is True


def test_reset_starts_fresh_session(tmp_path: Path):
    """After reset(), the next query starts a new session (no resume)."""
    log.init(RunDir.create(tmp_path, "codex_reset"))
    session = CodexSession(model="o4-mini")

    with patch(
        "kodo.sessions.base.subprocess.Popen",
        _make_popen_factory(result_text="ok", session_id="t1"),
    ):
        session.query("task", tmp_path, max_turns=10)

    assert session.stats.queries == 1
    assert session.session_id == "t1"

    session.reset()
    assert session.stats.queries == 0

    # After reset, next query should start a fresh session (not resume)
    calls = []
    original_factory = _make_popen_factory(result_text="ok2", session_id="t2")

    def capturing_factory(cmd, **kwargs):
        calls.append(cmd)
        return original_factory(cmd, **kwargs)

    with patch("kodo.sessions.base.subprocess.Popen", capturing_factory):
        session.query("new task", tmp_path, max_turns=10)

    assert "resume" not in calls[0]


def test_tokens_extracted(tmp_path: Path):
    log.init(RunDir.create(tmp_path, "codex_tokens"))
    session = CodexSession(model="o4-mini")

    with patch(
        "kodo.sessions.base.subprocess.Popen",
        _make_popen_factory(
            result_text="done",
            session_id="t1",
            input_tokens=500,
            output_tokens=200,
        ),
    ):
        result = session.query("task", tmp_path, max_turns=10)

    assert result.input_tokens == 500
    assert result.output_tokens == 200
    assert session.stats.total_input_tokens == 500
    assert session.stats.total_output_tokens == 200


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


def test_clone_creates_fresh_session(tmp_path: Path):
    """clone() creates a new session with same config but no state."""
    log.init(RunDir.create(tmp_path, "codex_clone"))
    session = CodexSession(
        model="o4-mini",
        system_prompt="Test prompt",
        resume_session_id="original-session",
        sandbox="workspace-write",
        timeout_s=3600,
    )

    clone = session.clone()

    assert clone.model == session.model
    assert clone.system_prompt == session.system_prompt
    assert clone._sandbox == session._sandbox
    assert clone._timeout_s == session._timeout_s
    # Clone should NOT have the session_id (fresh state)
    assert clone._session_id is None
    assert clone.stats.queries == 0


def test_clone_independence(tmp_path: Path):
    """Verify cloned sessions don't share state."""
    log.init(RunDir.create(tmp_path, "codex_clone_independence"))

    original = CodexSession(
        model="o4-mini",
        system_prompt="Original prompt",
        sandbox="workspace-write",
        timeout_s=1800,
    )

    clone = original.clone()

    # Mutate original
    original._session_id = "mutated-session-id"
    original._stats.queries = 42
    original._system_prompt_sent = True

    # Clone should be unaffected
    assert clone._session_id is None
    assert clone.stats.queries == 0
    assert clone._system_prompt_sent is False

    # Verify config is copied correctly
    assert clone._sandbox == "workspace-write"

    # Verify they're different objects
    assert clone is not original
    assert clone._stats is not original._stats


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
    session = CodexSession(model="o4-mini", sandbox="full-auto")
    assert session._sandbox == "full-auto"


def test_json_decode_error_skipped(tmp_path: Path):
    """Malformed JSON lines are skipped without crashing."""
    log.init(RunDir.create(tmp_path, "codex_bad_json"))
    session = CodexSession(model="o4-mini")

    with patch(
        "kodo.sessions.base.subprocess.Popen",
        _make_popen_factory(
            result_text="final result",
            session_id="t1",
            malformed_json=True,
        ),
    ):
        result = session.query("task", tmp_path, max_turns=10)

    # Should still get the result despite malformed JSON
    assert result.text == "final result"


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
    assert "500" in result.text or "retry" in result.text.lower() or "failed" in result.text.lower()


def test_model_not_supported_hint(tmp_path: Path):
    """'not supported' error gets actionable hint."""
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
    assert "login" in result.text.lower() or "check" in result.text.lower()


def test_spawn_error_returns_error_result(tmp_path: Path):
    """FileNotFoundError when spawning returns error QueryResult."""
    log.init(RunDir.create(tmp_path, "codex_spawn_err"))
    session = CodexSession(model="o4-mini")

    with patch(
        "kodo.sessions.base.subprocess.Popen",
        side_effect=FileNotFoundError("codex: command not found"),
    ):
        result = session.query("task", tmp_path, max_turns=10)

    assert result.is_error is True
    assert "codex" in result.text or "not found" in result.text.lower()


def test_permission_error_on_spawn(tmp_path: Path):
    """PermissionError when spawning returns error QueryResult."""
    log.init(RunDir.create(tmp_path, "codex_perm_err"))
    session = CodexSession(model="o4-mini")

    with patch(
        "kodo.sessions.base.subprocess.Popen",
        side_effect=PermissionError("Permission denied"),
    ):
        result = session.query("task", tmp_path, max_turns=10)

    assert result.is_error is True
    assert result.elapsed_s >= 0


def test_error_classification_on_failure(tmp_path: Path):
    """classify_session_error hint is used when process fails."""
    log.init(RunDir.create(tmp_path, "codex_classify"))
    session = CodexSession(model="o4-mini")

    with patch(
        "kodo.sessions.base.subprocess.Popen",
        _make_popen_factory(
            result_text="",
            session_id=None,
            returncode=1,
            stderr_text="Subscription expired",
        ),
    ):
        result = session.query("task", tmp_path, max_turns=10)

    assert result.is_error is True
    # Should have classified the error
    assert "subscription" in result.text.lower() or "billing" in result.text.lower()


def test_empty_lines_skipped(tmp_path: Path):
    """Empty lines in JSON stream are skipped."""
    log.init(RunDir.create(tmp_path, "codex_empty"))
    session = CodexSession(model="o4-mini")

    with patch(
        "kodo.sessions.base.subprocess.Popen",
        _make_popen_factory(
            result_text="success",
            session_id="t1",
            empty_lines=True,
        ),
    ):
        result = session.query("task", tmp_path, max_turns=10)

    assert result.text == "success"


def test_command_construction(tmp_path: Path):
    """Verify command is constructed correctly with all parameters."""
    log.init(RunDir.create(tmp_path, "codex_cmd"))
    session = CodexSession(model="o4-mini", sandbox="full-auto")

    calls = []

    def capturing_factory(cmd, **kwargs):
        calls.append(cmd)
        return MockCodexProcess(cmd, result_text="ok", session_id="t1", **kwargs)

    with patch("kodo.sessions.base.subprocess.Popen", capturing_factory):
        session.query("test task", tmp_path, max_turns=10)

    cmd = calls[0]
    assert "codex" in cmd
    assert "exec" in cmd
    assert "--full-auto" in cmd
    assert "--json" in cmd
    assert "--sandbox" in cmd
    assert "full-auto" in cmd
    assert "-m" in cmd
    assert "o4-mini" in cmd
    assert str(tmp_path) in cmd
