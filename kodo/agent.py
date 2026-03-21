"""Agent — a prompt + session, ready to run.

Usage::

    agent = Agent(session, "worker description", timeout_s=300)
    result = agent.run("implement feature X", project_dir)

    # When done, release session resources:
    agent.close()

    # Or use as a context manager:
    with Agent(session, "worker") as agent:
        result = agent.run("task", project_dir)
"""

from __future__ import annotations

import threading
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from pathlib import Path

from kodo import log
from kodo.sessions.base import QueryResult, Session


@dataclass
class AgentResult:
    """Agent run result with context metadata."""

    query: QueryResult
    context_reset: bool = False  # was the session reset before this run?
    context_reset_reason: str = ""  # why it was reset
    session_tokens: int = 0  # cumulative session tokens after this run
    session_queries: int = 0  # cumulative session queries after this run

    @property
    def text(self) -> str:
        return self.query.text

    @property
    def is_error(self) -> bool:
        return self.query.is_error

    @property
    def elapsed_s(self) -> float:
        return self.query.elapsed_s

    def format_report(self) -> str:
        """Format the result with context metadata for the orchestrator."""
        parts = []

        if self.context_reset:
            parts.append(f"[Context was reset: {self.context_reset_reason}]")

        parts.append(self.query.text or "(no output)")

        parts.append(
            f"\n---\n"
            f"[Context: {self.session_tokens:,} tokens used"
            f" | {self.session_queries} queries in session]",
        )

        return "\n".join(parts)


class Agent:
    """A prompt + session, ready to run.

    Wraps a :class:`Session` with timeout support, context-reset logic,
    and structured logging.

    Supports use as a context manager for automatic cleanup::

        with Agent(session, "worker", timeout_s=300) as agent:
            result = agent.run("task", project_dir)
        # session.terminate() + session.close() called on exit

    Or call :meth:`close` explicitly when done.
    """

    def __init__(
        self,
        session: Session,
        description: str = "",
        *,
        max_turns: int = 15,
        timeout_s: float | None = None,
    ):
        self.session = session
        self.description = description  # kept for tool description extraction
        self.max_turns = max_turns
        self.timeout_s = timeout_s

    # -- context manager ---------------------------------------------------

    def __enter__(self) -> "Agent":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    # -- core API ----------------------------------------------------------

    def run(
        self,
        goal: str,
        project_dir: Path,
        *,
        new_conversation: bool = False,
        agent_name: str = "",
    ) -> AgentResult:
        context_reset = False
        context_reset_reason = ""
        label = agent_name or "agent"

        log.emit(
            "agent_run_start",
            agent=label,
            new_conversation=new_conversation,
            goal=goal,
            session_tokens=self.session.stats.total_tokens,
            session_queries=self.session.stats.queries,
        )

        # Explicit reset requested by orchestrator
        if new_conversation:
            had_context = self.session.stats.queries > 0
            self.session.reset()
            if had_context:
                context_reset = True
                context_reset_reason = "orchestrator requested new conversation"
                log.emit("agent_session_reset", agent=label, reason=context_reset_reason)

        log.emit("agent_query", agent=label, prompt=goal)

        if self.timeout_s is not None:
            query_result = self._run_timed(goal, project_dir, label)
        else:
            query_result = self.session.query(
                goal,
                project_dir,
                max_turns=self.max_turns,
            )

        bucket = self.session.cost_bucket
        log.emit(
            "agent_run_end",
            agent=label,
            elapsed_s=query_result.elapsed_s,
            is_error=query_result.is_error,
            turns=query_result.turns,
            input_tokens=query_result.input_tokens,
            output_tokens=query_result.output_tokens,
            cost_usd=query_result.cost_usd,
            cost_bucket=bucket,
            response_text=query_result.text,
            context_reset=context_reset,
            context_reset_reason=context_reset_reason,
            session_tokens=self.session.stats.total_tokens,
            session_queries=self.session.stats.queries,
        )

        log.get_run_stats().record_agent(
            agent=label,
            cost_usd=query_result.cost_usd or 0,
            input_tokens=query_result.input_tokens or 0,
            output_tokens=query_result.output_tokens or 0,
            elapsed_s=query_result.elapsed_s or 0,
            is_error=query_result.is_error,
            cost_bucket=bucket,
        )

        return AgentResult(
            query=query_result,
            context_reset=context_reset,
            context_reset_reason=context_reset_reason,
            session_tokens=self.session.stats.total_tokens,
            session_queries=self.session.stats.queries,
        )

    # Adaptive timeout: if no output, time out quickly (idle_timeout_s).
    # Each time the session produces output, extend the deadline by up to
    # idle_timeout_s more seconds, never exceeding max_timeout_s total.
    _IDLE_TIMEOUT_S: float = 300  # 5 min of silence → timeout
    _MAX_TIMEOUT_S: float = 4500  # 75 min hard cap
    _POLL_INTERVAL_S: float = 5  # check activity every 5s

    def _run_timed(self, goal: str, project_dir: Path, label: str) -> QueryResult:
        """Execute a query with an adaptive timeout.

        Uses a daemon thread so that even if session.terminate() fails to
        unblock the worker, the thread won't prevent process exit.

        The timeout extends automatically when the session produces output:
        - If the session is idle for ``_IDLE_TIMEOUT_S`` (5 min), it times out.
        - If the session keeps producing output, it can run up to
          ``_MAX_TIMEOUT_S`` (75 min) total.
        - The configured ``timeout_s`` acts as the initial idle timeout
          (overrides ``_IDLE_TIMEOUT_S`` if set).
        """
        import time

        future: Future[QueryResult] = Future()

        def worker() -> None:
            try:
                result = self.session.query(
                    goal,
                    project_dir,
                    max_turns=self.max_turns,
                )
                future.set_result(result)
            except BaseException as e:
                future.set_exception(e)

        idle_timeout = self.timeout_s or self._IDLE_TIMEOUT_S
        max_timeout = max(self._MAX_TIMEOUT_S, idle_timeout)
        poll_interval = min(self._POLL_INTERVAL_S, idle_timeout / 2)
        start = time.monotonic()

        t = threading.Thread(target=worker, daemon=True)
        t.start()

        # Poll until the future completes, idle timeout fires, or hard cap.
        last_seen_activity = self.session.stats.last_activity
        while True:
            try:
                return future.result(timeout=poll_interval)
            except FuturesTimeoutError:
                pass  # not done yet — check activity

            now = time.monotonic()
            elapsed = now - start
            current_activity = self.session.stats.last_activity

            # Activity detected — reset idle clock
            if current_activity > last_seen_activity:
                last_seen_activity = current_activity

            idle_s = now - last_seen_activity

            if elapsed >= max_timeout:
                log.emit(
                    "agent_timeout",
                    agent=label,
                    timeout_s=max_timeout,
                    reason="max_timeout",
                    elapsed_s=elapsed,
                )
                break
            if idle_s >= idle_timeout:
                log.emit(
                    "agent_timeout",
                    agent=label,
                    timeout_s=idle_timeout,
                    reason="idle",
                    idle_s=idle_s,
                    elapsed_s=elapsed,
                )
                break

        elapsed = time.monotonic() - start
        self.session.terminate()
        try:
            future.result(timeout=0.5)
        except FuturesTimeoutError:
            log.emit(
                "agent_timeout_worker_stuck",
                agent=label,
                message="Worker thread did not exit after terminate()",
            )
        except Exception:
            # Worker finished with an error during grace period
            # (e.g. ConnectionError). Swallow it — the timeout result
            # below is the correct response for the caller.
            pass
        self.session.reset()
        return QueryResult(
            text=(
                f"Agent timed out after {elapsed:.0f}s. "
                "Hint: increase timeout_s in Agent or TeamConfig."
            ),
            elapsed_s=elapsed,
            is_error=True,
        )

    def clone(self) -> "Agent":
        """Create a new Agent with a fresh session copy (no shared state)."""
        return Agent(
            session=self.session.clone(),
            description=self.description,
            max_turns=self.max_turns,
            timeout_s=self.timeout_s,
        )

    def close(self) -> None:
        """Release resources held by the underlying session.

        Calls ``session.terminate()`` to stop any in-flight work, then
        ``session.close()`` for final cleanup.  Safe to call multiple times.
        """
        try:
            self.session.terminate()
        finally:
            self.session.close()
