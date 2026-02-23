"""Orchestrator using Pydantic AI with tool_use (Anthropic, Gemini, etc.)."""

from __future__ import annotations

from pathlib import Path

import time

import httpx
from pydantic_ai import Agent, Tool
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
from kodo.summarizer import Summarizer
from kodo.orchestrators.base import (
    ORCHESTRATOR_SYSTEM_PROMPT,
    CycleResult,
    DoneSignal,
    OrchestratorBase,
    TeamConfig,
    VerificationState,
    build_cycle_prompt,
    handle_agent_call,
    handle_done,
)

# Per-1M-token pricing: (input, output)
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-6": (5, 25),
    "claude-sonnet-4-5-20250929": (3, 15),
    "gemini-3.1-pro-preview": (2.0, 12.0),
    "gemini-3-pro-preview": (2.0, 12.0),
    "gemini-3-flash-preview": (0.50, 3.0),
}

# Map our model IDs to pydantic-ai model strings (provider:model).
_PYDANTIC_MODEL_MAP: dict[str, str] = {
    "claude-opus-4-6": "anthropic:claude-opus-4-6",
    "claude-sonnet-4-5-20250929": "anthropic:claude-sonnet-4-5-20250929",
    "gemini-3.1-pro-preview": "google-gla:gemini-3.1-pro-preview",
    "gemini-3-pro-preview": "google-gla:gemini-3-pro-preview",
    "gemini-3-flash-preview": "google-gla:gemini-3-flash-preview",
}


def _build_tools(
    team: TeamConfig,
    project_dir: Path,
    summarizer: Summarizer,
    done_signal: DoneSignal,
    goal: str,
    verification_state: VerificationState | None = None,
    browser_testing: bool = False,
    verifiers: dict | None = None,
    auto_commit: bool = False,
) -> list[Tool]:
    """Build pydantic-ai Tool objects for each team agent + the done tool."""
    tools: list[Tool] = []

    for name, agent in team.items():

        def _make_handler(agent_name: str, agent_obj):
            def handler(task: str, new_conversation: bool = False) -> str:
                return handle_agent_call(
                    agent_name,
                    agent_obj,
                    task,
                    project_dir,
                    summarizer,
                    new_conversation=new_conversation,
                )

            return handler

        tools.append(
            Tool(
                _make_handler(name, agent),
                name=f"ask_{name}",
                description=f"Delegate a task to the {name} agent.\n{agent.description.strip()}",
                takes_ctx=False,
            )
        )

    def done(summary: str, success: bool) -> str:
        """Signal that the goal is complete (or cannot be completed).
        This triggers automated verification by the tester and architect.
        If they find issues, the call is rejected and you must fix them first."""
        return handle_done(
            summary,
            success,
            done_signal,
            goal,
            team,
            project_dir,
            verification_state=verification_state,
            browser_testing=browser_testing,
            verifiers=verifiers,
            auto_commit=auto_commit,
        )

    tools.append(Tool(done, takes_ctx=False))
    return tools


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
                        f"[user] tool_result({part.tool_name}): {str(part.content)[:300]}"
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
        model: str = "claude-opus-4-6",
        max_context_tokens: int | None = 100_000,
        system_prompt: str | None = None,
        fallback_model: str | None = None,
    ):
        self.model = model
        self._orchestrator_name = "api"
        self.max_context_tokens = max_context_tokens
        self._system_prompt = system_prompt or ORCHESTRATOR_SYSTEM_PROMPT
        self._pydantic_model = _PYDANTIC_MODEL_MAP.get(model, model)
        self._fallback_model = fallback_model
        self._fallback_pydantic = (
            _PYDANTIC_MODEL_MAP.get(fallback_model, fallback_model)
            if fallback_model
            else None
        )
        self._summarizer = Summarizer()

    def for_parallel(self) -> "ApiOrchestrator":
        """Create a copy safe for use in a parallel thread.

        pydantic-ai caches a process-global ``httpx.AsyncClient`` per provider.
        That client's transport holds asyncio primitives bound to whatever
        event loop first used it, so reusing it from a thread with a different
        loop crashes.  This method creates a new orchestrator whose model is an
        explicit ``Model`` instance with its own HTTP client, bypassing the
        cache entirely.
        """
        from pydantic_ai.models import infer_model

        copy = ApiOrchestrator(
            model=self.model,
            max_context_tokens=self.max_context_tokens,
            system_prompt=self._system_prompt,
            fallback_model=self._fallback_model,
        )
        # Replace the model string with an explicit Model instance that owns
        # its own httpx.AsyncClient (no shared cache).
        base_model = infer_model(self._pydantic_model)
        if hasattr(base_model, "client"):
            # Google/Gemini models — recreate with a fresh HTTP client
            from pydantic_ai.providers.google import GoogleProvider

            provider = GoogleProvider(http_client=httpx.AsyncClient())
            type_of_model = type(base_model)
            copy._pydantic_model = type_of_model(
                base_model._model_name, provider=provider
            )
        else:
            # Anthropic/other models — infer_model already creates a new
            # instance; the cache issue is specific to Google providers.
            copy._pydantic_model = base_model
        return copy

    def cycle(
        self,
        goal: str,
        project_dir: Path,
        team: TeamConfig,
        *,
        max_exchanges: int = 30,
        prior_summary: str = "",
        browser_testing: bool = False,
        verifiers: dict | None = None,
        auto_commit: bool = False,
    ) -> CycleResult:
        done_signal = DoneSignal()
        verification_state = VerificationState()
        tools = _build_tools(
            team,
            project_dir,
            self._summarizer,
            done_signal,
            goal,
            verification_state,
            browser_testing=browser_testing,
            verifiers=verifiers,
            auto_commit=auto_commit,
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
                )
            )

        agent = Agent(
            self._pydantic_model,
            system_prompt=self._system_prompt,
            tools=tools,
            history_processors=history_processors or None,
        )

        log.tprint(f"\n[orchestrator] starting cycle (max {max_exchanges} requests)...")

        max_retries = 3
        run_result = None
        for attempt in range(max_retries):
            try:
                run_result = agent.run_sync(
                    prompt,
                    usage_limits=UsageLimits(request_limit=max_exchanges),
                )
                break
            except UsageLimitExceeded:
                log.tprint(f"[orchestrator] request limit reached ({max_exchanges})")
                break
            except ModelHTTPError as exc:
                status = exc.status_code
                if status == 529 and self._fallback_pydantic and attempt == 0:
                    log.tprint(
                        f"[orchestrator] 529 on {self.model}, "
                        f"falling back to {self._fallback_model}"
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
                elif status in (429, 500, 502, 503, 529) and attempt < max_retries - 1:
                    wait = 30 * (attempt + 1)
                    log.tprint(
                        f"[orchestrator] {status} from model API, "
                        f"retrying in {wait}s "
                        f"(attempt {attempt + 1}/{max_retries})..."
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

        if run_result is not None:
            usage = run_result.usage()
            price_in, price_out = _MODEL_PRICING.get(self.model, (0, 0))
            result.total_cost_usd = (
                usage.input_tokens * price_in + usage.output_tokens * price_out
            ) / 1_000_000
            result.exchanges = usage.requests
            log.get_run_stats().record_orchestrator(result.total_cost_usd, "api")

        if done_signal.called:
            result.finished = True
            result.success = done_signal.success
            result.summary = done_signal.summary
            log.emit(
                "cycle_end",
                reason="done",
                exchanges=result.exchanges,
                finished=True,
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
        summary = summary_result.output
        log.emit("summarize_end", summary=summary)
        return summary
