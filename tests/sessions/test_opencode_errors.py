"""Structured provider errors remain actionable across the real CLI subprocess."""

import os
import sys

import pytest

from kodo.sessions.opencode import OpenCodeSession


@pytest.mark.parametrize("returncode", [0, 1])
@pytest.mark.parametrize("partial_reply", [False, True])
def test_error_events_override_partial_text_even_with_success_exit(
    tmp_path, monkeypatch, returncode, partial_reply,
):
    """Quota evidence must reach Hive even when the CLI prints prose or exits zero."""
    cli = tmp_path / "opencode"
    message = "Please retry later."
    events = [{"type": "error", "error": {"data": {"statusCode": 429, "message": message}}}]
    if partial_reply:
        events.insert(0, {"type": "text", "part": {"text": "Working on it."}})
    cli.write_text(f"#!{sys.executable}\n" + (
        "import json, sys\n"
        f"for event in {events!r}: print(json.dumps(event))\n"
        f"sys.exit({returncode})\n"
    ))
    cli.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")

    result = OpenCodeSession(model="opencode/test-free").query("work", tmp_path, max_turns=1)

    assert result.is_error
    assert result.text == f"HTTP 429: {message}"
