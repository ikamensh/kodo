"""Reusable test harness for observing kodo component interactions.

Provides instrumented wrappers that print every orchestrator ↔ agent exchange
in real-time, making the decision flow visible for analysis.

Usage as a library:
    from scripts.harness import InstrumentedTeam, print_banner, print_separator
"""

from __future__ import annotations

import functools
import json
import textwrap
import time
from pathlib import Path

# Ensure output is visible immediately (not buffered when piped/redirected)
print = functools.partial(print, flush=True)  # type: ignore[assignment]

from kodo.agent import Agent, AgentResult

# ── Pretty-printing helpers ──────────────────────────────────────────────

COLORS = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "blue": "\033[34m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "cyan": "\033[36m",
    "magenta": "\033[35m",
}


def _c(text: str, *styles: str) -> str:
    prefix = "".join(COLORS.get(s, "") for s in styles)
    return f"{prefix}{text}{COLORS['reset']}" if prefix else text


def print_banner(title: str) -> None:
    print(f"\n{_c('━' * 70, 'bold')}")
    print(f"  {_c(title, 'bold')}")
    print(_c("━" * 70, "bold"))


def print_separator(char: str = "─") -> None:
    print(_c(char * 70, "dim"))


def _truncate(text: str, max_chars: int = 5000) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n... ({len(text)} chars total)"


def _indent(text: str, prefix: str = "    │ ") -> str:
    return textwrap.indent(text, prefix)


# ── Instrumented Agent wrapper ───────────────────────────────────────────


class InstrumentedAgent:
    """Wraps a real Agent, printing every interaction."""

    def __init__(self, agent: Agent, name: str):
        self._agent = agent
        self._name = name
        # Proxy attributes the orchestrator reads
        self.description = agent.description
        self.session = agent.session
        self.max_turns = agent.max_turns
        self.timeout_s = agent.timeout_s

    def run(
        self,
        goal: str,
        project_dir: Path,
        *,
        new_conversation: bool = False,
        agent_name: str = "",
    ) -> AgentResult:
        label = agent_name or self._name
        print(f"\n{_c('─' * 70, 'dim')}")
        print(
            f"  {_c('>>>', 'blue', 'bold')} ORCHESTRATOR → {_c(label, 'cyan', 'bold')}"
        )
        if new_conversation:
            print(f"      {_c('(new_conversation=True)', 'yellow')}")
        print(_indent(goal))
        print(_c("─" * 70, "dim"))

        t0 = time.monotonic()
        result = self._agent.run(
            goal,
            project_dir,
            new_conversation=new_conversation,
            agent_name=agent_name,
        )
        elapsed = time.monotonic() - t0

        print(f"\n{_c('─' * 70, 'dim')}")
        print(
            f"  {_c('<<<', 'green', 'bold')} {_c(label, 'cyan', 'bold')} → ORCHESTRATOR  "
            f"{_c(f'({elapsed:.1f}s, {result.session_tokens:,} tok)', 'dim')}"
        )
        if result.context_reset:
            print(
                f"      {_c(f'[Context reset: {result.context_reset_reason}]', 'yellow')}"
            )
        text = result.text or "(empty)"
        # Highlight plan mode
        if "[PROPOSED PLAN]" in text:
            print(f"      {_c('★ PLAN MODE TRIGGERED', 'magenta', 'bold')}")
        print(_indent(_truncate(text)))
        print(_c("─" * 70, "dim"))

        return result

    def close(self) -> None:
        self._agent.close()


def instrument_team(
    team: dict[str, Agent],
) -> dict[str, InstrumentedAgent]:
    """Wrap every agent in a team with instrumentation."""
    return {name: InstrumentedAgent(agent, name) for name, agent in team.items()}


# ── Log replay ───────────────────────────────────────────────────────────


def print_log_summary(log_path: Path) -> None:
    """Print a condensed summary of orchestrator decisions from a JSONL log."""
    if not log_path.exists():
        return

    events = []
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))

    print_banner("Orchestrator Decision Log")

    step = 0
    for e in events:
        ev = e.get("event")
        t = e.get("t", 0)

        if ev == "orchestrator_tool_call":
            step += 1
            agent = e.get("agent", "?")
            task = e.get("task", "")
            new_conv = e.get("new_conversation", False)
            print(
                f"\n  {_c(f'[{t:.1f}s]', 'dim')} {_c(f'Step {step}', 'bold')}: "
                f"orchestrator → {_c(agent, 'cyan')}"
            )
            task_display = task if len(task) <= 200 else task[:200] + "..."
            print(_indent(task_display, "    "))
            if new_conv:
                print(f"    {_c('(new conversation)', 'yellow')}")

        elif ev == "orchestrator_tool_result":
            agent = e.get("agent", "?")
            elapsed = e.get("elapsed_s", 0)
            is_error = e.get("is_error", False)
            report = e.get("report", "")
            status = _c("ERROR", "yellow") if is_error else _c("ok", "green")
            print(
                f"  {_c(f'[{t:.1f}s]', 'dim')} {_c(agent, 'cyan')} responded "
                f"({elapsed:.1f}s) [{status}]"
            )
            # Show first 300 chars of response
            if report:
                short = report[:300] + ("..." if len(report) > 300 else "")
                print(_indent(short, "    "))

        elif ev == "orchestrator_done_attempt":
            success = e.get("success", False)
            summary = e.get("summary", "")[:200]
            label = (
                _c("DONE (success)", "green", "bold")
                if success
                else _c("DONE (failed)", "yellow", "bold")
            )
            print(f"\n  {_c(f'[{t:.1f}s]', 'dim')} {label}: {summary}")

        elif ev == "orchestrator_done_accepted":
            print(f"  {_c(f'[{t:.1f}s]', 'dim')} {_c('✓ ACCEPTED', 'green', 'bold')}")

        elif ev == "orchestrator_done_rejected":
            rejection = e.get("rejection", "")[:300]
            print(f"  {_c(f'[{t:.1f}s]', 'dim')} {_c('✗ REJECTED', 'yellow', 'bold')}")
            print(_indent(rejection, "    "))

        elif ev == "cycle_end":
            reason = e.get("reason", "?")
            finished = e.get("finished", False)
            print(
                f"\n  {_c(f'[{t:.1f}s]', 'dim')} Cycle ended: "
                f"reason={reason}, finished={finished}"
            )


def extract_orchestrator_choice(log_path: Path) -> str:
    """Parse the JSONL log for the orchestrator's second ask_worker call.

    The second tool call is typically where the orchestrator tells the worker
    which approach it chose and why. Returns the task text, or "" if not found.
    """
    if not log_path.exists():
        return ""

    tool_calls: list[str] = []
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("event") == "orchestrator_tool_call":
                task = e.get("task", "")
                if task:
                    tool_calls.append(task)

    # The second call is typically the choice; return it if available
    if len(tool_calls) >= 2:
        return tool_calls[1]
    return tool_calls[-1] if tool_calls else ""
