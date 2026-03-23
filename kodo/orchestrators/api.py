"""Orchestrator using Pydantic AI with tool_use (Anthropic, Gemini, etc.)."""

from __future__ import annotations

import asyncio
import queue
import threading
import time
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FuturesTimeoutError
from pathlib import Path

import httpx
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelHTTPError, UsageLimitExceeded
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.usage import RunUsage, UsageLimits
from pydantic_ai_summarization import create_summarization_processor

from kodo import log
from kodo.models import (
    CLAUDE_OPUS_FULL,
    MODEL_PRICING,
    PYDANTIC_MODEL_MAP,
    ensure_ollama_base_url,
    get_pricing,
    is_ollama_model,
    make_fresh_model,
    resolve_model,
)
from kodo.prompts.roles import ORCHESTRATOR_SYSTEM_PROMPT
from kodo.orchestrators.base import (
    CycleConfig,
    CycleResult,
    DoneSignal,
    FatalAgentError,
    OrchestratorBase,
    TeamConfig,
    apply_done_signal,
    build_cycle_prompt,
)
from kodo.orchestrators.tools import build_pydantic_tools
from kodo.orchestrators.verification import VerificationState
from kodo.summarizer import Summarizer

# Backwards-compatible aliases for any external importers
_MODEL_PRICING = MODEL_PRICING
_PYDANTIC_MODEL_MAP = PYDANTIC_MODEL_MAP


class _CycleWallTimeout(Exception):
    """Raised when run_sync exceeds the wall-clock timeout."""


def _messages_to_text(messages: list) -> str:
    """Flatten pydantic-ai message history to text for summarization."""
    parts: list[str] = []
    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if hasattr(part, "content") and isinstance(part.content, str):
                    parts.append(f"[user] {part.content[:500]}")
                elif isinstance(part, ToolReturnPart):
                    parts.append(
                        f"[user] tool_result({part.tool_name}): {str(part.content)[:300]}",
                    )
        elif isinstance(msg, ModelResponse):
            for part in msg.parts:
                if isinstance(part, TextPart):
                    parts.append(f"[assistant] {part.content[:300]}")
                elif isinstance(part, ToolCallPart):
                    parts.append(f"[assistant] tool_use: {part.tool_name}")
    return "\n".join(parts)


class ApiOrchestrator(OrchestratorBase):
    """Orchestrator backed by Pydantic AI (supports Anthropic, Gemini, etc.)."""

    def __init__(
        self,
        model: str = CLAUDE_OPUS_FULL,
        max_context_tokens: int | None = 100_000,
        system_prompt: str | None = None,
        fallback_model: str | None = None,
    ):
        from kodo.models import orchestrator_emoji

        self.model = model
        self._orchestrator_name = "api"
        self._emoji = orchestrator_emoji("api", model)
        self.max_context_tokens = max_context_tokens
        self._system_prompt = system_prompt or ORCHESTRATOR_SYSTEM_PROMPT
        if is_ollama_model(model):
            ensure_ollama_base_url()
        # Use make_fresh_model to avoid pydantic-ai's global httpx client cache.
        # The cached client binds asyncio primitives to whichever event loop first
        # uses it, causing "bound to a different event loop" crashes on re-entry.
        self._pydantic_model = make_fresh_model(resolve_model(model))
        self._fallback_model = fallback_model
        self._fallback_pydantic = (
            make_fresh_model(resolve_model(fallback_model)) if fallback_model else None
        )
        self._summarizer = Summarizer()
        self._http_client: httpx.AsyncClient | None = None

        # Persistent worker thread with a stable event loop.
        #
        # httpx/httpcore bind asyncio primitives (Locks, Events) to whichever
        # event loop first uses them via asyncio's lazy _get_loop() binding.
        # Previously each _run_sync_with_timeout() call created a new daemon
        # thread → pydantic-ai's run_sync() created a new event loop → the
        # httpx client's primitives from cycle N were bound to a dead loop by
        # cycle N+1, causing "bound to a different event loop" crashes.
        #
        # By running all run_sync() calls in the same thread, get_event_loop()
        # always returns the same loop, and httpx primitives stay bound to it.
        self._task_queue: queue.Queue[tuple[callable, Future] | None] = queue.Queue()
        self._worker_thread = threading.Thread(
            target=self._run_loop_worker, daemon=True
        )
        self._worker_thread.start()

    def _run_loop_worker(self) -> None:
        """Worker loop: process tasks sequentially on a stable event loop.

        Creates one asyncio event loop for this thread and reuses it for
        every task, ensuring httpx's connection pool primitives always bind
        to the same loop.
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            while True:
                item = self._task_queue.get()
                if item is None:  # shutdown sentinel
                    break
                fn, future = item
                if future.cancelled():
                    continue
                try:
                    result = fn()
                except BaseException as exc:
                    if not future.cancelled():
                        future.set_exception(exc)
                else:
                    if not future.cancelled():
                        future.set_result(result)
        finally:
            loop.close()

    def for_parallel(self) -> "ApiOrchestrator":
        """Create a copy safe for use in a parallel thread.

        Each copy gets its own worker thread + fresh httpx client,
        avoiding cross-loop asyncio conflicts.
        """
        return ApiOrchestrator(
            model=self.model,
            max_context_tokens=self.max_context_tokens,
            system_prompt=self._system_prompt,
            fallback_model=self._fallback_model,
        )

    async def close(self) -> None:
        """Close any HTTP client created by :meth:`for_parallel`."""
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    def shutdown(self) -> None:
        """Stop the worker thread by sending a shutdown sentinel."""
        self._task_queue.put(None)
        self._worker_thread.join(timeout=5)

    def cycle(
        self,
        goal: str,
        project_dir: Path,
        team: TeamConfig,
        *,
        max_exchanges: int = 30,
        prior_summary: str = "",
        config: CycleConfig | None = None,
        advisory_queue=None,
        coach=None,
    ) -> CycleResult:
        if config is None:
            config = CycleConfig()
        done_signal = DoneSignal()
        verification_state = VerificationState()
        tools = build_pydantic_tools(
            team,
            project_dir,
            self._summarizer,
            done_signal,
            goal,
            verification_state=verification_state,
            config=config,
            advisory_queue=advisory_queue,
            coach=coach,
        )
        result = CycleResult()

        prompt = build_cycle_prompt(
            goal, project_dir, prior_summary, advisory_queue=advisory_queue
        )

        log.emit(
            "cycle_start",
            orchestrator="api",
            model=self.model,
            goal=goal,
            project_dir=str(project_dir),
            max_exchanges=max_exchanges,
            has_prior_summary=bool(prior_summary),
            prior_summary=prior_summary or None,
        )

        history_processors = []
        if self.max_context_tokens:
            history_processors.append(
                create_summarization_processor(
                    trigger=("tokens", self.max_context_tokens),
                    keep=("tokens", self.max_context_tokens // 2),
                    model=self._pydantic_model,
                ),
            )

        system_prompt = self._system_prompt.format(
            log_path=log.get_log_file(),
        )

        agent = Agent(
            self._pydantic_model,
            system_prompt=system_prompt,
            tools=tools,
            history_processors=history_processors or None,
        )

        log.tprint(
            f"\n{self._emoji} [orchestrator] starting cycle (max {max_exchanges} requests)...",
        )

        max_retries = 3
        run_result = None
        # Shared usage accumulator — pydantic-ai mutates this in-place
        # during each run_sync call, so tokens consumed during failed
        # attempts are preserved when the next attempt succeeds.
        cumulative_usage = RunUsage()
        wall_timeout_s = 3600  # 60 min — workers can run up to ~40 min normally
        consecutive_timeouts = 0
        for attempt in range(max_retries):
            try:
                run_result = self._run_sync_with_timeout(
                    agent,
                    prompt,
                    max_exchanges,
                    cumulative_usage,
                    timeout_s=wall_timeout_s,
                )
                consecutive_timeouts = 0
                break
            except _CycleWallTimeout:
                consecutive_timeouts += 1
                log.tprint(
                    f"⏱️  [orchestrator] wall-clock timeout ({wall_timeout_s}s) "
                    f"(attempt {consecutive_timeouts}/{max_retries})",
                )
                log.emit(
                    "orchestrator_wall_timeout",
                    timeout_s=wall_timeout_s,
                    attempt=consecutive_timeouts,
                )
                if consecutive_timeouts >= max_retries:
                    result.finished = False
                    result.success = False
                    accumulated = self._summarizer.get_accumulated_summary()
                    result.summary = (
                        f"[Cycle aborted: orchestrator API hung {max_retries} times. "
                        f"Work so far:]\n{accumulated}"
                        if accumulated
                        else f"[Cycle aborted: orchestrator API hung {max_retries} times.]"
                    )
                    log.emit(
                        "cycle_end",
                        reason="wall_timeout_exhausted",
                        summary=result.summary,
                    )
                    return result
                continue
            except FatalAgentError as exc:
                log.tprint(f"🛑 [orchestrator] fatal worker error: {exc}")
                log.emit("cycle_fatal_agent_error", error=str(exc))
                result.finished = True
                result.success = False
                result.summary = f"Aborted: {exc}"
                return result
            except UsageLimitExceeded:
                log.tprint(f"⏱️  [orchestrator] request limit reached ({max_exchanges})")
                break
            except ModelHTTPError as exc:
                status = exc.status_code
                # Auth failures: not retryable, give a clear message
                if status in (401, 403):
                    if is_ollama_model(self.model):
                        provider = "Ollama"
                    else:
                        provider = "Gemini" if "gemini" in self.model else "Anthropic"
                    log.tprint(
                        f"[orchestrator] Authentication failed (HTTP {status}). "
                        f"Check your API key for {provider}.",
                    )
                    log.emit(
                        "orchestrator_auth_error",
                        status_code=status,
                        provider=provider,
                    )
                    raise
                elif status == 529 and self._fallback_pydantic and attempt == 0:
                    log.tprint(
                        f"[orchestrator] 529 on {self.model}, "
                        f"falling back to {self._fallback_model}",
                    )
                    log.emit(
                        "orchestrator_fallback",
                        primary=self.model,
                        fallback=self._fallback_model,
                    )
                    agent = Agent(
                        self._fallback_pydantic,
                        system_prompt=self._system_prompt,
                        tools=tools,
                        history_processors=history_processors or None,
                    )
                    continue
                elif (
                    status in (408, 429, 500, 502, 503, 504, 529)
                    and attempt < max_retries - 1
                ):
                    wait = 30 * (attempt + 1)
                    log.tprint(
                        f"[orchestrator] {status} from model API, "
                        f"retrying in {wait}s "
                        f"(attempt {attempt + 1}/{max_retries})...",
                    )
                    log.emit(
                        "orchestrator_retry",
                        status_code=status,
                        attempt=attempt + 1,
                        wait_s=wait,
                    )
                    time.sleep(wait)
                else:
                    raise
            except (
                httpx.TimeoutException,
                httpx.ConnectError,
                httpx.RemoteProtocolError,
            ) as exc:
                if attempt < max_retries - 1:
                    wait = 30 * (attempt + 1)
                    log.tprint(
                        f"[orchestrator] Network error: {type(exc).__name__}, "
                        f"retrying in {wait}s "
                        f"(attempt {attempt + 1}/{max_retries})...",
                    )
                    log.emit(
                        "orchestrator_retry",
                        error=f"{type(exc).__name__}: {exc}",
                        attempt=attempt + 1,
                        wait_s=wait,
                    )
                    time.sleep(wait)
                else:
                    raise

        # Use cumulative_usage which includes tokens from all attempts
        # (failed retries + the final run), not just run_result.usage().
        if cumulative_usage.requests:
            price_in, price_out = get_pricing(self.model)
            result.total_cost_usd = (
                cumulative_usage.input_tokens * price_in
                + cumulative_usage.output_tokens * price_out
            ) / 1_000_000
            result.exchanges = cumulative_usage.requests
            log.get_run_stats().record_orchestrator(result.total_cost_usd, "api")

        apply_done_signal(result, done_signal)

        if done_signal.called:
            log.emit(
                "cycle_end",
                reason="done",
                exchanges=result.exchanges,
                finished=result.finished,
                summary=result.summary,
                cost_usd=result.total_cost_usd,
                cost_bucket="api",
            )
        else:
            # Model stopped without calling done — summarize for next cycle.
            if run_result is not None:
                result.summary = self._summarize(run_result.all_messages())
            else:
                # UsageLimitExceeded — use accumulated agent summaries.
                accumulated = self._summarizer.get_accumulated_summary()
                result.summary = (
                    f"[Cycle ended: hit request limit after {max_exchanges} requests. "
                    f"Work so far:]\n{accumulated}"
                    if accumulated
                    else "[Cycle ended: hit request limit. No summary available.]"
                )
            log.emit(
                "cycle_end",
                reason="stop_no_done" if run_result else "request_limit",
                exchanges=result.exchanges,
                finished=False,
                summary=result.summary,
                cost_usd=result.total_cost_usd,
                cost_bucket="api",
            )

        return result

    def _run_sync_with_timeout(
        self,
        agent: Agent,
        prompt: str,
        max_exchanges: int,
        cumulative_usage: RunUsage,
        *,
        timeout_s: float,
    ):
        """Run agent.run_sync() on the persistent worker thread.

        All pydantic-ai run_sync() calls go through the same worker thread,
        which owns a single event loop. This prevents httpx/httpcore asyncio
        primitives from binding to different loops across cycles and retries.

        Raises _CycleWallTimeout if the call doesn't return in time.
        """
        future: Future = Future()

        def _task():
            return agent.run_sync(
                prompt,
                usage_limits=UsageLimits(request_limit=max_exchanges),
                usage=cumulative_usage,
            )

        if not self._worker_thread.is_alive():
            raise RuntimeError("Worker thread died — cannot dispatch run_sync()")
        self._task_queue.put((_task, future))
        try:
            return future.result(timeout=timeout_s)
        except FuturesTimeoutError:
            future.cancel()
            raise _CycleWallTimeout(f"run_sync did not complete within {timeout_s}s")

    def _summarize(self, messages: list) -> str:
        """Compress conversation into a summary using a simple agent."""
        log.emit("summarize_start", message_count=len(messages))

        try:
            summarizer_agent = Agent(
                self._pydantic_model,
                system_prompt=(
                    "Summarize this orchestration conversation concisely. "
                    "Include: what was accomplished, what's pending, any known issues."
                ),
            )
            # Run on the persistent worker thread to keep httpx on the same
            # event loop as the main cycle calls.
            future: Future = Future()
            text = _messages_to_text(messages)

            def _task():
                return summarizer_agent.run_sync(
                    f"Conversation:\n\n{text}",
                )

            self._task_queue.put((_task, future))
            summary_result = future.result(timeout=120)
            output = summary_result.output
            if output and output.strip():
                summary = output.strip()
            else:
                accumulated = self._summarizer.get_accumulated_summary()
                summary = (
                    f"[Summarization returned empty. Work so far:]\n{accumulated}"
                    if accumulated
                    else "[Summarization returned empty. No detailed summary available.]"
                )
        except Exception as exc:
            log.tprint(f"[orchestrator] summarization failed: {exc}")
            log.emit("summarize_error", error=str(exc))
            # Fall back to accumulated agent summaries so the cycle remains resumable.
            accumulated = self._summarizer.get_accumulated_summary()
            summary = (
                f"[Summarization failed: {exc}. Work so far:]\n{accumulated}"
                if accumulated
                else f"[Summarization failed: {exc}. No detailed summary available.]"
            )

        log.emit("summarize_end", summary=summary)
        return summary
