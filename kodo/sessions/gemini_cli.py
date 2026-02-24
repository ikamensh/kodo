"""Google Gemini CLI session using gemini subprocess."""

from __future__ import annotations

import json
import time
from pathlib import Path

from kodo import log
from kodo.sessions.base import QueryResult, SubprocessSession, classify_session_error


class GeminiCliSession(SubprocessSession):
    _session_label = "gemini-cli"

    def __init__(
        self,
        model: str = "gemini-2.5-flash",
        system_prompt: str | None = None,
        resume_session: bool = False,
    ):
        super().__init__(model, system_prompt)
        self._resume_next = resume_session
        # Gemini CLI auto-saves sessions; --resume loads the last one.
        # We track whether to pass --resume on the next query.
        self._has_queried = False

    def clone(self) -> "GeminiCliSession":
        """Create a fresh session with the same config but no state."""
        return GeminiCliSession(model=self.model, system_prompt=self.system_prompt)

    @property
    def cost_bucket(self) -> str:
        return "gemini_api"

    @property
    def session_id(self) -> str | None:
        # Gemini CLI uses --resume for last session; no explicit session ID.
        return "last" if self._has_queried else None

    def reset(self) -> None:
        log.emit(
            "session_reset",
            session="gemini-cli",
            model=self.model,
            queries_before=self._stats.queries,
        )
        self._resume_next = False
        self._has_queried = False
        super().reset()

    def query(self, prompt: str, project_dir: Path, *, max_turns: int) -> QueryResult:
        prompt = self._prepend_system_prompt(prompt)

        cmd = [
            "gemini",
            "-p",
            prompt,
            "-y",  # auto-approve all tool calls
            "--output-format",
            "json",
            "-m",
            self.model,
        ]

        if self._resume_next:
            cmd.append("--resume")

        log.emit(
            "session_query_start",
            session="gemini-cli",
            model=self.model,
            prompt=prompt,
            resume=self._resume_next,
            project_dir=str(project_dir),
        )

        t0 = time.monotonic()

        proc, stderr_chunks, stderr_thread = self._spawn(cmd, cwd=str(project_dir))

        stdout_text = proc.stdout.read()
        stderr_text = self._wait(proc, stderr_chunks, stderr_thread)
        elapsed = time.monotonic() - t0

        is_error = proc.returncode != 0

        # Parse the JSON response
        result_text = ""
        input_tokens = 0
        output_tokens = 0
        usage_raw = None

        if stdout_text.strip():
            try:
                data = json.loads(stdout_text)
                result_text = data.get("response", "")
                # Extract token stats from stats.models
                stats_models = data.get("stats", {}).get("models", {})
                for model_stats in stats_models.values():
                    tokens = model_stats.get("tokens", {})
                    input_tokens += tokens.get("prompt", 0)
                    output_tokens += tokens.get("candidates", 0)
                usage_raw = data.get("stats")

                # Check for error in response
                err = data.get("error")
                if err:
                    is_error = True
                    result_text = result_text or err.get("message", str(err))

                # When response is empty but tool calls happened (file writes,
                # shell commands), gemini-cli did work but the final model turn
                # was a tool call, not text.  Report a fallback so the
                # orchestrator knows something happened.
                if not result_text and not is_error and output_tokens > 0:
                    tool_stats = data.get("stats", {}).get("tools", {})
                    total_calls = tool_stats.get("totalCalls", 0)
                    if total_calls:
                        result_text = (
                            f"[completed {total_calls} tool call(s), no text response]"
                        )
                    else:
                        result_text = "[completed, no text response]"
            except json.JSONDecodeError:
                # Fall back to raw text
                result_text = stdout_text.strip()

        if is_error and not result_text:
            hint = classify_session_error(
                proc.returncode, stderr_text, stdout_text, "gemini"
            )
            result_text = hint or stderr_text

        self._stats.queries += 1
        self._stats.total_input_tokens += input_tokens
        self._stats.total_output_tokens += output_tokens
        self._has_queried = True
        self._resume_next = True  # subsequent queries resume the session

        log.emit(
            "session_query_end",
            session="gemini-cli",
            model=self.model,
            elapsed_s=elapsed,
            is_error=is_error,
            session_id=self.session_id,
            returncode=proc.returncode,
            response_text=result_text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

        return QueryResult(
            text=result_text,
            elapsed_s=elapsed,
            is_error=is_error,
            input_tokens=input_tokens or None,
            output_tokens=output_tokens or None,
            usage_raw=usage_raw,
        )
