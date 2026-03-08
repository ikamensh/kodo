"""Live integration tests for KimiSession.

Run with: uv run pytest tests/sessions/test_kimi_live.py -v -m live

Requires:
  - kimi-agent-sdk installed: uv pip install kimi-agent-sdk
  - KIMI_API_KEY set in environment (get one at https://platform.moonshot.ai/console/api-keys)
  - kimi CLI authenticated: kimi /login
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from kodo import log
from kodo.log import RunDir

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.environ.get("KIMI_API_KEY"),
        reason="KIMI_API_KEY not set",
    ),
]


@pytest.fixture(autouse=True)
def _init_log(tmp_path: Path):
    log.init(RunDir.create(tmp_path, "kimi_live"))


@pytest.fixture()
def kimi_session():
    """Create a real KimiSession and close it after the test."""
    from kodo.sessions.kimi import KimiSession

    session = KimiSession()
    yield session
    session.close()


def test_simple_query(kimi_session, tmp_path: Path):
    """KimiSession can send a simple query and get a text response."""
    result = kimi_session.query(
        "Reply with exactly: HELLO_KODO_TEST",
        tmp_path,
        max_turns=5,
    )
    assert not result.is_error, f"Query failed: {result.text}"
    assert len(result.text) > 0
    assert "HELLO_KODO_TEST" in result.text


def test_token_tracking(kimi_session, tmp_path: Path):
    """Token usage is tracked after a query."""
    result = kimi_session.query(
        "What is 2+2? Reply with just the number.",
        tmp_path,
        max_turns=5,
    )
    assert not result.is_error, f"Query failed: {result.text}"
    # Token counts should be populated
    assert kimi_session.stats.total_input_tokens > 0 or kimi_session.stats.total_output_tokens > 0
    assert kimi_session.stats.queries == 1


def test_session_id_assigned(kimi_session, tmp_path: Path):
    """Session ID is available after first query."""
    assert kimi_session.session_id is None
    kimi_session.query("Say hi", tmp_path, max_turns=5)
    assert kimi_session.session_id is not None


def test_system_prompt(tmp_path: Path):
    """System prompt is applied to the first query."""
    from kodo.sessions.kimi import KimiSession

    session = KimiSession(system_prompt="Always end your response with BANANA.")
    try:
        result = session.query("What is 1+1?", tmp_path, max_turns=5)
        assert not result.is_error, f"Query failed: {result.text}"
        assert "BANANA" in result.text
    finally:
        session.close()


def test_multi_query_conversation(kimi_session, tmp_path: Path):
    """Multiple queries share the same session (conversation continuity)."""
    r1 = kimi_session.query(
        "Remember the word FLAMINGO. Reply with just 'OK'.",
        tmp_path,
        max_turns=5,
    )
    assert not r1.is_error, f"First query failed: {r1.text}"

    r2 = kimi_session.query(
        "What word did I ask you to remember? Reply with just the word.",
        tmp_path,
        max_turns=5,
    )
    assert not r2.is_error, f"Second query failed: {r2.text}"
    assert "FLAMINGO" in r2.text.upper()
    assert kimi_session.stats.queries == 2


def test_reset_starts_fresh(kimi_session, tmp_path: Path):
    """Reset clears stats and creates a new session."""
    kimi_session.query("Say hello", tmp_path, max_turns=5)
    old_sid = kimi_session.session_id
    assert kimi_session.stats.queries == 1

    kimi_session.reset()
    assert kimi_session.stats.queries == 0

    kimi_session.query("Say goodbye", tmp_path, max_turns=5)
    assert kimi_session.stats.queries == 1
    # New session should have a different ID
    assert kimi_session.session_id != old_sid
