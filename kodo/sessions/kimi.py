"""Native Kimi Code CLI sessions over its ACP JSON-RPC interface."""

from __future__ import annotations

import json
import os
import queue
import signal
import threading
import subprocess
import tempfile
import time
from pathlib import Path

from kodo import __version__, log
from kodo.models import KIMI_DEFAULT
from kodo.sessions.base import QueryResult, SubprocessSession


class KimiSession(SubprocessSession):
    """Use native CLI auth/config and persist conversations by their ACP session ID."""

    _session_label = "kimi"

    def __init__(
        self,
        model: str = KIMI_DEFAULT,
        system_prompt: str | None = None,
        resume_session_id: str | None = None,
        session_timeout_s: int | None = None,
        mcp_servers: list[dict] | None = None,
    ):
        super().__init__(
            model,
            system_prompt,
            timeout_s=7200 if session_timeout_s is None else session_timeout_s,
        )
        self._session_id = resume_session_id
        self._project_dir: Path | None = None
        self._mcp_servers = mcp_servers or []
        self._process_lock = threading.Lock()

    def __enter__(self) -> KimiSession:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def cost_bucket(self) -> str:
        return "kimi_cli"

    @property
    def session_id(self) -> str | None:
        return self._session_id

    def clone(self) -> KimiSession:
        return KimiSession(
            model=self.model,
            system_prompt=self.system_prompt,
            session_timeout_s=self._timeout_s,
            mcp_servers=self._mcp_servers,
        )

    def close(self) -> None:
        self.terminate()

    def terminate(self) -> None:
        with self._process_lock:
            proc = self._process
            if proc is None:
                return
            try:
                if os.name == "nt":
                    if proc.poll() is None:
                        try:
                            subprocess.run(
                                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE,
                                text=True,
                                timeout=5,
                                check=True,
                            )
                        except subprocess.CalledProcessError:
                            if proc.poll() is None:
                                raise
                else:
                    # Tools may inherit ACP pipes; kill our group so EOF can arrive.
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            finally:
                super().terminate()

    def reset(self) -> None:
        self.terminate()
        self._session_id = None
        self._project_dir = None
        super().reset()

    def query(self, prompt: str, project_dir: Path, *, max_turns: int) -> QueryResult:
        if self._project_dir is not None and self._project_dir != project_dir:
            self.reset()
        self._project_dir = project_dir
        started = time.monotonic()
        deadline = started + self._timeout_s
        messages: list[dict] = []
        text_parts: list[str] = []
        tool_calls = 0
        limited = False
        proc = None
        incoming: queue.Queue[bytes | None] = queue.Queue(maxsize=128)
        stop_reader = threading.Event()
        reader = None
        request_id = 0
        error = ""
        incomplete_reason = ""

        # File-backed stderr cannot block the CLI while stdout carries ACP messages.
        with tempfile.TemporaryFile() as stderr:

            def send(message: dict) -> None:
                assert proc is not None and proc.stdin is not None
                proc.stdin.write(
                    (json.dumps({"jsonrpc": "2.0", **message}) + "\n").encode()
                )
                proc.stdin.flush()

            def read_stdout() -> None:
                assert proc is not None and proc.stdout is not None
                # The reader owns this handle: closing a live BufferedReader
                # elsewhere can block on its lock if a descendant keeps stdout.
                with proc.stdout:
                    while not stop_reader.is_set():
                        line = proc.stdout.readline()
                        while not stop_reader.is_set():
                            try:
                                incoming.put(line or None, timeout=0.1)
                                break
                            except queue.Full:
                                continue
                        if not line:
                            return

            def read_message() -> dict:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"Kimi query timed out after {self._timeout_s}s")
                try:
                    line = incoming.get(timeout=remaining)
                except queue.Empty:
                    raise TimeoutError(
                        f"Kimi query timed out after {self._timeout_s}s"
                    ) from None
                if line is None:
                    stderr.seek(0, os.SEEK_END)
                    stderr.seek(max(0, stderr.tell() - 2000))
                    detail = stderr.read().decode("utf-8", errors="replace").strip()
                    raise RuntimeError(
                        detail or "Kimi CLI exited before completing its response"
                    )
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise ValueError("Kimi CLI returned a non-object ACP message")
                messages.append(message)
                self._stats.touch()
                return message

            def request(method: str, params: dict, *, collect: bool = False) -> dict:
                nonlocal request_id, tool_calls, limited
                request_id += 1
                current_id = request_id
                send({"id": current_id, "method": method, "params": params})
                while True:
                    message = read_message()
                    if message.get("id") == current_id and "method" not in message:
                        if rpc_error := message.get("error"):
                            hint = (
                                " Run `kimi login` to authenticate the native CLI."
                                if rpc_error.get("code") == -32000
                                else ""
                            )
                            raise RuntimeError(
                                f"Kimi: {rpc_error.get('message', rpc_error)}.{hint}"
                            )
                        return message.get("result", {})
                    if message.get("method") == "session/request_permission":
                        options = message["params"]["options"]
                        option = next(
                            (o for o in options if o["kind"] == "allow_once"), None
                        )
                        if option is None:
                            raise RuntimeError(
                                "Kimi requested input without an allow-once option"
                            )
                        send(
                            {
                                "id": message["id"],
                                "result": {
                                    "outcome": {
                                        "outcome": "selected",
                                        "optionId": option["optionId"],
                                    }
                                },
                            }
                        )
                    elif message.get("method") == "session/update" and collect:
                        update = message["params"]["update"]
                        if update["sessionUpdate"] == "agent_message_chunk":
                            content = update["content"]
                            if content["type"] == "text":
                                text_parts.append(content["text"])
                        elif update["sessionUpdate"] == "tool_call":
                            tool_calls += 1
                            if tool_calls >= max_turns and not limited:
                                limited = True
                                send(
                                    {
                                        "method": "session/cancel",
                                        "params": {"sessionId": self._session_id},
                                    }
                                )
                    elif "method" in message and "id" in message:
                        send(
                            {
                                "id": message["id"],
                                "error": {
                                    "code": -32601,
                                    "message": "Method not supported by Kodo",
                                },
                            }
                        )

            try:
                with self._process_lock:
                    proc = subprocess.Popen(
                        ["kimi", "acp"],
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=stderr,
                        cwd=str(project_dir),
                        start_new_session=os.name != "nt",
                    )
                    self._process = proc
                reader = threading.Thread(
                    target=read_stdout, daemon=True, name="kodo-kimi-stdout"
                )
                reader.start()
                initialized = request(
                    "initialize",
                    {
                        "protocolVersion": 1,
                        "clientCapabilities": {},
                        "clientInfo": {"name": "kodo", "version": __version__},
                    },
                )
                session_params = {
                    "cwd": str(project_dir.resolve()),
                    "mcpServers": self._mcp_servers,
                }
                if self._session_id:
                    if not initialized.get("agentCapabilities", {}).get("loadSession"):
                        raise RuntimeError(
                            "Installed Kimi CLI does not support resuming ACP sessions"
                        )
                    session_params["sessionId"] = self._session_id
                    request("session/load", session_params)
                else:
                    self._session_id = request("session/new", session_params)[
                        "sessionId"
                    ]
                if self.model != KIMI_DEFAULT:
                    request(
                        "session/set_model",
                        {"sessionId": self._session_id, "modelId": self.model},
                    )
                full_prompt = self._prepend_system_prompt(prompt)
                log.emit(
                    "session_query_start",
                    session="kimi",
                    model=self.model,
                    prompt=full_prompt,
                    session_id=self._session_id,
                    project_dir=str(project_dir),
                )
                self._stats.queries += 1
                response = request(
                    "session/prompt",
                    {
                        "sessionId": self._session_id,
                        "prompt": [{"type": "text", "text": full_prompt}],
                    },
                    collect=True,
                )
                stop_reason = response.get("stopReason")
                if limited:
                    incomplete_reason = "max_turns"
                    error = f"Kimi reached the limit of {max_turns} tool calls"
                elif stop_reason != "end_turn":
                    incomplete_reason = str(stop_reason or "incomplete_response")
                    error = (
                        f"Kimi stopped before completing the query: {incomplete_reason}"
                    )
            except TimeoutError as exc:
                incomplete_reason = "timeout"
                error = str(exc)
            except (OSError, ValueError, RuntimeError) as exc:
                error = str(exc)
            finally:
                try:
                    self.terminate()
                finally:
                    stop_reader.set()
                    if reader is not None:
                        reader.join(timeout=5)
                    if proc is not None and proc.stdin is not None:
                        proc.stdin.close()

        result_text = "".join(text_parts)
        if error:
            result_text = f"{result_text}\n{error}".strip()
        conversation = (
            log.save_conversation(
                f"kimi_{id(self) % 10000:04d}",
                self._stats.queries,
                messages,
            )
            if messages
            else None
        )
        result = QueryResult(
            text=result_text,
            elapsed_s=time.monotonic() - started,
            # ACP exposes tool starts, not model turns; text-only work counts once.
            turns=max(1, tool_calls),
            is_error=bool(error),
            incomplete_reason=incomplete_reason,
        )
        log.emit(
            "session_query_end",
            session="kimi",
            model=self.model,
            session_id=self._session_id,
            response_text=result.text,
            elapsed_s=result.elapsed_s,
            is_error=result.is_error,
            conversation_log=conversation,
        )
        return result
