"""Orchestrator using Gemini CLI with MCP tools for agent delegation."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from kodo import log
from kodo.orchestrators.base import (
    ORCHESTRATOR_SYSTEM_PROMPT,
    CycleConfig,
    CycleResult,
    DoneSignal,
    McpServerContext,
    OrchestratorBase,
    TeamConfig,
    VerificationState,
    build_cycle_prompt,
    build_mcp_server,
)
from kodo.summarizer import Summarizer


class GeminiCliOrchestrator(OrchestratorBase):
    """Orchestrator backed by Gemini CLI with MCP tools for agents."""

    def __init__(
        self, model: str = "gemini-2.5-flash", system_prompt: str | None = None
    ):
        self.model = model
        self._orchestrator_name = "gemini-cli"
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
            orchestrator="gemini-cli",
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
            orchestrator_tag="gemini-cli",
            verification_state=verification_state,
            config=config,
        )

        result = CycleResult()
        prompt = build_cycle_prompt(goal, project_dir, prior_summary)
        full_prompt = f"{self._system_prompt}\n\n{prompt}"

        mcp_name = "kodo_team"

        with McpServerContext(mcp) as ctx:
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
                )
            except subprocess.CalledProcessError as exc:
                log.tprint(f"⚠️  [gemini-cli] failed to register MCP: {exc.stderr}")
                raise RuntimeError(
                    f"Failed to register MCP server: {exc.stderr}"
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

                    result.finished = done_signal.called
                    result.success = done_signal.success
                    result.summary = (
                        (done_signal.summary or "")
                        if done_signal.called
                        else response_text
                    )

                    log.emit(
                        "orchestrator_response",
                        orchestrator="gemini-cli",
                        is_error=proc.returncode != 0,
                        result_text=response_text[:2000],
                        done_called=done_signal.called,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                    )

                    if done_signal.called:
                        log.tprint(
                            f"✅ [orchestrator] cycle done (done tool called): {done_signal.summary[:200]}"
                        )
                    elif proc.returncode != 0:
                        log.tprint(
                            f"⚠️  [orchestrator] gemini-cli error: {response_text[:200]}"
                        )
                    else:
                        log.tprint("⏱️  [orchestrator] cycle ended without calling done")

            finally:
                # Always clean up MCP registration
                subprocess.run(
                    ["gemini", "mcp", "remove", mcp_name, "--scope", "project"],
                    capture_output=True,
                    text=True,
                    cwd=str(project_dir),
                )

        # Fallback summary from accumulated agent reports
        if not result.finished and not result.summary:
            accumulated = self._summarizer.get_accumulated_summary()
            if accumulated:
                result.summary = (
                    f"[Cycle ended: gemini-cli finished. Work so far:]\n{accumulated}"
                )
            else:
                result.summary = "[Cycle ended: no summary available — check logs.]"

        log.emit(
            "cycle_end",
            orchestrator="gemini-cli",
            exchanges=result.exchanges,
            finished=result.finished,
            summary=result.summary,
            cost_usd=result.total_cost_usd,
            cost_bucket="gemini_cli",
        )
        self._summarizer.clear()
        return result
