"""Orchestrator using Cursor CLI with MCP tools for agent delegation."""

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
from kodo.models import CURSOR_COMPOSER
from kodo.summarizer import Summarizer


class CursorOrchestrator(OrchestratorBase):
    """Orchestrator backed by Cursor CLI with MCP tools for agents."""

    def __init__(self, model: str = CURSOR_COMPOSER, system_prompt: str | None = None):
        self.model = model
        self._orchestrator_name = "cursor"
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
            orchestrator="cursor",
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
            orchestrator_tag="cursor",
            verification_state=verification_state,
            config=config,
        )

        result = CycleResult()
        prompt = build_cycle_prompt(goal, project_dir, prior_summary)
        full_prompt = f"{self._system_prompt}\n\n{prompt}"

        # Cursor reads MCP config from .cursor/mcp.json in the workspace
        mcp_config_dir = project_dir / ".cursor"
        mcp_config_path = mcp_config_dir / "mcp.json"
        original_config: str | None = None

        with McpServerContext(mcp) as ctx:
            # Write temporary MCP config
            try:
                if mcp_config_path.exists():
                    original_config = mcp_config_path.read_text(encoding="utf-8")
                    try:
                        existing = json.loads(original_config)
                    except (json.JSONDecodeError, ValueError):
                        existing = {}
                else:
                    existing = {}
                    mcp_config_dir.mkdir(parents=True, exist_ok=True)

                servers = existing.get("mcpServers", {})
                servers["kodo_team"] = {
                    "url": ctx.sse_url,
                    "transport": "sse",
                }
                existing["mcpServers"] = servers
                mcp_config_path.write_text(
                    json.dumps(existing, indent=2), encoding="utf-8",
                )
            except OSError as exc:
                raise RuntimeError(f"Failed to write Cursor MCP config: {exc}") from exc

            proc = None
            try:
                log.tprint("🚀 [orchestrator] starting cursor cycle...")

                cmd = [
                    "cursor-agent",
                    "-p",
                    "-f",
                    "--approve-mcps",
                    "--trust",
                    "--output-format",
                    "stream-json",
                    "--model",
                    self.model,
                    "--workspace",
                    str(project_dir),
                    full_prompt,
                ]

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

                # Parse stream-json output
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

                    if msg.get("type") == "result":
                        response_text = msg.get("result", "")
                    elif msg.get("type") == "tool_use":
                        exchanges += 1

                proc.wait()
                stderr_text = proc.stderr.read()

                if proc.returncode != 0 and not response_text:
                    response_text = (
                        stderr_text
                        or f"cursor-agent exited with code {proc.returncode}"
                    )

                result.exchanges = max(exchanges, 1)
                result.total_cost_usd = 0.0  # subscription-covered
                log.get_run_stats().record_orchestrator(0.0, "cursor_subscription")

                apply_done_signal(result, done_signal)
                if not done_signal.called:
                    result.summary = response_text

                log.emit(
                    "orchestrator_response",
                    orchestrator="cursor",
                    is_error=proc.returncode != 0,
                    result_text=response_text[:2000],
                    done_called=done_signal.called,
                )

                if done_signal.called:
                    log.tprint(
                        f"✅ [orchestrator] cycle done (done tool called): {done_signal.summary[:200]}",
                    )
                elif proc.returncode != 0:
                    log.tprint(f"⚠️  [orchestrator] cursor error: {response_text[:200]}")
                else:
                    log.tprint("⏱️  [orchestrator] cycle ended without calling done")

            finally:
                if proc is not None:
                    if proc.poll() is None:
                        proc.kill()
                    proc.wait()
                try:
                    if original_config is not None:
                        mcp_config_path.write_text(original_config, encoding="utf-8")
                    elif mcp_config_path.exists():
                        try:
                            current = json.loads(
                                mcp_config_path.read_text(encoding="utf-8"),
                            )
                            servers = current.get("mcpServers", {})
                            servers.pop("kodo_team", None)
                            if servers:
                                current["mcpServers"] = servers
                                mcp_config_path.write_text(
                                    json.dumps(current, indent=2), encoding="utf-8",
                                )
                            else:
                                mcp_config_path.unlink(missing_ok=True)
                        except (json.JSONDecodeError, OSError):
                            mcp_config_path.unlink(missing_ok=True)
                except OSError:
                    pass

        # Fallback summary
        if not result.finished and not result.summary:
            accumulated = self._summarizer.get_accumulated_summary()
            if accumulated:
                result.summary = (
                    f"[Cycle ended: cursor finished. Work so far:]\n{accumulated}"
                )
            else:
                result.summary = "[Cycle ended: no summary available — check logs.]"

        log.emit(
            "cycle_end",
            orchestrator="cursor",
            exchanges=result.exchanges,
            finished=result.finished,
            summary=result.summary,
            cost_usd=result.total_cost_usd,
            cost_bucket="cursor_subscription",
        )
        self._summarizer.clear()
        return result
