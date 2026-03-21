"""Cursor session using cursor-agent CLI subprocess."""

from __future__ import annotations

import json
from pathlib import Path

from kodo import log
from kodo.models import CURSOR_COMPOSER
from kodo.sessions.base import QueryResult, SubprocessSession, classify_session_error


class CursorSession(SubprocessSession):
    _session_label = "cursor"

    def __init__(
        self,
        model: str = CURSOR_COMPOSER,
        system_prompt: str | None = None,
        resume_chat_id: str | None = None,
        timeout_s: int = 7200,
    ):
        super().__init__(model, system_prompt, timeout_s=timeout_s)
        self._chat_id: str | None = resume_chat_id

    def clone(self) -> "CursorSession":
        """Create a fresh session with the same config but no state."""
        return CursorSession(
            model=self.model,
            system_prompt=self.system_prompt,
            timeout_s=self._timeout_s,
        )

    @property
    def cost_bucket(self) -> str:
        return "cursor_subscription"

    @property
    def session_id(self) -> str | None:
        return self._chat_id

    def reset(self) -> None:
        log.emit(
            "session_reset",
            session="cursor",
            model=self.model,
            chat_id=self._chat_id,
            queries_before=self._stats.queries,
        )
        self._chat_id = None
        super().reset()

    def query(self, prompt: str, project_dir: Path, *, max_turns: int) -> QueryResult:
        prompt = self._prepend_system_prompt(prompt)

        cmd = [
            "cursor-agent",
            "-p",
            "-f",
            "--output-format",
            "stream-json",
            "--model",
            self.model,
            "--workspace",
            str(project_dir),
        ]

        if self._chat_id:
            cmd.extend(["--resume", self._chat_id])

        cmd.append(prompt)

        log.emit(
            "session_query_start",
            session="cursor",
            model=self.model,
            prompt=prompt,
            chat_id=self._chat_id,
            project_dir=str(project_dir),
        )

        raw_messages: list[dict] = []

        def _parse_stdout(proc):
            result_text = ""
            input_tokens = 0
            output_tokens = 0

            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    raw_messages.append({"type": "raw_text", "text": line})
                    self._stats.touch()
                    continue

                raw_messages.append(msg)
                self._stats.touch()

                if msg.get("type") == "result":
                    r = msg.get("result", "")
                    result_text = str(r) if r is not None else ""
                # Extract token counts from any message that reports them.
                # cursor-agent may emit these in "token_count", "usage", or
                # top-level fields depending on version.
                _usage = msg.get("usage") or msg
                if "input_tokens" in _usage:
                    input_tokens += _usage.get("input_tokens", 0)
                    output_tokens += _usage.get("output_tokens", 0)
                elif msg.get("type") == "token_count":
                    inner = msg.get("data") or msg
                    input_tokens += inner.get("input_tokens", 0)
                    output_tokens += inner.get("output_tokens", 0)
                # Capture chat ID from any message that reports it
                if "chatId" in msg:
                    self._chat_id = msg["chatId"]
                elif "chat_id" in msg:
                    self._chat_id = msg["chat_id"]
                elif "session_id" in msg:
                    self._chat_id = msg["session_id"]

            return result_text, input_tokens, output_tokens

        r = self._query_template(cmd, parse_stdout=_parse_stdout)
        if isinstance(r, QueryResult):
            # Spawn failed — log and return early
            log.emit(
                "session_query_end",
                session="cursor",
                elapsed_s=r.elapsed_s,
                is_error=True,
                error=r.text,
            )
            return r

        # Session-specific post-processing
        is_error = r.returncode != 0
        result_text = r.result_text
        stderr_text = r.stderr_text

        if not is_error:
            stderr_text = ""
        elif not result_text:
            hint = classify_session_error(
                r.returncode,
                r.stderr_text,
                backend="cursor",
                did_timeout=self._did_timeout,
                timeout_s=self._timeout_s,
            )
            if hint:
                result_text = hint

        # Save full conversation to gzip file; keep only summary in log.jsonl
        conv_file = None
        if raw_messages:
            conv_file = log.save_conversation(
                f"cursor_{id(self) % 10000:04d}", self._stats.queries, raw_messages
            )

        log.emit(
            "session_query_end",
            session="cursor",
            model=self.model,
            elapsed_s=r.elapsed_s,
            is_error=is_error,
            chat_id=self._chat_id,
            returncode=r.returncode,
            input_tokens=r.input_tokens or None,
            output_tokens=r.output_tokens or None,
            response_text=result_text or stderr_text,
            conversation_log=conv_file,
        )

        text_out = result_text if result_text else stderr_text
        return QueryResult(
            text=text_out,
            elapsed_s=r.elapsed_s,
            is_error=is_error,
            input_tokens=r.input_tokens or None,
            output_tokens=r.output_tokens or None,
        )
