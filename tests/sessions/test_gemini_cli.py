"""Tests for kodo.sessions.gemini_cli.GeminiCliSession.

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
from kodo.sessions.gemini_cli import GeminiCliSession
from tests.mocks.gemini_cli_process import MockGeminiCliProcess


def _make_popen_factory(**defaults):
    """Return a factory that creates MockGeminiCliProcess with given defaults."""

    def factory(cmd, **kwargs):
        return MockGeminiCliProcess(cmd, **defaults, **kwargs)

    return factory


def test_resume_on_subsequent_queries(tmp_path: Path):
    """Gemini CLI uses --resume with session_id 'last' for follow-up queries."""
    log.init(RunDir.create(tmp_path, "gemini_resume"))
    session = GeminiCliSession(model="gemini-2.5-flash")

    with patch(
        "kodo.sessions.base.subprocess.Popen",
        _make_popen_factory(result_text="ok"),
    ):
        session.query("first", tmp_path, max_turns=10)

    assert session.session_id == "last"

    calls = []
    original_factory = _make_popen_factory(result_text="ok2")

    def capturing_factory(cmd, **kwargs):
        calls.append(cmd)
        return original_factory(cmd, **kwargs)

    with patch("kodo.sessions.base.subprocess.Popen", capturing_factory):
        session.query("second", tmp_path, max_turns=10)

    assert "--resume" in calls[0]


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


def test_cost_bucket_property(tmp_path: Path):
    """Test that cost_bucket returns correct value."""
    log.init(RunDir.create(tmp_path, "gemini_cost"))
    session = GeminiCliSession()
    assert session.cost_bucket == "gemini_api"


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
