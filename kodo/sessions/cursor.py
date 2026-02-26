"""Cursor session using cursor-agent CLI subprocess."""

from __future__ import annotations

import json
import time
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

        t0 = time.monotonic()
        result_text = ""
        raw_messages: list[dict] = []

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

                if msg.get("type") == "result":
                    r = msg.get("result", "")
                    result_text = str(r) if r is not None else ""
                # Capture chat ID from any message that reports it
                if "chatId" in msg:
                    self._chat_id = msg["chatId"]
                elif "chat_id" in msg:
                    self._chat_id = msg["chat_id"]
                elif "session_id" in msg:
                    self._chat_id = msg["session_id"]
        finally:
            stderr_text = self._wait(proc, stderr_chunks, stderr_thread)
        elapsed = time.monotonic() - t0

        is_error = proc.returncode != 0
        if not is_error:
            stderr_text = ""
        elif not result_text:
            hint = classify_session_error(
                proc.returncode,
                stderr_text,
                backend="cursor",
                did_timeout=self._did_timeout,
                timeout_s=self._timeout_s,
            )
            if hint:
                result_text = hint

        self._stats.queries += 1

        log.emit(
            "session_query_end",
            session="cursor",
            model=self.model,
            elapsed_s=elapsed,
            is_error=is_error,
            chat_id=self._chat_id,
            returncode=proc.returncode,
            response_text=result_text or stderr_text,
            raw_messages=raw_messages,
        )

        text_out = result_text if result_text else stderr_text
        return QueryResult(
            text=text_out,
            elapsed_s=elapsed,
            is_error=is_error,
        )
