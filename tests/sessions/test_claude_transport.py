"""ClaudeSession must accept large tool messages through the real SDK transport."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys

import claude_agent_sdk
import pytest

from kodo import log
from kodo.log import RunDir
from kodo.sessions.claude import ClaudeSession


@pytest.fixture
def scripted_claude(tmp_path, monkeypatch):
    """Only the executable is replaced; SDK framing, parsing and session I/O run."""
    script = tmp_path / "claude-scripted"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "if '--version' in sys.argv:\n"
        "    print('2.1.260 (Claude Code)'); sys.exit(0)\n"
        "def emit(message):\n"
        "    print(json.dumps(message), flush=True)\n"
        "for line in sys.stdin:\n"
        "    request = json.loads(line)\n"
        "    if request['type'] == 'control_request':\n"
        "        emit({'type': 'control_response', 'response': {'subtype': 'success',\n"
        "              'request_id': request['request_id'], 'response': {}}})\n"
        "    elif request['type'] == 'user':\n"
        "        emit({'type': 'user', 'message': {'role': 'user', 'content': [\n"
        "            {'type': 'tool_result', 'tool_use_id': 'screenshot-read', 'content': [\n"
        "                {'type': 'image', 'source': {'type': 'base64',\n"
        "                 'media_type': 'image/png', 'data': 'A' * (2 * 1024 * 1024)}}]}]}})\n"
        "        emit({'type': 'result', 'subtype': 'success', 'duration_ms': 1,\n"
        "              'duration_api_ms': 0, 'is_error': False, 'num_turns': 2,\n"
        "              'session_id': 'scripted-session', 'total_cost_usd': 0,\n"
        "              'usage': {'input_tokens': 100, 'output_tokens': 20},\n"
        "              'result': 'review complete'})\n"
    )
    script.chmod(0o755)
    client = claude_agent_sdk.ClaudeSDKClient
    monkeypatch.setattr(
        claude_agent_sdk,
        "ClaudeSDKClient",
        lambda options: client(options=replace(options, cli_path=str(script))),
    )
    log.init(RunDir.create(tmp_path, "claude_transport"))
    return tmp_path


def test_large_tool_message_does_not_abort_review(scripted_claude: Path):
    """An image-bearing tool result above the SDK's 1 MiB default must reach
    the terminal review, retain accounting and leave the session usable."""
    with ClaudeSession(session_timeout_s=5) as session:
        for _ in range(2):
            result = session.query(
                "Review the screenshot", scripted_claude, max_turns=5
            )
            assert not result.is_error, result.text
            assert result.text == "review complete"
            assert (result.input_tokens, result.output_tokens) == (100, 20)
            assert session.session_id == "scripted-session"
        assert session.stats.queries == 2


def test_larger_limit_survives_clone(scripted_claude: Path):
    """A fresh cloned review uses its caller's transport limit, not an SDK default."""
    with ClaudeSession(
        session_timeout_s=5, max_buffer_size=4 * 1024 * 1024
    ) as original:
        with original.clone() as cloned:
            assert cloned.max_buffer_size == original.max_buffer_size
            result = cloned.query("Review the screenshot", scripted_claude, max_turns=5)
            assert not result.is_error, result.text
            assert result.text == "review complete"


def test_explicit_limit_still_rejects_oversized_message(scripted_claude: Path):
    """A bounded stream fails visibly; it never trims tool evidence or accepts
    the terminal success that followed the message it could not decode."""
    with ClaudeSession(session_timeout_s=5, max_buffer_size=1024 * 1024) as session:
        result = session.query("Review the screenshot", scripted_claude, max_turns=5)
    assert result.is_error
    assert result.text.startswith("Claude session error during query:")
    assert "JSON message exceeded maximum buffer size of 1048576 bytes" in result.text
    assert "review complete" not in result.text


@pytest.mark.parametrize("limit", [0, -1])
def test_buffer_limit_must_be_positive(limit):
    """Reject invalid configuration before allocating a session thread."""
    with pytest.raises(ValueError, match="max_buffer_size must be positive"):
        ClaudeSession(max_buffer_size=limit)
