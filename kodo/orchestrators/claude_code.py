"""Orchestrator using Claude Code session with in-process MCP tools."""

from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path

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
    build_mcp_server,
)


class ClaudeCodeOrchestrator(OrchestratorBase):
    """Orchestrator backed by a Claude Code session with MCP tools for agents."""

    def __init__(self, model: str = "opus", system_prompt: str | None = None):
        self.model = model
        self._orchestrator_name = "claude_code"
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
        browser_testing: bool = False,
        verifiers: dict | None = None,
        auto_commit: bool = False,
    ) -> CycleResult:
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage

        log.emit(
            "cycle_start",
            orchestrator="claude_code",
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
            orchestrator_tag="claude_code",
            verification_state=verification_state,
            browser_testing=browser_testing,
            verifiers=verifiers,
            auto_commit=auto_commit,
        )

        options = ClaudeAgentOptions(
            permission_mode="bypassPermissions",
            cwd=project_dir,
            disallowed_tools=["AskUserQuestion"],
            model=self.model,
            system_prompt=self._system_prompt,
            max_turns=max_exchanges,
            debug_stderr=None,
            stderr=lambda _: None,
            mcp_servers={
                "team": {
                    "type": "sdk",
                    "name": "team",
                    "instance": mcp._mcp_server,
                }
            },
        )

        result = CycleResult()

        prompt = build_cycle_prompt(goal, project_dir, prior_summary)

        # Run the entire connect→query→collect→disconnect lifecycle in a single
        # async function on a fresh event loop so anyio cancel scopes stay in
        # the same task throughout.
        _MAX_NUDGES = 3

        # Strip ANTHROPIC_API_KEY so the SDK subprocess uses the Claude.ai
        # subscription instead of API billing.  Mirrors ClaudeSession logic.
        saved_api_key = os.environ.pop("ANTHROPIC_API_KEY", None)

        async def _run_cycle():
            client = ClaudeSDKClient(options=options)
            try:
                await client.connect()
                log.tprint("🚀 [orchestrator] starting cycle...")
                await client.query(prompt)

                nudges = 0
                while True:
                    async for message in client.receive_response():
                        if isinstance(message, ResultMessage):
                            result.exchanges = result.exchanges + (
                                message.num_turns or 0
                            )
                            result.total_cost_usd = result.total_cost_usd + (
                                message.total_cost_usd or 0.0
                            )
                            log.get_run_stats().record_orchestrator(
                                message.total_cost_usd or 0.0,
                                "claude_subscription",
                            )
                            if done_signal.called:
                                result.summary = done_signal.summary or ""
                            elif message.is_error:
                                result.summary = (
                                    f"[Claude Code error] {message.result or ''}"
                                )
                            else:
                                result.summary = message.result or ""
                            log.emit(
                                "orchestrator_response",
                                orchestrator="claude_code",
                                is_error=message.is_error,
                                num_turns=message.num_turns,
                                cost_usd=message.total_cost_usd,
                                result_text=message.result,
                                done_called=done_signal.called,
                            )

                    if done_signal.called:
                        result.finished = True
                        result.success = done_signal.success
                        log.tprint(
                            f"✅ [orchestrator] cycle done (done tool called): "
                            f"{done_signal.summary[:200]}"
                        )
                        break

                    if message.is_error:
                        log.tprint(
                            f"⚠️  [orchestrator] Claude Code error: {message.result}"
                        )
                        break

                    nudges += 1
                    if nudges > _MAX_NUDGES:
                        log.tprint(
                            "⏱️  [orchestrator] cycle ended without calling "
                            f"done after {_MAX_NUDGES} nudges"
                        )
                        break

                    log.tprint(
                        f"🔄 [orchestrator] nudging to call done() "
                        f"(attempt {nudges}/{_MAX_NUDGES})..."
                    )
                    await client.query(
                        "You must call the done() tool to complete this cycle. "
                        "Summarize what you've accomplished and call done()."
                    )
            finally:
                try:
                    await client.disconnect()
                except RuntimeError as exc:
                    msg = str(exc).lower()
                    if "cancel" in msg or "anyio" in msg:
                        pass  # anyio cancel scope mismatch on cleanup — harmless
                    else:
                        log.tprint(f"[orchestrator] disconnect error: {exc}")
                        raise

        # Use a dedicated thread so we never collide with a caller's loop
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, daemon=True)
        thread.start()
        try:
            future = asyncio.run_coroutine_threadsafe(_run_cycle(), loop)
            future.result()  # blocks until cycle completes
        finally:
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=5)
            if not thread.is_alive():
                loop.close()
            # Restore ANTHROPIC_API_KEY so the orchestrator's own API calls
            # (summarizer, etc.) continue to work.
            if saved_api_key is not None:
                os.environ["ANTHROPIC_API_KEY"] = saved_api_key

        # If we ran out of turns without calling done, build a summary from
        # the summarizer's accumulated agent reports so the next cycle has context.
        if not result.finished and not result.summary:
            accumulated = self._summarizer.get_accumulated_summary()
            if accumulated:
                result.summary = (
                    f"[Cycle ended: hit turn limit after {result.exchanges} exchanges. "
                    f"Work so far:]\n{accumulated}"
                )
            else:
                result.summary = (
                    f"[Cycle ended: hit turn limit after {result.exchanges} exchanges. "
                    f"No summary available — check logs.]"
                )

        log.emit(
            "cycle_end",
            orchestrator="claude_code",
            exchanges=result.exchanges,
            finished=result.finished,
            summary=result.summary,
            cost_usd=result.total_cost_usd,
            cost_bucket="claude_subscription",
        )
        self._summarizer.clear()
        return result
