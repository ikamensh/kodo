"""Orchestrator using Kimi SDK with MCP tools for agent delegation."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from kodo import log
from kodo.prompts.roles import ORCHESTRATOR_SYSTEM_PROMPT
from kodo.orchestrators.base import (
    CycleConfig,
    CycleResult,
    DoneSignal,
    OrchestratorBase,
    TeamConfig,
    apply_done_signal,
    build_cycle_prompt,
)
from kodo.orchestrators.mcp_server import McpServerContext, build_mcp_server
from kodo.orchestrators.verification import VerificationState
from kodo.models import KIMI_K2_5
from kodo.summarizer import Summarizer


class KimiCodeOrchestrator(OrchestratorBase):
    """Orchestrator backed by Kimi SDK with MCP tools for agents."""

    def __init__(self, model: str = KIMI_K2_5, system_prompt: str | None = None):
        self.model = model
        self._orchestrator_name = "kimi-code"
        self._system_prompt = system_prompt or ORCHESTRATOR_SYSTEM_PROMPT
        self._summarizer = Summarizer()

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

        log.emit(
            "cycle_start",
            orchestrator="kimi-code",
            model=self.model,
            goal=goal,
            project_dir=str(project_dir),
            max_exchanges=max_exchanges,
            has_prior_summary=bool(prior_summary),
            prior_summary=prior_summary or None,
        )

        done_signal = DoneSignal()
        verification_state = VerificationState()
        mcp = build_mcp_server(
            team,
            project_dir,
            self._summarizer,
            done_signal,
            goal,
            orchestrator_tag="kimi-code",
            verification_state=verification_state,
            config=config,
        )

        result = CycleResult()
        prompt = build_cycle_prompt(goal, project_dir, prior_summary)
        full_prompt = f"{self._system_prompt}\n\n{prompt}"

        _MAX_NUDGES = 3

        with McpServerContext(mcp) as ctx:
            mcp_configs = [{"mcpServers": {"team": {"url": ctx.sse_url}}}]

            async def _run_cycle():
                from kimi_agent_sdk import (
                    ApprovalRequest,
                    Session as KimiSdkSession,
                    TextPart,
                    TokenUsage,
                    TurnEnd,
                )

                session = await KimiSdkSession.create(
                    work_dir=str(project_dir),
                    model=self.model,
                    yolo=True,
                    mcp_configs=mcp_configs,
                )

                try:
                    log.tprint("🚀 [orchestrator] starting kimi-code cycle...")

                    async def _stream_prompt(p: str) -> str:
                        """Stream a prompt, aggregate text, update result stats."""
                        parts: list[str] = []
                        async for wire_msg in session.prompt(p):
                            if isinstance(wire_msg, TextPart):
                                parts.append(wire_msg.text)
                            elif isinstance(wire_msg, TokenUsage):
                                pass  # token tracking not critical for orchestrator
                            elif isinstance(wire_msg, TurnEnd):
                                result.exchanges += 1
                            elif isinstance(wire_msg, ApprovalRequest):
                                wire_msg.resolve("approve")
                        return "".join(parts)

                    response_text = await _stream_prompt(full_prompt)

                    result.total_cost_usd = 0.0
                    log.get_run_stats().record_orchestrator(0.0, "kimi_api")

                    if done_signal.called:
                        apply_done_signal(result, done_signal)
                        log.tprint(
                            f"✅ [orchestrator] cycle done: "
                            f"{done_signal.summary[:200]}",
                        )
                    else:
                        result.summary = response_text

                        # Nudge loop
                        nudges = 0
                        while not done_signal.called and nudges < _MAX_NUDGES:
                            nudges += 1
                            log.tprint(
                                f"🔄 [orchestrator] nudging to finish "
                                f"(attempt {nudges}/{_MAX_NUDGES})...",
                            )
                            response_text = await _stream_prompt(
                                "You must signal completion to end this cycle. "
                                "Use the appropriate done tool (goal_done, "
                                "end_cycle, or done) to summarize what you've "
                                "accomplished.",
                            )
                            if done_signal.called:
                                apply_done_signal(result, done_signal)
                                log.tprint(
                                    f"✅ [orchestrator] cycle done: "
                                    f"{done_signal.summary[:200]}",
                                )

                        if not done_signal.called:
                            result.summary = response_text
                            log.tprint(
                                "⏱️  [orchestrator] cycle ended without calling "
                                f"done after {_MAX_NUDGES} nudges",
                            )

                    log.emit(
                        "orchestrator_response",
                        orchestrator="kimi-code",
                        is_error=False,
                        result_text=(result.summary or "")[:2000],
                        done_called=done_signal.called,
                    )

                finally:
                    await session.close()

            # Run on dedicated thread+loop
            loop = asyncio.new_event_loop()
            thread = threading.Thread(target=loop.run_forever, daemon=True)
            thread.start()
            try:
                future = asyncio.run_coroutine_threadsafe(_run_cycle(), loop)
                future.result()
            finally:
                loop.call_soon_threadsafe(loop.stop)
                thread.join(timeout=5)
                if not thread.is_alive():
                    loop.close()

        return self._cycle_epilogue(
            result, cost_bucket="kimi_api", context="kimi-code finished.",
        )
