"""Mock replacement for subprocess.Popen used by CodexSession."""

from __future__ import annotations

import io
import json
from typing import Any


class MockCodexProcess:
    """Mimics subprocess.Popen for codex exec.

    Produces JSONL events on stdout matching the Codex CLI --json format:
    thread.started, item.completed (assistant message), turn.completed (usage).

    Use error_message to simulate bad-model or auth errors (emits error event,
    clears result_text so CodexSession surfaces the formatted error).

    Extracts the prompt and resume ID from the command for test inspection.
    """

    def __init__(
        self,
        cmd: list[str],
        *,
        result_text: str = "Task completed.",
        session_id: str | None = "thread-abc-123",
        returncode: int = 0,
        input_tokens: int = 100,
        output_tokens: int = 50,
        extra_messages: list[dict[str, Any]] | None = None,
        stderr_text: str = "",
        error_message: str | None = None,
        background_error: str | None = None,
        malformed_json: bool = False,
        empty_lines: bool = False,
        nested_format: bool = False,
        legacy_tokens: bool = False,
        legacy_item: bool = False,
        **kwargs: Any,
    ):
        self.cmd = cmd
        self.returncode = returncode
        msgs = list(extra_messages or [])
        if error_message:
            msgs.append({"type": "error", "message": error_message})
            result_text = ""
        if background_error:
            msgs.append({"type": "background_event", "message": background_error})
            result_text = ""
        self._build_stdout(
            result_text,
            session_id,
            input_tokens,
            output_tokens,
            msgs,
            malformed_json,
            empty_lines,
            nested_format,
            legacy_tokens,
            legacy_item,
        )
        self.stderr = io.StringIO(stderr_text)
        self.pid = 12345

        # Extract prompt and resume info from command for test inspection.
        # "codex exec <prompt> ..." or "codex exec resume <session_id> ..."
        self.prompt = None
        self.resume_id = None
        if len(cmd) >= 3 and cmd[1] == "exec":
            if cmd[2] == "resume" and len(cmd) >= 4:
                self.resume_id = cmd[3]
            else:
                self.prompt = cmd[2]

    def _build_stdout(
        self,
        result_text: str,
        session_id: str | None,
        input_tokens: int,
        output_tokens: int,
        extra_messages: list[dict[str, Any]],
        malformed_json: bool,
        empty_lines: bool,
        nested_format: bool,
        legacy_tokens: bool,
        legacy_item: bool,
    ) -> None:
        lines: list[str] = []

        if empty_lines:
            lines.append("")
            lines.append("   ")

        # thread.started — provides session ID
        if session_id:
            lines.append(
                json.dumps(
                    {
                        "type": "thread.started",
                        "thread_id": session_id,
                    }
                )
            )

        # Extra messages (e.g. tool calls, intermediate items)
        for msg in extra_messages:
            lines.append(json.dumps(msg))

        if malformed_json:
            lines.append("not valid json {]")

        # Result message format
        if nested_format:
            # New nested format: {"msg": {"type": "agent_message", ...}}
            lines.append(json.dumps({
                "id": "0",
                "msg": {
                    "type": "agent_message",
                    "message": result_text,
                }
            }))
            if input_tokens or output_tokens:
                lines.append(json.dumps({
                    "id": "1",
                    "msg": {
                        "type": "token_count",
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                    }
                }))
        elif legacy_item:
            # Legacy format: item.completed
            lines.append(
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": result_text}],
                        },
                    }
                )
            )
        else:
            # Current format: agent_message (top-level)
            lines.append(json.dumps({
                "type": "agent_message",
                "message": result_text,
            }))

        # Token counts
        if legacy_tokens:
            # Legacy: turn.completed with usage
            lines.append(
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                        },
                    }
                )
            )
        elif not nested_format and (input_tokens or output_tokens):
            # Current: token_count (top-level)
            lines.append(json.dumps({
                "type": "token_count",
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            }))

        if empty_lines:
            lines.append("")

        self.stdout = io.StringIO("\n".join(lines) + "\n")

    def wait(self, timeout=None) -> int:
        return self.returncode
