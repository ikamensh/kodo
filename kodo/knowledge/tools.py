"""Tools exposed to knowledge agents and the orchestrator.

Agent tools: web_search, fetch_page, compute, read_artifact, write_artifact
Orchestrator tools: ask_<agent>, finish
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable

from pydantic_ai import Tool

if TYPE_CHECKING:
    from kodo.knowledge.models import ConvergenceState, Workspace
    from kodo.orchestrators.base import DoneSignal
    from kodo.summarizer import Summarizer


# ---------------------------------------------------------------------------
# Workspace tools (available to agents)
# ---------------------------------------------------------------------------


def _make_read_artifact(workspace: "Workspace") -> Callable:
    def read_artifact(name: str) -> str:
        """Read a knowledge artifact from the shared workspace."""
        content = workspace.read(name)
        if content is None:
            available = workspace.list_artifacts()
            return f"Artifact '{name}' not found. Available: {available}"
        return content

    return read_artifact


def _make_write_artifact(workspace: "Workspace") -> Callable:
    def write_artifact(name: str, content: str) -> str:
        """Write or update a knowledge artifact in the shared workspace."""
        workspace.write(name, content)
        return f"Artifact '{name}' updated (v{workspace.artifacts[name].version})."

    return write_artifact


def _make_list_artifacts(workspace: "Workspace") -> Callable:
    def list_artifacts() -> str:
        """List all artifacts in the shared workspace."""
        arts = workspace.list_artifacts()
        if not arts:
            return "No artifacts yet."
        lines = []
        for name in arts:
            art = workspace.artifacts[name]
            lines.append(f"- {name} (v{art.version}, {len(art.content)} chars)")
        return "\n".join(lines)

    return list_artifacts


# ---------------------------------------------------------------------------
# Computation tool
# ---------------------------------------------------------------------------


def _make_compute() -> Callable:
    def compute(python_code: str) -> str:
        """Execute Python code and return stdout. For calculations,
        data analysis, or verification. The code runs in a subprocess
        with a 30-second timeout."""
        import subprocess

        try:
            result = subprocess.run(
                ["python3", "-c", python_code],
                capture_output=True,
                text=True,
                timeout=30,
            )
            output = result.stdout
            if result.returncode != 0:
                output += f"\nSTDERR: {result.stderr}"
            return output.strip() or "(no output)"
        except subprocess.TimeoutExpired:
            return "ERROR: Computation timed out after 30 seconds."

    return compute


# ---------------------------------------------------------------------------
# Orchestrator tools — agent delegation
# ---------------------------------------------------------------------------


def _make_knowledge_agent_handler(
    agent_name: str,
    agent_obj,
    workspace: "Workspace",
    summarizer: "Summarizer",
) -> tuple[Callable, str]:
    """Return (handler_fn, description) for an ask_<name> tool."""
    from kodo.orchestrators.base import handle_agent_call

    # Knowledge agents don't use project_dir in the same way.
    # We pass a dummy path; the real "project" is the workspace.
    dummy_dir = Path(".")

    def handler(task: str) -> str:
        # Inject workspace context into the task.
        overview = workspace.snapshot(max_chars_per_artifact=200)
        parts = [task, f"\n## Workspace overview\n{overview}"]

        # Include full content of key working artifacts
        for key_name in ("answer", "feedback", "counterarguments", "assessment"):
            content = workspace.read(key_name)
            if content:
                parts.append(f"\n## Full artifact: {key_name}\n{content}")

        # List reference materials and remind agent to read them
        ref_names = [n for n in workspace.list_artifacts() if n.startswith("ref_")]
        if ref_names:
            parts.append(
                f"\n## Reference materials available\n"
                f"These reference documents are in the workspace: {', '.join(ref_names)}.\n"
                f"Use read_artifact to access them. You MUST read and cite these "
                f"when making factual claims."
            )

        augmented_task = "\n".join(parts)
        return handle_agent_call(
            agent_name,
            agent_obj,
            augmented_task,
            dummy_dir,
            summarizer,
            new_conversation=False,
            dead_workers=set(),
            total_workers=1,
        )

    desc = (
        f"Delegate a task to the {agent_name} agent.\n{agent_obj.description.strip()}"
    )
    return handler, desc


# ---------------------------------------------------------------------------
# Finish tool
# ---------------------------------------------------------------------------


def _make_finish(
    done_signal: "DoneSignal",
    workspace: "Workspace",
    convergence: "ConvergenceState",
) -> Callable:
    def finish(final_summary: str) -> str:
        """Signal that the knowledge task is complete. The answer artifact
        should contain the final answer before calling this."""
        from kodo import log

        answer = workspace.read("answer") or final_summary
        done_signal.called = True
        done_signal.summary = answer
        done_signal.success = True
        done_signal.terminal = "goal_done"

        log.tprint(
            f"[knowledge] Finished (confidence={convergence.confidence:.2f}, "
            f"verdict={convergence.verdict_type}): {final_summary[:200]}"
        )
        return "Knowledge task complete."

    return finish


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------


def build_knowledge_tools(
    team: dict,
    workspace: "Workspace",
    convergence: "ConvergenceState",
    summarizer: "Summarizer",
    done_signal: "DoneSignal",
) -> list[Tool]:
    """Build pydantic-ai tools for the knowledge orchestrator."""
    tools: list[Tool] = []

    # Agent delegation tools
    for name, agent in team.items():
        handler, desc = _make_knowledge_agent_handler(
            name,
            agent,
            workspace,
            summarizer,
        )
        tools.append(
            Tool(handler, name=f"ask_{name}", description=desc, takes_ctx=False)
        )

    # Workspace tools (orchestrator can also read/write directly)
    tools.append(
        Tool(
            _make_read_artifact(workspace),
            name="read_artifact",
            description="Read a knowledge artifact from the shared workspace.",
            takes_ctx=False,
        )
    )
    tools.append(
        Tool(
            _make_write_artifact(workspace),
            name="write_artifact",
            description="Write or update a knowledge artifact.",
            takes_ctx=False,
        )
    )
    tools.append(
        Tool(
            _make_list_artifacts(workspace),
            name="list_artifacts",
            description="List all artifacts in the workspace.",
            takes_ctx=False,
        )
    )

    # Finish
    tools.append(
        Tool(
            _make_finish(done_signal, workspace, convergence),
            name="finish",
            description="Signal that the knowledge task is complete.",
            takes_ctx=False,
        )
    )

    return tools
