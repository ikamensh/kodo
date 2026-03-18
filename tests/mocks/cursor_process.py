"""Mock replacement for subprocess.Popen used by CursorSession."""

from __future__ import annotations

import io
import json
from typing import Any


class MockCursorProcess:
    """Mimics subprocess.Popen for cursor-agent.

    Produces stream-json lines on stdout including a result message
    with configurable result_text, chat_id, and returncode.

    Extracts the prompt from the command for test inspection.
    """

    def __init__(
        self,
        cmd: list[str],
        *,
        result_text: str = "Task completed.",
        chat_id: str | None = "chat-abc-123",
        returncode: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        extra_messages: list[dict[str, Any]] | None = None,
        stderr_text: str = "",
        malformed_json: bool = False,
        empty_lines: bool = False,
        **kwargs: Any,
    ):
        self.cmd = cmd
        self.returncode = returncode
        self._build_stdout(
            result_text,
            chat_id,
            input_tokens,
            output_tokens,
            extra_messages or [],
            malformed_json,
            empty_lines,
        )
        self.stderr = io.StringIO(stderr_text)
        self.pid = 12345

        # Extract prompt: last positional argument (after all flags)
        self.prompt = cmd[-1] if cmd else ""

        # Extract resume chat ID if present
        self.resume_id = None
        if "--resume" in cmd:
            idx = cmd.index("--resume")
            if idx + 1 < len(cmd):
                self.resume_id = cmd[idx + 1]

    def _build_stdout(
        self,
        result_text: str,
        chat_id: str | None,
        input_tokens: int,
        output_tokens: int,
        extra_messages: list[dict[str, Any]],
        malformed_json: bool,
        empty_lines: bool,
    ) -> None:
        lines: list[str] = []

        if empty_lines:
            lines.append("")
            lines.append("   ")

        for msg in extra_messages:
            lines.append(json.dumps(msg))

        if malformed_json:
            lines.append("not valid json {]")

        # Token count message (if tokens provided)
        if input_tokens or output_tokens:
            lines.append(
                json.dumps(
                    {
                        "type": "usage",
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                    }
                )
            )

        result_msg = {
            "type": "result",
            "result": result_text,
            "duration_ms": 1234,
        }
        if chat_id:
            result_msg["chatId"] = chat_id

        lines.append(json.dumps(result_msg))

        if empty_lines:
            lines.append("")

        self.stdout = io.StringIO("\n".join(lines) + "\n")

    def wait(self, timeout=None) -> int:
        return self.returncode
