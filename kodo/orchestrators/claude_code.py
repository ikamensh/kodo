"""Orchestrator using Claude Code session with in-process MCP tools."""

from __future__ import annotations

import asyncio
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
    handle_agent_call,
    handle_done,
)


def _build_mcp_server(
    team: TeamConfig,
    project_dir: Path,
    summarizer: Summarizer,
    done_signal: DoneSignal,
    goal: str,
    verification_state: VerificationState | None = None,
    browser_testing: bool = False,
    verifiers: dict | None = None,
    auto_commit: bool = False,
):
    """Build a FastMCP server exposing each team agent as a tool."""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("team")

    for name, agent in team.items():

        def _make_handler(agent_name, agent_obj, agent_desc):
            def handler(task: str, new_conversation: bool = False) -> str:
                """Delegate a task to this agent."""
                return handle_agent_call(
                    agent_name,
                    agent_obj,
                    task,
                    project_dir,
                    summarizer,
                    new_conversation=new_conversation,
                    orchestrator_tag="claude_code",
                )

            handler.__name__ = f"ask_{agent_name}"
            handler.__doc__ = (
                f"Delegate a task to the {agent_name} agent.\n{agent_desc}"
            )
            return handler

        mcp.add_tool(
            _make_handler(name, agent, agent.description.strip()), name=f"ask_{name}"
        )

    def done(summary: str, success: bool) -> str:
        """Signal that the goal is complete. Runs automated verification first — \
if the tester or architect find issues, the call is rejected and you must fix them."""
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
            orchestrator_tag="claude_code",
            auto_commit=auto_commit,
        )

    mcp.add_tool(done)
    return mcp


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
        mcp = _build_mcp_server(
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
        async def _run_cycle():
            client = ClaudeSDKClient(options=options)
            try:
                await client.connect()
                log.tprint("🚀 [orchestrator] starting cycle...")
                await client.query(prompt)

                async for message in client.receive_response():
                    if isinstance(message, ResultMessage):
                        result.exchanges = message.num_turns or 0
                        result.total_cost_usd = message.total_cost_usd or 0.0
                        log.get_run_stats().record_orchestrator(
                            result.total_cost_usd, "claude_subscription"
                        )
                        result.finished = done_signal.called
                        result.success = done_signal.success
                        result.summary = (
                            (done_signal.summary or "")
                            if done_signal.called
                            else (message.result or "")
                        )
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
                            log.tprint(
                                f"✅ [orchestrator] cycle done (done tool called): {done_signal.summary[:200]}"
                            )
                        elif message.is_error:
                            log.tprint(f"⚠️  [orchestrator] error: {message.result}")
                        else:
                            log.tprint(
                                "⏱️  [orchestrator] cycle ended without calling done (hit turn limit?)"
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
