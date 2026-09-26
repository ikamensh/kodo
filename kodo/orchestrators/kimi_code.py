"""Kimi Code CLI orchestration through ACP and local MCP delegation tools."""

from __future__ import annotations

from pathlib import Path

from kodo import log
from kodo.models import KIMI_DEFAULT
from kodo.orchestrators.base import CycleResult, DoneSignal
from kodo.orchestrators.cli_base import CliOrchestratorBase
from kodo.orchestrators.mcp_server import McpServerContext
from kodo.sessions.kimi import KimiSession


class KimiCodeOrchestrator(CliOrchestratorBase):
    _orchestrator_name = "kimi-code"
    _cost_bucket = "kimi_cli"
    _emoji = "🌙"

    def __init__(self, model: str = KIMI_DEFAULT, system_prompt: str | None = None):
        super().__init__(model, system_prompt)

    def _run_subprocess(
        self,
        ctx: McpServerContext,
        full_prompt: str,
        project_dir: Path,
        max_exchanges: int,
        result: CycleResult,
        done_signal: DoneSignal,
    ) -> None:
        session = KimiSession(
            model=self.model,
            mcp_servers=[
                {
                    "type": "sse",
                    "name": "kodo_team",
                    "url": ctx.sse_url,
                    "headers": [],
                }
            ],
        )
        response_text = ""
        is_error = False
        prompt = full_prompt
        try:
            for _ in range(4):
                remaining = max_exchanges - result.exchanges
                if remaining <= 0:
                    break
                response = session.query(prompt, project_dir, max_turns=remaining)
                result.exchanges += response.turns or 1
                result.total_cost_usd += response.cost_usd or 0.0
                if response.text:
                    response_text = response.text
                is_error = response.is_error
                if done_signal.called or is_error or response.incomplete_reason:
                    break
                prompt = (
                    "Signal the cycle's outcome with goal_done, end_cycle, "
                    "raise_issue, or done. Summarize the work completed."
                )
        finally:
            session.close()

        log.get_run_stats().record_orchestrator(
            result.total_cost_usd, self._cost_bucket
        )
        self._apply_result(result, done_signal, response_text, is_error)
