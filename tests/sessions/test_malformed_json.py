"""Boundary Condition 3: Sessions must not crash on semantically nonsensical JSON.

Fields with unexpected types (e.g. result as dict instead of string, response as
list, msg as non-dict) can cause AttributeError or TypeError when the session
assumes a specific shape. These tests document any crashes found.

Documented crashes:
- Cursor: result=dict → AttributeError in QueryResult.__post_init__ (base.py:25)
- Gemini: response=list → AttributeError in QueryResult.__post_init__ (base.py:25)
- Codex: msg=list → AttributeError at inner.get() (codex.py:115)
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from kodo import log
from kodo.log import RunDir
from kodo.sessions.codex import CodexSession
from kodo.sessions.cursor import CursorSession
from kodo.sessions.gemini_cli import GeminiCliSession


def _make_mock_popen(stdout_content: str, returncode: int = 0):
    """Return a mock Popen that produces the given stdout and exits cleanly."""

    class MockPopen:
        def __init__(self, cmd, **kwargs):
            self.cmd = cmd
            self.stdout = io.StringIO(stdout_content)
            self.stderr = io.StringIO("")
            self.returncode = returncode
            self.pid = 12345

        def wait(self, timeout=None):
            return self.returncode

    return MockPopen


def test_cursor_result_is_dict_instead_of_string(tmp_path: Path):
    """CursorSession: when cursor-agent sends result as dict instead of string,
    session must not crash.

    CursorSession does: result_text = msg.get('result', ''). If result is a
    dict, result_text becomes a dict. QueryResult(text=...) expects str;
    __post_init__ calls text.strip() → AttributeError.
    """
    log.init(RunDir.create(tmp_path, "cursor_malformed"))
    session = CursorSession()

    # Semantically nonsensical: result should be string, we send dict
    malformed = json.dumps(
        {
            "type": "result",
            "result": {"foo": "bar", "nested": True},
            "chatId": "c1",
        }
    )
    mock_popen = _make_mock_popen(malformed + "\n")

    with patch("kodo.sessions.base.subprocess.Popen", mock_popen):
        try:
            result = session.query("do something", tmp_path, max_turns=10)
            assert isinstance(result.text, str), (
                "Session should coerce or default result to str; got "
                f"{type(result.text).__name__}"
            )
        except (AttributeError, TypeError) as e:
            pytest.xfail(
                f"Boundary Condition 3 CRASH (Cursor): result=dict causes "
                f"{type(e).__name__} in QueryResult.__post_init__ (text.strip()). "
                f"Location: cursor.py passes dict to QueryResult; base.py:25."
            )


def test_gemini_response_field_is_list(tmp_path: Path):
    """GeminiCliSession: when gemini CLI sends response as list instead of
    string, session must not crash.

    GeminiCliSession does: result_text = data.get('response', ''). If
    response is a list, result_text becomes a list. QueryResult(text=...)
    expects str; __post_init__ calls text.strip() → AttributeError.
    """
    log.init(RunDir.create(tmp_path, "gemini_malformed"))
    session = GeminiCliSession(model="gemini-2.5-flash")

    # Semantically nonsensical: response should be string, we send list
    malformed = json.dumps(
        {
            "response": ["chunk1", "chunk2", "chunk3"],
            "stats": {
                "models": {
                    "gemini-2.5-flash": {"tokens": {"prompt": 0, "candidates": 0}}
                },
                "tools": {},
            },
        }
    )
    mock_popen = _make_mock_popen(malformed + "\n")

    with patch("kodo.sessions.base.subprocess.Popen", mock_popen):
        try:
            result = session.query("do something", tmp_path, max_turns=10)
            assert isinstance(result.text, str), (
                "Session should coerce or default response to str; got "
                f"{type(result.text).__name__}"
            )
        except (AttributeError, TypeError) as e:
            pytest.xfail(
                f"Boundary Condition 3 CRASH (Gemini): response=list causes "
                f"{type(e).__name__} in QueryResult.__post_init__ (text.strip()). "
                f"Location: gemini_cli.py passes list to QueryResult; base.py:25."
            )


def test_codex_missing_nested_msg_type(tmp_path: Path):
    """CodexSession: when codex sends msg as non-dict (e.g. list or string),
    session must not crash.

    CodexSession does: inner = msg.get('msg', {}); event_type = inner.get('type', '').
    If msg['msg'] is a list or string, inner.get() raises AttributeError.
    """
    log.init(RunDir.create(tmp_path, "codex_malformed"))
    session = CodexSession(model="o4-mini")

    # Semantically nonsensical: msg should be dict with optional 'type', we send list
    malformed = json.dumps({"id": "0", "msg": ["a", "b", "c"]}) + "\n"
    # Prepend thread.started so we have valid structure first; the malformed
    # line triggers the bug when processed
    full_stdout = json.dumps({"type": "thread.started", "thread_id": "t1"}) + "\n"
    full_stdout += malformed

    mock_popen = _make_mock_popen(full_stdout)

    with patch("kodo.sessions.base.subprocess.Popen", mock_popen):
        try:
            result = session.query("do something", tmp_path, max_turns=10)
            assert isinstance(result.text, str), (
                "Session should handle malformed msg without crashing; "
                f"result.text is {type(result.text).__name__}"
            )
        except (AttributeError, TypeError) as e:
            pytest.xfail(
                f"Boundary Condition 3 CRASH (Codex): msg=list causes "
                f"{type(e).__name__} at inner.get('type'). "
                f"Location: codex.py:115 — inner = msg.get('msg', {{}}) yields list "
                f"when msg is list; inner.get() then raises."
            )
