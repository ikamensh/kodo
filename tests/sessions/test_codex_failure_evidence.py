"""Provider failures must survive the real CLI transport for callers to act on them."""

import os
import sys

import pytest

from kodo.sessions.codex import CodexSession


@pytest.mark.parametrize("returncode", [0, 1])
@pytest.mark.parametrize("partial_reply", [False, True])
def test_provider_failure_is_not_replaced_by_unrelated_mcp_auth_warning(
    tmp_path, monkeypatch, returncode, partial_reply,
):
    """Hive needs the original quota/model failure, including its reset hint."""
    message = "Usage limit reached for gpt-6-astra; resets in 2 hours."
    events = []
    if partial_reply:
        events.append({"type": "item.completed", "item": {
            "type": "agent_message", "text": "I will implement this now.",
        }})
    events.extend([
        {"type": "error", "message": message},
        {"type": "turn.failed", "error": {"message": message}},
    ])
    cli = tmp_path / "codex"
    cli.write_text(f"#!{sys.executable}\n" + (
        "import json,sys\n"
        "print('MCP startup: OAuth authorization required',file=sys.stderr)\n"
        f"for event in {events!r}: print(json.dumps(event))\n"
        f"sys.exit({returncode})\n"
    ))
    cli.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")

    result = CodexSession(model="gpt-6-astra").query("probe", tmp_path, max_turns=1)

    assert result.is_error
    assert result.text == message
