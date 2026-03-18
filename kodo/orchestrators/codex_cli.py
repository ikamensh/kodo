"""Orchestrator using Codex CLI with MCP tools for agent delegation."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from kodo import log
from kodo.orchestrators.base import (
    CycleResult,
    DoneSignal,
)
from kodo.orchestrators.cli_base import CliOrchestratorBase
from kodo.orchestrators.mcp_server import McpServerContext
from kodo.models import CODEX_DEFAULT


class CodexOrchestrator(CliOrchestratorBase):
    """Orchestrator backed by Codex CLI with MCP tools for agents."""

    _orchestrator_name = "codex"
    _cost_bucket = "codex_subscription"

    def __init__(self, model: str = CODEX_DEFAULT, system_prompt: str | None = None):
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
        log.tprint("🚀 [orchestrator] starting codex cycle...")

        # Codex only supports stdio MCP; use a bridge command
        bridge_cmd = ctx.stdio_bridge_cmd
        cmd = [
            "codex",
            "exec",
            full_prompt,
            "--full-auto",
            "--json",
            "-C",
            str(project_dir),
            "-m",
            self.model,
            "-c",
            f'mcp_servers.kodo_team.command="{bridge_cmd[0]}"',
            "-c",
            f"mcp_servers.kodo_team.args={json.dumps(bridge_cmd[1:])}",
        ]

        proc = None
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            assert proc.stdout is not None
            assert proc.stderr is not None

            # Parse JSONL output
            response_text = ""
            exchanges = 0
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg_type = msg.get("type") or msg.get("msg", {}).get("type", "")
                if msg_type == "task_started":
                    exchanges += 1
                elif msg_type == "message":
                    content = msg.get("msg", {}).get("content", "")
                    if content:
                        response_text = content
                elif msg_type == "task_complete":
                    response_text = msg.get("msg", {}).get("message", response_text)

                # Codex nests messages under "msg"
                inner = msg.get("msg", {})
                if inner.get("type") == "task_complete":
                    response_text = inner.get("message", response_text)

            proc.wait()
            stderr_text = proc.stderr.read()

            if proc.returncode != 0 and not response_text:
                response_text = (
                    stderr_text or f"codex exited with code {proc.returncode}"
                )

            result.exchanges = max(exchanges, 1)
            result.total_cost_usd = 0.0  # subscription-covered
            log.get_run_stats().record_orchestrator(0.0, "codex_subscription")

            self._apply_result(
                result, done_signal, response_text, is_error=proc.returncode != 0
            )

        finally:
            if proc is not None:
                if proc.poll() is None:
                    proc.kill()
                proc.wait()
