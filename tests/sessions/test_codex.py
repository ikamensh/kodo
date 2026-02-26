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
