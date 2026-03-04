"""Orchestrator using Codex CLI with MCP tools for agent delegation."""

from __future__ import annotations

import json
import subprocess
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
from kodo.models import CODEX_DEFAULT
from kodo.summarizer import Summarizer


class CodexOrchestrator(OrchestratorBase):
    """Orchestrator backed by Codex CLI with MCP tools for agents."""

    def __init__(self, model: str = CODEX_DEFAULT, system_prompt: str | None = None):
        self.model = model
        self._orchestrator_name = "codex"
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
            orchestrator="codex",
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
            orchestrator_tag="codex",
            verification_state=verification_state,
            config=config,
        )

        result = CycleResult()
        prompt = build_cycle_prompt(goal, project_dir, prior_summary)
        full_prompt = f"{self._system_prompt}\n\n{prompt}"

        with McpServerContext(mcp) as ctx:
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

                apply_done_signal(result, done_signal)
                if not done_signal.called:
                    result.summary = response_text

                log.emit(
                    "orchestrator_response",
                    orchestrator="codex",
                    is_error=proc.returncode != 0,
                    result_text=response_text[:2000],
                    done_called=done_signal.called,
                )

                if done_signal.called:
                    log.tprint(
                        f"✅ [orchestrator] cycle done (done tool called): {done_signal.summary[:200]}",
                    )
                elif proc.returncode != 0:
                    log.tprint(f"⚠️  [orchestrator] codex error: {response_text[:200]}")
                else:
                    log.tprint("⏱️  [orchestrator] cycle ended without calling done")

            finally:
                if proc is not None:
                    if proc.poll() is None:
                        proc.kill()
                    proc.wait()

        return self._cycle_epilogue(
            result, cost_bucket="codex_subscription", context="codex finished.",
        )
