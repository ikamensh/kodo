"""Tests for kodo.sessions.gemini_cli.GeminiCliSession."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from kodo import log
from kodo.log import RunDir
from kodo.sessions.gemini_cli import GeminiCliSession
from tests.mocks.gemini_cli_process import MockGeminiCliProcess


def _make_popen_factory(**defaults):
    """Return a factory that creates MockGeminiCliProcess with given defaults."""

    def factory(cmd, **kwargs):
        return MockGeminiCliProcess(cmd, **defaults, **kwargs)

    return factory


def test_query_returns_result(tmp_path: Path):
    log.init(RunDir.create(tmp_path, "gemini_test"))
    session = GeminiCliSession(model="gemini-2.5-flash")

    with patch(
        "kodo.sessions.base.subprocess.Popen",
        _make_popen_factory(result_text="All done!"),
    ):
        result = session.query("do stuff", tmp_path, max_turns=10)

    assert result.text == "All done!"
    assert result.is_error is False
    assert session.stats.queries == 1


def test_resume_on_subsequent_queries(tmp_path: Path):
    log.init(RunDir.create(tmp_path, "gemini_resume"))
    session = GeminiCliSession(model="gemini-2.5-flash")

    with patch(
        "kodo.sessions.base.subprocess.Popen",
        _make_popen_factory(result_text="ok"),
    ):
        session.query("first", tmp_path, max_turns=10)

    assert session.session_id == "last"

    # Second query should include --resume
    calls = []
    original_factory = _make_popen_factory(result_text="ok2")

    def capturing_factory(cmd, **kwargs):
        calls.append(cmd)
        return original_factory(cmd, **kwargs)

    with patch("kodo.sessions.base.subprocess.Popen", capturing_factory):
        session.query("second", tmp_path, max_turns=10)

    assert "--resume" in calls[0]


def test_system_prompt_prepended_once(tmp_path: Path):
    log.init(RunDir.create(tmp_path, "gemini_sysprompt"))
    session = GeminiCliSession(model="gemini-2.5-flash", system_prompt="Be helpful.")

    procs = []

    def capturing_factory(cmd, **kwargs):
        proc = MockGeminiCliProcess(cmd, result_text="ok", **kwargs)
        procs.append(proc)
        return proc

    with patch("kodo.sessions.base.subprocess.Popen", capturing_factory):
        session.query("task1", tmp_path, max_turns=10)
        session.query("task2", tmp_path, max_turns=10)

    # First query should have system prompt in the prompt
    assert procs[0].prompt is not None
    assert "Be helpful." in procs[0].prompt

    # Second query should NOT have system prompt
    assert procs[1].prompt is not None
    assert "Be helpful." not in procs[1].prompt


def test_error_on_nonzero_returncode(tmp_path: Path):
    log.init(RunDir.create(tmp_path, "gemini_error"))
    session = GeminiCliSession(model="gemini-2.5-flash")

    with patch(
        "kodo.sessions.base.subprocess.Popen",
        _make_popen_factory(result_text="", returncode=1, stderr_text="fatal error\n"),
    ):
        result = session.query("fail", tmp_path, max_turns=10)

    assert result.is_error is True
    assert "fatal error" in result.text


def test_reset_starts_fresh_session(tmp_path: Path):
    """After reset(), the next query starts a new session (no --resume)."""
    log.init(RunDir.create(tmp_path, "gemini_reset"))
    session = GeminiCliSession(model="gemini-2.5-flash")

    with patch(
        "kodo.sessions.base.subprocess.Popen",
        _make_popen_factory(result_text="ok"),
    ):
        session.query("task", tmp_path, max_turns=10)

    assert session.stats.queries == 1
    assert session.session_id is not None

    session.reset()
    assert session.stats.queries == 0
    assert session.session_id is None

    # After reset, next query should NOT have --resume
    calls = []
    original_factory = _make_popen_factory(result_text="ok2")

    def capturing_factory(cmd, **kwargs):
        calls.append(cmd)
        return original_factory(cmd, **kwargs)

    with patch("kodo.sessions.base.subprocess.Popen", capturing_factory):
        session.query("new task", tmp_path, max_turns=10)

    assert "--resume" not in calls[0]


def test_tokens_extracted(tmp_path: Path):
    log.init(RunDir.create(tmp_path, "gemini_tokens"))
    session = GeminiCliSession(model="gemini-2.5-flash")

    with patch(
        "kodo.sessions.base.subprocess.Popen",
        _make_popen_factory(
            result_text="done",
            input_tokens=500,
            output_tokens=200,
        ),
    ):
        result = session.query("task", tmp_path, max_turns=10)

    assert result.input_tokens == 500
    assert result.output_tokens == 200
    assert session.stats.total_input_tokens == 500
    assert session.stats.total_output_tokens == 200


def test_cwd_set_to_project_dir(tmp_path: Path):
    """Gemini CLI uses cwd instead of --cd flag."""
    log.init(RunDir.create(tmp_path, "gemini_cwd"))
    session = GeminiCliSession(model="gemini-2.5-flash")

    kwargs_captured = []

    def capturing_factory(cmd, **kwargs):
        kwargs_captured.append(kwargs)
        return MockGeminiCliProcess(cmd, result_text="ok", **kwargs)

    with patch("kodo.sessions.base.subprocess.Popen", capturing_factory):
        session.query("task", tmp_path, max_turns=10)

    assert kwargs_captured[0]["cwd"] == str(tmp_path)


def test_clone_creates_independent_session(tmp_path: Path):
    """Test that clone() creates a new session with same config."""
    log.init(RunDir.create(tmp_path, "gemini_clone"))
    session = GeminiCliSession(
        model="gemini-2.5-flash",
        system_prompt="Be helpful",
        timeout_s=3600,
    )

    cloned = session.clone()

    # Verify same config
    assert cloned.model == session.model
    assert cloned.system_prompt == session.system_prompt
    assert cloned._timeout_s == session._timeout_s

    # Verify independent state
    assert cloned is not session
    assert cloned._has_queried is False
    assert cloned._resume_next is False


def test_cost_bucket_property(tmp_path: Path):
    """Test that cost_bucket returns correct value."""
    log.init(RunDir.create(tmp_path, "gemini_cost"))
    session = GeminiCliSession()
    assert session.cost_bucket == "gemini_api"


def test_spawn_file_not_found_error(tmp_path: Path):
    """Test handling when gemini CLI is not found."""
    log.init(RunDir.create(tmp_path, "gemini_notfound"))
    session = GeminiCliSession()

    def raise_file_not_found(cmd, **kwargs):
        raise FileNotFoundError("gemini: command not found")

    with patch("kodo.sessions.base.subprocess.Popen", raise_file_not_found):
        result = session.query("test", tmp_path, max_turns=10)

    assert result.is_error is True
    assert "gemini" in result.text.lower()
    assert session.stats.queries == 0  # Query not counted on spawn failure


def test_spawn_permission_error(tmp_path: Path):
    """Test handling when gemini CLI lacks execute permissions."""
    log.init(RunDir.create(tmp_path, "gemini_perms"))
    session = GeminiCliSession()

    def raise_permission_error(cmd, **kwargs):
        raise PermissionError("Permission denied: gemini")

    with patch("kodo.sessions.base.subprocess.Popen", raise_permission_error):
        result = session.query("test", tmp_path, max_turns=10)

    assert result.is_error is True
    # classify_session_error maps PermissionError to a user-friendly message
    assert "gemini" in result.text.lower()


def test_spawn_os_error(tmp_path: Path):
    """Test handling of generic OS errors during spawn."""
    log.init(RunDir.create(tmp_path, "gemini_oserr"))
    session = GeminiCliSession()

    def raise_os_error(cmd, **kwargs):
        raise OSError("Cannot allocate memory")

    with patch("kodo.sessions.base.subprocess.Popen", raise_os_error):
        result = session.query("test", tmp_path, max_turns=10)

    assert result.is_error is True
    assert session.stats.queries == 0


def test_json_response_with_error_field(tmp_path: Path):
    """Test handling when JSON response contains error field."""
    log.init(RunDir.create(tmp_path, "gemini_jsonerr"))
    session = GeminiCliSession()

    with patch(
        "kodo.sessions.base.subprocess.Popen",
        _make_popen_factory(
            result_text="",  # Empty result_text so error message is used
            error={"message": "API rate limit exceeded"},
        ),
    ):
        result = session.query("test", tmp_path, max_turns=10)

    assert result.is_error is True
    assert "rate limit" in result.text.lower()


def test_tool_only_response_with_tool_calls(tmp_path: Path):
    """Test when gemini makes tool calls but returns no text."""
    log.init(RunDir.create(tmp_path, "gemini_toolonly"))
    session = GeminiCliSession()

    with patch(
        "kodo.sessions.base.subprocess.Popen",
        _make_popen_factory(
            json_data={
                "response": "",  # Empty text response
                "stats": {
                    "models": {
                        "gemini-2.5-flash": {
                            "tokens": {"prompt": 100, "candidates": 50}
                        }
                    },
                    "tools": {"totalCalls": 3},
                },
            }
        ),
    ):
        result = session.query("test", tmp_path, max_turns=10)

    assert result.is_error is False
    assert "3 tool call(s)" in result.text
    assert result.output_tokens == 50


def test_tool_only_response_without_tool_stats(tmp_path: Path):
    """Test empty response with output tokens but no tool call stats."""
    log.init(RunDir.create(tmp_path, "gemini_notool"))
    session = GeminiCliSession()

    with patch(
        "kodo.sessions.base.subprocess.Popen",
        _make_popen_factory(
            json_data={
                "response": "",
                "stats": {
                    "models": {
                        "gemini-2.5-flash": {
                            "tokens": {"prompt": 100, "candidates": 20}
                        }
                    },
                    "tools": {},  # No totalCalls
                },
            }
        ),
    ):
        result = session.query("test", tmp_path, max_turns=10)

    assert result.is_error is False
    assert result.text == "[completed, no text response]"


def test_json_decode_error_fallback(tmp_path: Path):
    """Test that malformed JSON falls back to raw text."""
    log.init(RunDir.create(tmp_path, "gemini_badjson"))
    session = GeminiCliSession()

    with patch(
        "kodo.sessions.base.subprocess.Popen",
        _make_popen_factory(
            stdout_text="{this is not valid json at all",
        ),
    ):
        result = session.query("test", tmp_path, max_turns=10)

    assert result.is_error is False
    assert result.text == "{this is not valid json at all"
