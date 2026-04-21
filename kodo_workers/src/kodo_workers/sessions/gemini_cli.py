"""Google Gemini CLI session using gemini subprocess."""

from __future__ import annotations

import json
from pathlib import Path

from kodo_workers import log
from kodo_workers.models import GEMINI_CLI_FLASH
from kodo_workers.sessions.base import QueryResult, SubprocessSession, classify_session_error


class GeminiCliSession(SubprocessSession):
    _session_label = "gemini-cli"

    def __init__(
        self,
        model: str = GEMINI_CLI_FLASH,
        system_prompt: str | None = None,
        resume_session: bool = False,
        timeout_s: int = 7200,
    ):
        super().__init__(model, system_prompt, timeout_s=timeout_s)
        self._resume_next = resume_session
        # Gemini CLI auto-saves sessions; --resume loads the last one.
        # We track whether to pass --resume on the next query.
        self._has_queried = False

    def clone(self) -> "GeminiCliSession":
        """Create a fresh session with the same config but no state."""
        return GeminiCliSession(
            model=self.model,
            system_prompt=self.system_prompt,
            timeout_s=self._timeout_s,
        )

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

        # Closure state for post-processing
        usage_raw = None
        stdout_text = ""
        has_json_error = False  # set True when JSON payload contains an error
        parsed_data: dict | None = None  # parsed JSON for tool-call fallback

        def _parse_stdout(proc):
            nonlocal usage_raw, stdout_text, has_json_error, parsed_data

            stdout_text = proc.stdout.read()
            result_text = ""
            input_tokens = 0
            output_tokens = 0

            if stdout_text.strip():
                self._stats.touch()
                try:
                    data = json.loads(stdout_text)
                    parsed_data = data
                    r = data.get("response", "")
                    result_text = str(r) if r is not None else ""
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
                        has_json_error = True
                        result_text = result_text or err.get("message", str(err))
                except json.JSONDecodeError:
                    # Fall back to raw text
                    result_text = stdout_text.strip()

            return result_text, input_tokens, output_tokens

        r = self._query_template(cmd, cwd=str(project_dir), parse_stdout=_parse_stdout)
        if isinstance(r, QueryResult):
            # Spawn failed — log and return early
            log.emit(
                "session_query_end",
                session="gemini-cli",
                elapsed_s=r.elapsed_s,
                is_error=True,
                error=r.text,
            )
            return r

        # Session-specific post-processing
        is_error = r.returncode != 0 or has_json_error
        result_text = r.result_text

        # When response is empty but tool calls happened (file writes,
        # shell commands), gemini-cli did work but the final model turn
        # was a tool call, not text.  Report a fallback so the
        # orchestrator knows something happened.
        if not result_text and not is_error and r.output_tokens > 0 and parsed_data:
            tool_stats = parsed_data.get("stats", {}).get("tools", {})
            total_calls = tool_stats.get("totalCalls", 0)
            if total_calls:
                result_text = (
                    f"[completed {total_calls} tool call(s), no text response]"
                )
            else:
                result_text = "[completed, no text response]"

        if is_error and not result_text:
            hint = classify_session_error(
                r.returncode,
                r.stderr_text,
                stdout_text,
                "gemini",
                did_timeout=self._did_timeout,
                timeout_s=self._timeout_s,
            )
            result_text = hint or r.stderr_text

        self._has_queried = True
        self._resume_next = True  # subsequent queries resume the session

        conv_file = None
        if stdout_text:
            conv_file = log.save_conversation(
                f"gemini_{id(self) % 10000:04d}",
                self._stats.queries,
                [{"raw_stdout": stdout_text}],
            )

        log.emit(
            "session_query_end",
            session="gemini-cli",
            model=self.model,
            elapsed_s=r.elapsed_s,
            is_error=is_error,
            session_id=self.session_id,
            returncode=r.returncode,
            response_text=result_text,
            input_tokens=r.input_tokens,
            output_tokens=r.output_tokens,
            conversation_log=conv_file,
        )

        text_out = result_text or ""
        return QueryResult(
            text=text_out,
            elapsed_s=r.elapsed_s,
            is_error=is_error,
            input_tokens=r.input_tokens or None,
            output_tokens=r.output_tokens or None,
            usage_raw=usage_raw,
        )
