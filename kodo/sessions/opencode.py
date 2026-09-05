"""OpenCode session using opencode CLI subprocess."""

from __future__ import annotations

from contextlib import nullcontext
import json
import os
from pathlib import Path
import signal
import threading

from kodo import log
from kodo.models import OPENCODE_DEFAULT
from kodo.opencode_config import isolated_opencode_config
from kodo.sessions.base import QueryResult, SubprocessSession, classify_session_error


class OpenCodeSession(SubprocessSession):
    _session_label = "opencode"

    def __init__(
        self,
        model: str = OPENCODE_DEFAULT,
        system_prompt: str | None = None,
        resume_session_id: str | None = None,
        timeout_s: int = 7200,
        isolated_model: bool = False,
    ):
        super().__init__(model, system_prompt, timeout_s=timeout_s)
        self._session_id: str | None = resume_session_id
        self._has_queried = False
        self.isolated_model = isolated_model
        self._cancelled = threading.Event()
        self._spawn_lock = threading.Lock()

    def _spawn(self, cmd, *, cwd=None, env=None):
        with self._spawn_lock:
            if self._cancelled.is_set():
                raise RuntimeError("OpenCode session cancelled")
            return super()._spawn(cmd, cwd=cwd, env=env, start_new_session=True)

    def terminate(self) -> None:
        with self._spawn_lock:
            self._cancelled.set()
            if self._process is not None:
                try:
                    os.killpg(self._process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            super().terminate()

    def clone(self) -> "OpenCodeSession":
        """Create a fresh session with the same config but no state."""
        return OpenCodeSession(
            model=self.model,
            system_prompt=self.system_prompt,
            timeout_s=self._timeout_s,
            isolated_model=self.isolated_model,
        )

    @property
    def cost_bucket(self) -> str:
        return "opencode_api"

    @property
    def session_id(self) -> str | None:
        return self._session_id

    def reset(self) -> None:
        log.emit(
            "session_reset",
            session="opencode",
            model=self.model,
            session_id=self._session_id,
            queries_before=self._stats.queries,
        )
        self._session_id = None
        self._has_queried = False
        self._cancelled.clear()
        super().reset()

    def query(self, prompt: str, project_dir: Path, *, max_turns: int) -> QueryResult:
        prompt = self._prepend_system_prompt(prompt)
        project_dir = project_dir.resolve()

        cmd = [
            "opencode",
            "run",
            "--format",
            "json",
            # OpenCode's project selection can differ from subprocess cwd (e.g. inherited PWD).
            "--dir",
            str(project_dir),
        ]

        if self.model != "default":
            cmd.extend(["--model", self.model])

        if self.isolated_model:
            cmd.extend(["--pure", "--agent", "build", "--title", "Kodo worker"])

        if self._session_id:
            cmd.extend(["--session", self._session_id])
        elif self._has_queried:
            cmd.append("--continue")

        cmd.append(prompt)

        log.emit(
            "session_query_start",
            session="opencode",
            model=self.model,
            prompt=prompt,
            session_id=self._session_id,
            project_dir=str(project_dir),
        )

        raw_messages: list[dict] = []
        provider_error = ""

        def _parse_stdout(proc):
            nonlocal provider_error
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

                part = msg.get("part", {})
                msg_type = msg.get("type", "")

                if msg_type == "error":
                    error = msg.get("error", {})
                    data = error.get("data", error)
                    provider_error = str(
                        data.get("message") or error.get("message") or error.get("name")
                        or "OpenCode provider failed"
                    )
                    if data.get("statusCode"):
                        provider_error = f"HTTP {data['statusCode']}: {provider_error}"

                # Extract result text from text events (part.text)
                if msg_type == "text":
                    t = part.get("text", "")
                    if t:
                        result_text = str(t)

                # Extract token counts from step_finish events
                if msg_type == "step_finish":
                    tokens = part.get("tokens", {})
                    input_tokens += tokens.get("input", 0)
                    output_tokens += tokens.get("output", 0) + tokens.get("reasoning", 0)

                # Capture session ID (top-level field)
                if "sessionID" in msg:
                    self._session_id = msg["sessionID"]

            return result_text, input_tokens, output_tokens

        environment = (
            isolated_opencode_config(self.model, project_dir, cancelled=self._cancelled)
            if self.isolated_model else nullcontext(None)
        )
        with environment as env:
            r = self._query_template(cmd, cwd=str(project_dir), env=env, parse_stdout=_parse_stdout)
        if isinstance(r, QueryResult):
            log.emit(
                "session_query_end",
                session="opencode",
                elapsed_s=r.elapsed_s,
                is_error=True,
                error=r.text,
            )
            return r

        is_error = bool(provider_error) or r.returncode != 0
        result_text = provider_error or r.result_text

        if not is_error and not result_text:
            # Try to reconstruct from raw messages
            texts = [
                m.get("text", "")
                for m in raw_messages
                if m.get("type") in ("raw_text", "text", "assistant")
            ]
            result_text = "\n".join(t for t in texts if t).strip()
            if not result_text:
                result_text = "[completed, no text response]"

        if is_error and not result_text:
            hint = classify_session_error(
                r.returncode,
                r.stderr_text,
                backend="opencode",
                did_timeout=self._did_timeout,
                timeout_s=self._timeout_s,
            )
            result_text = hint or r.stderr_text

        self._has_queried = True

        conv_file = None
        if raw_messages:
            conv_file = log.save_conversation(
                f"opencode_{id(self) % 10000:04d}",
                self._stats.queries,
                raw_messages,
            )

        log.emit(
            "session_query_end",
            session="opencode",
            model=self.model,
            elapsed_s=r.elapsed_s,
            is_error=is_error,
            session_id=self._session_id,
            returncode=r.returncode,
            input_tokens=r.input_tokens or None,
            output_tokens=r.output_tokens or None,
            response_text=result_text,
            conversation_log=conv_file,
        )

        text_out = result_text or ""
        return QueryResult(
            text=text_out,
            elapsed_s=r.elapsed_s,
            is_error=is_error,
            input_tokens=r.input_tokens or None,
            output_tokens=r.output_tokens or None,
        )
