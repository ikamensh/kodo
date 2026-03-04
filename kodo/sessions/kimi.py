"""Kimi session using kimi-agent-sdk with conversation continuity."""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

from kodo import log
from kodo.models import KIMI_K2_5
from kodo.sessions.base import QueryResult, SessionStats


class KimiSession:
    """Session backed by Moonshot AI's Kimi Code via kimi-agent-sdk.

    Uses a dedicated background thread + asyncio event loop (same pattern as
    ClaudeSession) because the kimi-agent-sdk is in-process async.
    """

    def __init__(
        self,
        model: str = KIMI_K2_5,
        system_prompt: str | None = None,
        resume_session_id: str | None = None,
        session_timeout_s: int | None = None,
    ):
        self.model = model
        self.system_prompt = system_prompt
        self.resume_session_id = resume_session_id
        self._session_timeout_s = session_timeout_s
        self._session = None  # kimi_agent_sdk.Session (created lazily)
        self._project_dir: Path | None = None
        self._session_id: str | None = None
        self._stats = SessionStats()
        self._system_prompt_sent = False
        # Dedicated thread+loop so we never conflict with a caller's event loop
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._closed = False
        self._close_lock = threading.Lock()

    def __enter__(self) -> KimiSession:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    _DEFAULT_QUERY_TIMEOUT: float = 7200  # 2 hours

    @property
    def _query_timeout(self) -> float:
        if self._session_timeout_s is not None:
            return float(self._session_timeout_s)
        return self._DEFAULT_QUERY_TIMEOUT

    def _run(self, coro, *, timeout: float | None = None):
        """Submit a coroutine to our background loop and block until complete."""
        if coro is None:
            return
        if not self._thread.is_alive():
            raise RuntimeError(
                "Background event-loop thread is dead; cannot execute coroutine",
            )
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    @property
    def stats(self) -> SessionStats:
        return self._stats

    @property
    def cost_bucket(self) -> str:
        return "kimi_api"

    @property
    def session_id(self) -> str | None:
        return self._session_id

    def _ensure_session(self, project_dir: Path) -> None:
        """Lazily create the kimi SDK session on first query."""
        if self._session is not None and self._project_dir == project_dir:
            return

        self._close_session()
        self._project_dir = project_dir

        async def _create():
            from kimi_agent_sdk import Session as KimiSdkSession

            resume_id = self.resume_session_id
            if resume_id:
                self.resume_session_id = None  # one-shot
                session = await KimiSdkSession.resume(
                    work_dir=str(project_dir),
                    session_id=resume_id,
                    model=self.model,
                    yolo=True,
                )
                if session is None:
                    # Resume failed — fall back to new session
                    log.tprint(
                        f"⚠️  [kimi] resume failed for {resume_id}, creating new session",
                    )
                    session = await KimiSdkSession.create(
                        work_dir=str(project_dir),
                        model=self.model,
                        yolo=True,
                    )
            else:
                session = await KimiSdkSession.create(
                    work_dir=str(project_dir),
                    model=self.model,
                    yolo=True,
                )
            self._session = session
            self._session_id = session.id

        self._run(_create(), timeout=120)

    def _close_session(self) -> None:
        if self._session is not None:
            try:
                self._run(self._session.close(), timeout=10)
            except (RuntimeError, TimeoutError):
                pass
            self._session = None

    def clone(self) -> KimiSession:
        return KimiSession(
            model=self.model,
            system_prompt=self.system_prompt,
            session_timeout_s=self._session_timeout_s,
        )

    def close(self) -> None:
        """Stop the event loop and join the background thread. Idempotent."""
        with self._close_lock:
            if self._closed:
                return
            self._closed = True

        try:
            self._close_session()
        except (OSError, RuntimeError):
            pass

        # Cancel pending asyncio tasks
        try:
            pending = asyncio.all_tasks(self._loop)
            for task in pending:
                self._loop.call_soon_threadsafe(task.cancel)
            time.sleep(0.05)
        except RuntimeError:
            pass

        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except RuntimeError:
            pass
        self._thread.join(timeout=5)
        # No subprocess to force-kill (kimi SDK is in-process)
        if self._thread.is_alive():
            self._thread.join(timeout=2)
        if self._thread.is_alive():
            log.emit(
                "session_close_warning",
                session="kimi",
                reason="thread_still_alive",
            )
        try:
            self._loop.close()
        except (OSError, RuntimeError):
            pass

    def terminate(self) -> None:
        """Cancel the running query. Safe when idle."""
        if self._session is not None:
            try:
                self._session.cancel()
            except Exception:
                pass

    def reset(self) -> None:
        log.emit(
            "session_reset",
            session="kimi",
            model=self.model,
            tokens_before=self._stats.total_tokens,
            queries_before=self._stats.queries,
        )
        self._close_session()
        self._stats = SessionStats()
        self._system_prompt_sent = False

    def query(self, prompt: str, project_dir: Path, *, max_turns: int) -> QueryResult:
        if self._loop.is_closed():
            raise RuntimeError("Session is closed")

        # Prepend system prompt to first query only
        if self.system_prompt and not self._system_prompt_sent:
            prompt = f"{self.system_prompt}\n\n{prompt}"
            self._system_prompt_sent = True

        try:
            self._ensure_session(project_dir)
        except Exception as exc:
            self._session = None
            self._project_dir = None
            exc_name = type(exc).__name__
            return QueryResult(
                text=(
                    f"Kimi session failed to connect: {exc_name}: {exc}\n"
                    "Check that kimi-cli is installed and MOONSHOT_API_KEY is set."
                ),
                elapsed_s=0.0,
                is_error=True,
            )

        log.emit(
            "session_query_start",
            session="kimi",
            model=self.model,
            prompt=prompt,
            max_turns=max_turns,
            project_dir=str(project_dir),
        )

        t0 = time.monotonic()

        try:
            text_parts: list[str] = []
            input_tokens = 0
            output_tokens = 0
            turns = 0

            async def _do_query():
                nonlocal input_tokens, output_tokens, turns
                from kimi_agent_sdk import (
                    ApprovalRequest,
                    TextPart,
                    TokenUsage,
                    TurnEnd,
                )

                assert self._session is not None
                async for wire_msg in self._session.prompt(prompt):
                    if isinstance(wire_msg, TextPart):
                        text_parts.append(wire_msg.text)
                    elif isinstance(wire_msg, TokenUsage):
                        input_tokens += getattr(wire_msg, "prompt_tokens", 0) or 0
                        output_tokens += getattr(wire_msg, "completion_tokens", 0) or 0
                    elif isinstance(wire_msg, TurnEnd):
                        turns += 1
                    elif isinstance(wire_msg, ApprovalRequest):
                        wire_msg.resolve("approve")

                # Refresh session ID after query
                self._session_id = self._session.id

            self._run(_do_query(), timeout=self._query_timeout)

            elapsed = time.monotonic() - t0
            result_text = "".join(text_parts)

            self._stats.queries += 1
            self._stats.total_input_tokens += input_tokens
            self._stats.total_output_tokens += output_tokens

            result = QueryResult(
                text=result_text,
                elapsed_s=elapsed,
                turns=turns or None,
                input_tokens=input_tokens or None,
                output_tokens=output_tokens or None,
            )

        except Exception as exc:
            elapsed = time.monotonic() - t0
            exc_name = type(exc).__name__
            log.emit(
                "session_query_error",
                session="kimi",
                model=self.model,
                error=f"{exc_name}: {exc}",
            )
            return QueryResult(
                text=f"Kimi session error during query: {exc_name}: {exc}",
                elapsed_s=elapsed,
                is_error=True,
            )

        log.emit(
            "session_query_end",
            session="kimi",
            model=self.model,
            elapsed_s=result.elapsed_s,
            is_error=result.is_error,
            turns=result.turns,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            response_text=result.text,
            session_id=self._session_id,
        )
        return result
