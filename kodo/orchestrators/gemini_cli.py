"""Orchestrator using Gemini CLI with MCP tools for agent delegation."""

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
from kodo.models import GEMINI_CLI_FLASH


class GeminiCliOrchestrator(CliOrchestratorBase):
    """Orchestrator backed by Gemini CLI with MCP tools for agents."""

    _orchestrator_name = "gemini-cli"
    _cost_bucket = "gemini_cli"

    def __init__(
        self, model: str = GEMINI_CLI_FLASH, system_prompt: str | None = None,
    ):
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
        mcp_name = "kodo_team"

        # Register MCP server with Gemini CLI (project-scoped)
        try:
            subprocess.run(
                [
                    "gemini",
                    "mcp",
                    "add",
                    mcp_name,
                    ctx.sse_url,
                    "-t",
                    "sse",
                    "--scope",
                    "project",
                    "--trust",
                ],
                capture_output=True,
                text=True,
                check=True,
                cwd=str(project_dir),
                timeout=30,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            log.tprint(f"⚠️  [gemini-cli] failed to register MCP: {exc.stderr}")
            raise RuntimeError(
                f"Failed to register MCP server: {exc.stderr}",
            ) from exc

        try:
            log.tprint("🚀 [orchestrator] starting gemini-cli cycle...")

            cmd = [
                "gemini",
                "-p",
                full_prompt,
                "-y",
                "--output-format",
                "json",
                "-m",
                self.model,
            ]

            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    cwd=str(project_dir),
                    timeout=max_exchanges * 120,  # generous timeout
                )
            except subprocess.TimeoutExpired as exc:
                result.finished = False
                result.exchanges = 1
                result.summary = (
                    f"Gemini CLI timed out after {exc.timeout}s. "
                    "Consider reducing scope or increasing max_exchanges."
                )
                log.tprint(f"⚠️  [orchestrator] gemini-cli timeout: {exc.timeout}s")
                log.emit(
                    "orchestrator_response",
                    orchestrator="gemini-cli",
                    is_error=True,
                    result_text=result.summary[:2000],
                    done_called=False,
                )
            else:
                # Parse response
                response_text = ""
                input_tokens = 0
                output_tokens = 0

                if proc.stdout.strip():
                    try:
                        data = json.loads(proc.stdout)
                        response_text = data.get("response", "")
                        stats_models = data.get("stats", {}).get("models", {})
                        for model_stats in stats_models.values():
                            tokens = model_stats.get("tokens", {})
                            input_tokens += tokens.get("prompt", 0)
                            output_tokens += tokens.get("candidates", 0)
                    except json.JSONDecodeError:
                        response_text = proc.stdout.strip()

                if proc.returncode != 0 and not response_text:
                    response_text = (
                        proc.stderr or f"gemini exited with code {proc.returncode}"
                    )

                result.exchanges = (
                    max(1, (input_tokens + output_tokens) // 4000)
                    if output_tokens
                    else 1
                )
                result.total_cost_usd = 0.0  # free tier
                log.get_run_stats().record_orchestrator(0.0, "gemini_cli")

                self._apply_result(result, done_signal, response_text, is_error=proc.returncode != 0)

        finally:
            # Always clean up MCP registration
            try:
                subprocess.run(
                    ["gemini", "mcp", "remove", mcp_name, "--scope", "project"],
                    capture_output=True,
                    text=True,
                    cwd=str(project_dir),
                    timeout=30,
                )
            except subprocess.TimeoutExpired:
                pass  # best-effort cleanup
