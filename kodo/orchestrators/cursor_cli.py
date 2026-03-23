"""Orchestrator using Cursor CLI with MCP tools for agent delegation."""

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
from kodo.models import CURSOR_COMPOSER


class CursorOrchestrator(CliOrchestratorBase):
    """Orchestrator backed by Cursor CLI with MCP tools for agents."""

    _orchestrator_name = "cursor"
    _cost_bucket = "cursor_subscription"
    _emoji = "⚡"

    def __init__(self, model: str = CURSOR_COMPOSER, system_prompt: str | None = None):
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
        # Cursor reads MCP config from .cursor/mcp.json in the workspace
        mcp_config_dir = project_dir / ".cursor"
        mcp_config_path = mcp_config_dir / "mcp.json"
        original_config: str | None = None

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
                json.dumps(existing, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            raise RuntimeError(f"Failed to write Cursor MCP config: {exc}") from exc

        proc = None
        try:
            log.tprint(f"{self._emoji} [orchestrator] starting cursor cycle...")

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
                    stderr_text or f"cursor-agent exited with code {proc.returncode}"
                )

            result.exchanges = max(exchanges, 1)
            result.total_cost_usd = 0.0  # subscription-covered
            log.get_run_stats().record_orchestrator(0.0, "cursor_subscription")

            self._apply_result(
                result, done_signal, response_text, is_error=proc.returncode != 0
            )

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
                                json.dumps(current, indent=2),
                                encoding="utf-8",
                            )
                        else:
                            mcp_config_path.unlink(missing_ok=True)
                    except (json.JSONDecodeError, OSError):
                        mcp_config_path.unlink(missing_ok=True)
            except OSError:
                pass
