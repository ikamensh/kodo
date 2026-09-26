"""Completed tool loops and interrupted work remain distinguishable at the CLI boundary."""

import os
import sys

import pytest

from kodo.sessions.opencode import OpenCodeSession


@pytest.mark.parametrize("recovered", [False, True])
def test_terminal_tool_refusal_is_incomplete_but_a_recovered_call_can_finish(
    tmp_path, monkeypatch, recovered,
):
    """A real Muse run exited zero after a rejected cp from /tmp, with only commentary.

    Replay the three decisive native events through a real CLI subprocess. A later
    completed response resolves a recoverable error without losing token accounting.
    """
    events = [
        {"type": "text", "part": {"text": "Assignment logic updated — testing shared demand across presses.",
                                  "metadata": {"openai": {"phase": "commentary"}}}},
        {"type": "tool_use", "part": {"tool": "bash", "state": {
            "status": "error", "input": {"command": "cp /tmp/tradeoff.json scenarios/tradeoff.json"},
            "error": "The user rejected permission to use this specific tool call."}}},
        {"type": "step_finish", "part": {"reason": "tool-calls", "tokens": {"input": 863, "output": 145}}},
    ]
    if recovered:
        events.extend([
            {"type": "text", "part": {"text": "Completed using a file inside the project."}},
            {"type": "step_finish", "part": {"reason": "stop", "tokens": {"input": 100, "output": 20}}},
        ])
    cli = tmp_path / "opencode"
    cli.write_text(f"#!{sys.executable}\nimport json\n"
                   f"for event in {events!r}: print(json.dumps(event))\n")
    cli.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])

    result = OpenCodeSession(model="opencode/test-free").query("work", tmp_path, max_turns=1)

    assert not result.is_error
    if recovered:
        assert not result.incomplete_reason
        assert result.text == "Completed using a file inside the project."
        assert result.input_tokens == 963
    else:
        assert "tool-calls" in result.incomplete_reason
        assert "bash" in result.incomplete_reason
        assert "rejected permission" in result.incomplete_reason
        assert result.input_tokens == 863


def test_eof_without_a_terminal_stop_is_incomplete_even_after_text(tmp_path, monkeypatch):
    """A truncated stream cannot certify completion merely by having printed text."""
    cli = tmp_path / "opencode"
    cli.write_text(f"#!{sys.executable}\n"
                   "print('{\"type\":\"text\",\"part\":{\"text\":\"I am still editing the file.\"}}')\n")
    cli.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    result = OpenCodeSession(model="opencode/test-free").query("work", tmp_path, max_turns=1)
    assert not result.is_error
    assert "missing" in result.incomplete_reason
