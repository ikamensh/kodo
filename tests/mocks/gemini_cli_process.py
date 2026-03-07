"""Mock replacement for subprocess.Popen used by GeminiCliSession."""

from __future__ import annotations

import io
import json
from typing import Any


class MockGeminiCliProcess:
    """Mimics subprocess.Popen for gemini CLI.

    Produces a single JSON blob on stdout matching `gemini -p ... --output-format json`.

    Extracts the prompt from the command for test inspection.
    """

    def __init__(
        self,
        cmd: list[str],
        *,
        result_text: str = "Task completed.",
        returncode: int = 0,
        input_tokens: int = 100,
        output_tokens: int = 50,
        error: dict[str, Any] | None = None,
        stderr_text: str = "",
        json_data: dict[str, Any] | None = None,
        stdout_text: str | None = None,
        **kwargs: Any,
    ):
        self.cmd = cmd
        self.returncode = returncode

        # Allow raw stdout override or custom JSON data
        if stdout_text is not None:
            self.stdout = io.StringIO(stdout_text)
        elif json_data is not None:
            self.stdout = io.StringIO(json.dumps(json_data) + "\n")
        else:
            self._build_stdout(result_text, input_tokens, output_tokens, error)

        self.stderr = io.StringIO(stderr_text)
        self.pid = 12345

        # Extract prompt: value after -p flag
        self.prompt = None
        if "-p" in cmd:
            idx = cmd.index("-p")
            if idx + 1 < len(cmd):
                self.prompt = cmd[idx + 1]

        # Check for resume flag
        self.has_resume = "--resume" in cmd

    def _build_stdout(
        self,
        result_text: str,
        input_tokens: int,
        output_tokens: int,
        error: dict[str, Any] | None,
    ) -> None:
        data: dict[str, Any] = {
            "response": result_text,
            "stats": {
                "models": {
                    "gemini-2.5-flash": {
                        "api": {
                            "totalRequests": 1,
                            "totalErrors": 0,
                            "totalLatencyMs": 1234,
                        },
                        "tokens": {
                            "prompt": input_tokens,
                            "candidates": output_tokens,
                            "total": input_tokens + output_tokens,
                            "cached": 0,
                            "thoughts": 0,
                            "tool": 0,
                        },
                    }
                },
                "tools": {
                    "totalCalls": 0,
                    "totalSuccess": 0,
                    "totalFail": 0,
                    "totalDurationMs": 0,
                },
                "files": {
                    "totalLinesAdded": 0,
                    "totalLinesRemoved": 0,
                },
            },
        }
        if error:
            data["error"] = error
        self.stdout = io.StringIO(json.dumps(data) + "\n")

    def wait(self, timeout=None) -> int:
        return self.returncode
