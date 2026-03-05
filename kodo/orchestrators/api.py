"""Orchestrator using Pydantic AI with tool_use (Anthropic, Gemini, etc.)."""

from __future__ import annotations

import time
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
from pydantic_ai.usage import UsageLimits
from pydantic_ai_summarization import create_summarization_processor

from kodo import log
from kodo.models import (
    CLAUDE_OPUS_FULL,
    MODEL_PRICING,
    PYDANTIC_MODEL_MAP,
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
        self.model = model
        self._orchestrator_name = "api"
        self.max_context_tokens = max_context_tokens
        self._system_prompt = system_prompt or ORCHESTRATOR_SYSTEM_PROMPT
        self._pydantic_model = PYDANTIC_MODEL_MAP.get(model, model)
        self._fallback_model = fallback_model
        self._fallback_pydantic = (
            PYDANTIC_MODEL_MAP.get(fallback_model, fallback_model)
            if fallback_model
            else None
        )
        self._summarizer = Summarizer()
        self._http_client: httpx.AsyncClient | None = None

    def for_parallel(self) -> "ApiOrchestrator":
        """Create a copy safe for use in a parallel thread.

        pydantic-ai caches a process-global ``httpx.AsyncClient`` per provider.
        That client's transport holds asyncio primitives bound to whatever
        event loop first used it, so reusing it from a thread with a different
        loop crashes.  This method creates a new orchestrator whose model is an
        explicit ``Model`` instance with its own HTTP client, bypassing the
        cache entirely.
        """
        from kodo.models import make_fresh_model

        copy = ApiOrchestrator(
            model=self.model,
            max_context_tokens=self.max_context_tokens,
            system_prompt=self._system_prompt,
            fallback_model=self._fallback_model,
        )
        pydantic_model_str = self._pydantic_model
        if isinstance(pydantic_model_str, str):
            copy._pydantic_model = make_fresh_model(pydantic_model_str)
        else:
            # Already a Model instance — just use it directly
            copy._pydantic_model = pydantic_model_str
        return copy

    async def close(self) -> None:
        """Close any HTTP client created by :meth:`for_parallel`."""
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    def cycle(
        self,
        goal: str,
        project_dir: Path,
        team: TeamConfig,
        *,
        max_exchanges: int = 30,
        prior_summary: str = "",
        config: CycleConfig | None = None,
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
        )
        result = CycleResult()

        prompt = build_cycle_prompt(goal, project_dir, prior_summary)

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
            f"\n🚀 [orchestrator] starting cycle (max {max_exchanges} requests)...",
        )

        max_retries = 3
        run_result = None
        for attempt in range(max_retries):
            try:
                run_result = agent.run_sync(
                    prompt,
                    usage_limits=UsageLimits(request_limit=max_exchanges),
                )
                break
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

        if run_result is not None:
            usage = run_result.usage()
            price_in, price_out = MODEL_PRICING.get(self.model, (0, 0))
            result.total_cost_usd = (
                usage.input_tokens * price_in + usage.output_tokens * price_out
            ) / 1_000_000
            result.exchanges = usage.requests
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
            summary_result = summarizer_agent.run_sync(
                f"Conversation:\n\n{_messages_to_text(messages)}",
            )
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
