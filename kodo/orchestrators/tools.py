"""Orchestrator tool definitions.

Provides tool factories for both pydantic-ai (ApiOrchestrator) and
MCP (ClaudeCode, Cursor, Gemini, Codex orchestrators).  Tool set is
controlled by ``CycleConfig.done_mode``:

- ``"legacy"``: single ``done(summary, success)`` with regex verification gate
- ``"new"``: three tools — ``goal_done``, ``end_cycle``, ``raise_issue``
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from kodo.orchestrators.base import (
        CycleConfig,
        DoneSignal,
        TeamConfig,
    )
    from kodo.orchestrators.verification import VerificationState
    from kodo.summarizer import Summarizer


# ---------------------------------------------------------------------------
# Agent delegation handlers
# ---------------------------------------------------------------------------


def _make_agent_handler(
    agent_name: str,
    agent_obj,
    project_dir: Path,
    summarizer: Summarizer,
    *,
    dead_workers: set[str],
    total_workers: int,
    orchestrator_tag: str | None = None,
) -> tuple[Callable, str]:
    """Return (handler_fn, description) for one ask_<name> tool."""
    from kodo.orchestrators.base import handle_agent_call

    def handler(task: str, new_conversation: bool = False) -> str:
        return handle_agent_call(
            agent_name,
            agent_obj,
            task,
            project_dir,
            summarizer,
            new_conversation=new_conversation,
            orchestrator_tag=orchestrator_tag,
            dead_workers=dead_workers,
            total_workers=total_workers,
        )

    desc = f"Delegate a task to the {agent_name} agent.\n{agent_obj.description.strip()}"
    return handler, desc


# ---------------------------------------------------------------------------
# Legacy done tool (single done with optional verification gate)
# ---------------------------------------------------------------------------


def _make_legacy_done(
    done_signal: DoneSignal,
    goal: str,
    team: TeamConfig,
    project_dir: Path,
    *,
    verification_state: VerificationState | None = None,
    orchestrator_tag: str | None = None,
    config: CycleConfig | None = None,
) -> Callable:
    """Return a ``done(summary, success)`` handler that routes through ``handle_done``."""
    from kodo.orchestrators.verification import handle_done

    def done(summary: str, success: bool) -> str:
        """Signal that the goal is complete (or cannot be completed).
        This triggers automated verification by the tester and architect.
        If they find issues, the call is rejected and you must fix them first."""
        return handle_done(
            summary,
            success,
            done_signal,
            goal,
            team,
            project_dir,
            verification_state=verification_state,
            orchestrator_tag=orchestrator_tag,
            config=config,
        )

    return done


# ---------------------------------------------------------------------------
# New done tools (goal_done / end_cycle / raise_issue)
# ---------------------------------------------------------------------------


def _make_new_done_tools(
    done_signal: DoneSignal,
    goal: str,
    team: TeamConfig,
    project_dir: Path,
    *,
    orchestrator_tag: str | None = None,
    config: CycleConfig | None = None,
) -> tuple[Callable, Callable, Callable]:
    """Return ``(goal_done, end_cycle, raise_issue)`` handlers."""
    from kodo import log
    from kodo.orchestrators.base import _auto_commit

    tag = {"orchestrator": orchestrator_tag} if orchestrator_tag else {}

    def goal_done(summary: str) -> str:
        """Signal that the goal has been fully achieved.
        Before calling this, verify the work by asking your tester agent(s)."""
        done_signal.called = True
        done_signal.summary = summary
        done_signal.success = True
        done_signal.terminal = "goal_done"

        if config and config.auto_commit:
            _auto_commit(team, project_dir, summary)

        log.emit("orchestrator_done_accepted", **tag, summary=summary)
        log.tprint(f"🎉 [orchestrator] goal_done: {summary[:200]}")
        return "Goal accepted."

    def end_cycle(summary: str) -> str:
        """End this cycle. The run will continue on the next cycle with your summary as context.
        Use this when you have made progress but need more work to achieve the goal."""
        done_signal.called = True
        done_signal.summary = summary
        done_signal.success = False
        done_signal.terminal = "end_cycle"

        log.emit("orchestrator_end_cycle", **tag, summary=summary)
        log.tprint(f"🔄 [orchestrator] end_cycle: {summary[:200]}")
        return "Cycle ended. Will continue next cycle."

    def raise_issue(reason: str) -> str:
        """Signal a fatal issue that prevents this goal from being completed.
        Use this for broken requirements, missing credentials, or repeated worker failures.
        The entire run will stop."""
        done_signal.called = True
        done_signal.summary = reason
        done_signal.success = False
        done_signal.terminal = "raise_issue"

        log.emit("orchestrator_raise_issue", **tag, reason=reason)
        log.tprint(f"🚨 [orchestrator] raise_issue: {reason[:200]}")
        return "Issue raised. Run stopped."

    return goal_done, end_cycle, raise_issue


# ---------------------------------------------------------------------------
# Public factory: pydantic-ai tools
# ---------------------------------------------------------------------------


def build_pydantic_tools(
    team: TeamConfig,
    project_dir: Path,
    summarizer: Summarizer,
    done_signal: DoneSignal,
    goal: str,
    verification_state: VerificationState | None = None,
    config: CycleConfig | None = None,
) -> list:
    """Build pydantic-ai ``Tool`` objects for the API orchestrator."""
    from pydantic_ai import Tool

    from kodo.orchestrators.base import CycleConfig as _CycleConfig

    if config is None:
        config = _CycleConfig()

    tools: list[Tool] = []
    dead_workers: set[str] = set()
    total_workers = len(team)

    # Agent delegation tools
    for name, agent in team.items():
        handler, desc = _make_agent_handler(
            name,
            agent,
            project_dir,
            summarizer,
            dead_workers=dead_workers,
            total_workers=total_workers,
        )
        tools.append(Tool(handler, name=f"ask_{name}", description=desc, takes_ctx=False))

    # Done tool(s)
    if config.done_mode == "legacy":
        done_fn = _make_legacy_done(
            done_signal, goal, team, project_dir,
            verification_state=verification_state,
            config=config,
        )
        tools.append(Tool(done_fn, takes_ctx=False))
    else:
        gd, ec, ri = _make_new_done_tools(
            done_signal, goal, team, project_dir,
            config=config,
        )
        tools.append(Tool(gd, takes_ctx=False))
        tools.append(Tool(ec, takes_ctx=False))
        tools.append(Tool(ri, takes_ctx=False))

    return tools


# ---------------------------------------------------------------------------
# Public factory: MCP tools
# ---------------------------------------------------------------------------


def add_tools_to_mcp(
    mcp,
    team: TeamConfig,
    project_dir: Path,
    summarizer: Summarizer,
    done_signal: DoneSignal,
    goal: str,
    orchestrator_tag: str = "unknown",
    verification_state: VerificationState | None = None,
    config: CycleConfig | None = None,
) -> None:
    """Populate a FastMCP instance with agent delegation and done tools."""
    from kodo.orchestrators.base import CycleConfig as _CycleConfig

    if config is None:
        config = _CycleConfig()

    dead_workers: set[str] = set()
    total_workers = len(team)

    # Agent delegation tools
    for name, agent in team.items():
        handler, desc = _make_agent_handler(
            name,
            agent,
            project_dir,
            summarizer,
            dead_workers=dead_workers,
            total_workers=total_workers,
            orchestrator_tag=orchestrator_tag,
        )
        handler.__name__ = f"ask_{name}"
        handler.__doc__ = desc
        mcp.add_tool(handler, name=f"ask_{name}")

    # Done tool(s)
    if config.done_mode == "legacy":
        done_fn = _make_legacy_done(
            done_signal, goal, team, project_dir,
            verification_state=verification_state,
            orchestrator_tag=orchestrator_tag,
            config=config,
        )
        mcp.add_tool(done_fn)
    else:
        gd, ec, ri = _make_new_done_tools(
            done_signal, goal, team, project_dir,
            orchestrator_tag=orchestrator_tag,
            config=config,
        )
        mcp.add_tool(gd)
        mcp.add_tool(ec)
        mcp.add_tool(ri)
