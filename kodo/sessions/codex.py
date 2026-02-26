"""OpenAI Codex session using codex CLI subprocess."""

from __future__ import annotations

import json
import time
from pathlib import Path

from kodo import log
from kodo.models import CODEX_DEFAULT, CODEX_WORKER
from kodo.sessions.base import QueryResult, SubprocessSession, classify_session_error


class CodexSession(SubprocessSession):
    _session_label = "codex"

    def __init__(
        self,
        model: str = CODEX_WORKER,
        system_prompt: str | None = None,
        resume_session_id: str | None = None,
        sandbox: str = "workspace-write",
        timeout_s: int = 7200,
    ):
        super().__init__(model, system_prompt, timeout_s=timeout_s)
        self._session_id: str | None = resume_session_id
        self._sandbox = sandbox

    def clone(self) -> "CodexSession":
        """Create a fresh session with the same config but no state."""
        return CodexSession(
            model=self.model,
            system_prompt=self.system_prompt,
            sandbox=self._sandbox,
            timeout_s=self._timeout_s,
        )

    @property
    def cost_bucket(self) -> str:
        return "codex_subscription"

    @property
    def session_id(self) -> str | None:
        return self._session_id

    def reset(self) -> None:
        log.emit(
            "session_reset",
            session="codex",
            model=self.model,
            session_id=self._session_id,
            queries_before=self._stats.queries,
        )
        self._session_id = None
        super().reset()

    def query(self, prompt: str, project_dir: Path, *, max_turns: int) -> QueryResult:
        prompt = self._prepend_system_prompt(prompt)

        cmd = [
            "codex",
            "exec",
            prompt,
            "--full-auto",
            "--json",
            "--cd",
            str(project_dir),
            "--sandbox",
            self._sandbox,
            "-m",
            self.model,
        ]

        log.emit(
            "session_query_start",
            session="codex",
            model=self.model,
            prompt=prompt,
            session_id=self._session_id,
            project_dir=str(project_dir),
        )

        t0 = time.monotonic()
        result_text = ""
        input_tokens = 0
        output_tokens = 0
        raw_messages: list[dict] = []
        error_messages: list[str] = []

        proc, stderr_chunks, stderr_thread = self._spawn(cmd)

        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue

                raw_messages.append(msg)

                # Codex emits two shapes:
                #   top-level: {"type": "...", ...}
                #   nested:    {"id": "0", "msg": {"type": "...", ...}}
                raw_inner = msg.get("msg", {}) if "msg" in msg else {}
                inner = raw_inner if isinstance(raw_inner, dict) else {}
                event_type = msg.get("type", "") or inner.get("type", "")

                # Capture session/thread ID
                if event_type == "thread.started":
                    tid = msg.get("thread_id") or msg.get("session_id")
                    if tid:
                        self._session_id = tid

                # Agent text response (current codex format)
                elif event_type == "agent_message":
                    src = inner if inner else msg
                    text = src.get("message", "")
                    if text:
                        result_text = text

                # Token counts (current codex format)
                elif event_type == "token_count":
                    src = inner if inner else msg
                    input_tokens += src.get("input_tokens", 0)
                    output_tokens += src.get("output_tokens", 0)

                # Legacy: accumulate token usage from completed turns
                elif event_type == "turn.completed":
                    usage = msg.get("usage", {})
                    input_tokens += usage.get("input_tokens", 0)
                    output_tokens += usage.get("output_tokens", 0)

                # Legacy: capture assistant message text
                elif event_type == "item.completed":
                    item = msg.get("item", {})
                    if item.get("role") == "assistant":
                        for content in item.get("content", []):
                            if content.get("type") == "text":
                                result_text = content.get("text", "")

                # Capture errors (top-level or nested)
                elif event_type == "error":
                    src = inner if inner else msg
                    error_msg = src.get("message", src.get("error", ""))
                    if error_msg:
                        error_messages.append(error_msg)

                # Capture background_event errors (retries, API failures)
                elif event_type == "background_event":
                    src = inner if inner else msg
                    bg_msg = src.get("message", "")
                    if "error" in bg_msg.lower() or "status 4" in bg_msg:
                        error_messages.append(bg_msg)
        finally:
            stderr_text = self._wait(proc, stderr_chunks, stderr_thread)
        elapsed = time.monotonic() - t0

        is_error = proc.returncode != 0

        # Codex may exit 0 even when all API calls failed — detect this
        if not is_error and not result_text and error_messages:
            is_error = True
            # Surface an actionable message for model/auth issues
            last_err = error_messages[-1]
            if "not supported" in last_err or "does not exist" in last_err:
                result_text = (
                    f"Codex error: {last_err}\n"
                    f"Check your Codex login ('codex login status') and model "
                    f"('{self.model}'). ChatGPT accounts support {CODEX_WORKER} "
                    f"and {CODEX_DEFAULT}. API accounts may use {CODEX_DEFAULT}. "
                    f"Run 'codex login' to switch auth method."
                )
            else:
                result_text = last_err

        # Classify the error for better diagnostics
        if is_error and not result_text:
            hint = classify_session_error(
                proc.returncode,
                stderr_text,
                "\n".join(error_messages),
                "codex",
                did_timeout=self._did_timeout,
                timeout_s=self._timeout_s,
            )
            if hint:
                result_text = hint

        self._stats.queries += 1
        self._stats.total_input_tokens += input_tokens
        self._stats.total_output_tokens += output_tokens

        log.emit(
            "session_query_end",
            session="codex",
            model=self.model,
            elapsed_s=elapsed,
            is_error=is_error,
            session_id=self._session_id,
            returncode=proc.returncode,
            response_text=result_text or stderr_text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            raw_messages=raw_messages,
        )

        text_out = result_text if result_text else stderr_text
        return QueryResult(
            text=text_out,
            elapsed_s=elapsed,
            is_error=is_error,
            input_tokens=input_tokens or None,
            output_tokens=output_tokens or None,
        )
