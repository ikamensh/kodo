"""Agent dispatch helper used by orchestrator tool handlers."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from kodo.formatting import CYAN as _CYAN, DIM as _DIM, RESET as _RESET
from kodo.orchestrators.types import FatalAgentError

if TYPE_CHECKING:
    from kodo.agent import Agent

# Patterns that indicate unrecoverable errors — retrying won't help.
_FATAL_ERROR_PATTERNS = re.compile(
    r"Subscription/billing issue|Authentication failed|Binary not working|"
    r"Rate limit exceeded|Model not found|model_not_available|Permission denied",
    re.IGNORECASE,
)


def handle_agent_call(
    agent_name: str,
    agent_obj: "Agent",
    task: str,
    project_dir: Path,
    summarizer,
    *,
    new_conversation: bool = False,
    cycle_log: list[str] | None = None,
    orchestrator_tag: str | None = None,
    dead_workers: set[str] | None = None,
    total_workers: int = 0,
) -> str:
    """Run an agent and return its report (or error string on crash).

    *cycle_log*: if provided, task/result snippets are appended (used by
    ApiOrchestrator for fallback model context).
    *orchestrator_tag*: if set, included as ``orchestrator=`` in log events.
    """
    from kodo import log

    tag = {"orchestrator": orchestrator_tag} if orchestrator_tag else {}

    log.tprint(f"🔧 [orchestrator] → {_CYAN}{agent_name}{_RESET}: {task[:100]}...")
    if new_conversation:
        log.tprint("   (new conversation)")

    if cycle_log is not None:
        cycle_log.append(f"→ {agent_name}: {task[:200]}")

    log.emit(
        "orchestrator_tool_call",
        **tag,
        agent=agent_name,
        task=task,
        new_conversation=new_conversation,
    )

    try:
        agent_result = agent_obj.run(
            task,
            project_dir,
            new_conversation=new_conversation,
            agent_name=agent_name,
        )
    except Exception as exc:
        error_msg = f"💥 {_CYAN}{agent_name}{_RESET} crashed: {_DIM}{type(exc).__name__}: {exc}{_RESET}"
        log.emit("agent_crash", agent=agent_name, error=str(exc))
        log.tprint(error_msg)
        if cycle_log is not None:
            cycle_log.append(f"← {agent_name}: {error_msg}")
        return error_msg

    report = agent_result.format_report()[:10000]
    log.emit(
        "orchestrator_tool_result",
        **tag,
        agent=agent_name,
        elapsed_s=agent_result.elapsed_s,
        is_error=agent_result.is_error,
        context_reset=agent_result.context_reset,
        session_tokens=agent_result.session_tokens,
        report=report,
    )

    icon = "⚠️" if agent_result.is_error else "✅"
    done_msg = (
        f"{icon} [{_CYAN}{agent_name}{_RESET}] done ({agent_result.elapsed_s:.1f}s)"
    )
    if agent_obj.session.cost_bucket != "cursor_subscription":
        done_msg += f" | session: {agent_result.session_tokens:,} tokens"
    log.tprint(done_msg)
    if agent_result.is_error:
        err_text = (agent_result.text or "unknown error")[:200]
        log.tprint(f"⚠️  [{_CYAN}{agent_name}{_RESET}] error: {_DIM}{err_text}{_RESET}")
        # Track workers with fatal (unrecoverable) errors
        if dead_workers is not None and _FATAL_ERROR_PATTERNS.search(err_text):
            dead_workers.add(agent_name)
            if len(dead_workers) >= total_workers:
                raise FatalAgentError(
                    f"All workers failed: {', '.join(sorted(dead_workers))}"
                )
    if agent_result.context_reset:
        log.tprint(
            f"🔄 [{_CYAN}{agent_name}{_RESET}] context reset: {agent_result.context_reset_reason}",
        )

    log.print_stats_table()

    if cycle_log is not None:
        cycle_log.append(f"← {agent_name}: {report[:500]}")

    summarizer.summarize(agent_name, task, report)
    return report
