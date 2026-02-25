"""Orchestrator protocol and shared types."""

from __future__ import annotations

import asyncio
import shutil
import socket
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from kodo.agent import Agent


# Team is just a named dict of agents
TeamConfig = dict[str, Agent]


@dataclass
class GoalStage:
    """One stage in a multi-stage goal plan."""

    index: int  # 1-based
    name: str  # short label
    description: str  # full prose for orchestrator
    acceptance_criteria: str  # verifiable "done" definition
    browser_testing: bool = False  # whether this stage needs browser verification
    parallel_group: int | None = None  # stages with same group run concurrently


@dataclass
class GoalPlan:
    """Ordered list of stages with shared architectural context."""

    context: str  # shared architectural context
    stages: list[GoalStage]


@dataclass
class StageResult:
    """Groups cycles and outcome for a single stage."""

    stage_index: int
    stage_name: str
    cycles: list["CycleResult"] = field(default_factory=list)
    finished: bool = False
    summary: str = ""


@dataclass
class CycleResult:
    """Result of a single orchestration cycle (one 'day of work')."""

    exchanges: int = 0
    total_cost_usd: float = 0.0
    finished: bool = False
    success: bool = False
    summary: str = ""
    stage_index: int | None = None


@dataclass
class RunResult:
    """Result of a full multi-cycle run."""

    cycles: list[CycleResult] = field(default_factory=list)
    stage_results: list[StageResult] = field(default_factory=list)

    @property
    def total_exchanges(self) -> int:
        return sum(c.exchanges for c in self.cycles)

    @property
    def total_cost_usd(self) -> float:
        return sum(c.total_cost_usd for c in self.cycles)

    @property
    def finished(self) -> bool:
        # In staged runs, a crashed stage may have 0 cycles — check
        # stage_results to avoid reporting "finished" when the last
        # stage failed.
        if self.stage_results:
            return self.stage_results[-1].finished
        return bool(self.cycles) and self.cycles[-1].finished

    @property
    def summary(self) -> str:
        return self.cycles[-1].summary if self.cycles else ""


# NOTE: If the orchestrator still over-specifies tasks despite this prompt,
# the next step is to insert an LLM layer between the orchestrator and the
# team that strips implementation details from directives, passing through
# only the WHAT/WHY and letting agents decide HOW.
ORCHESTRATOR_SYSTEM_PROMPT = """
You are an orchestrator. Get the user's desired outcome.

Your agents have full codebase access and are expert coders. Every implementation
detail you specify risks making the result worse. Tell them WHAT, never HOW.

1. Define desired outcome (user-facing behavior, not code structure).
2. Delegate as small, verifiable goals.
3. Verify results match intent. Commit good work, revert bad iterations.

The team shares .kodo/architecture.md — the architect updates it, workers read it.

You decide: priorities, scope, what "done" looks like, when to revert.
Agents decide: code structure, libraries, patterns, file organization.
""".strip()


# ---------------------------------------------------------------------------
# Shared handler functions — used by both ApiOrchestrator and ClaudeCodeOrchestrator
# ---------------------------------------------------------------------------


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
) -> str:
    """Run an agent and return its report (or error string on crash).

    *cycle_log*: if provided, task/result snippets are appended (used by
    ApiOrchestrator for fallback model context).
    *orchestrator_tag*: if set, included as ``orchestrator=`` in log events.
    """
    from kodo import log

    tag = {"orchestrator": orchestrator_tag} if orchestrator_tag else {}

    log.tprint(f"🔧 [orchestrator] → {agent_name}: {task[:100]}...")
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
        error_msg = f"💥 {agent_name} crashed: {type(exc).__name__}: {exc}"
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

    done_msg = f"✅ [{agent_name}] done ({agent_result.elapsed_s:.1f}s)"
    if agent_obj.session.cost_bucket != "cursor_subscription":
        done_msg += f" | session: {agent_result.session_tokens:,} tokens"
    log.tprint(done_msg)
    if agent_result.is_error:
        log.tprint(f"⚠️  [{agent_name}] reported error")
    if agent_result.context_reset:
        log.tprint(
            f"🔄 [{agent_name}] context reset: {agent_result.context_reset_reason}"
        )

    log.print_stats_table()

    if cycle_log is not None:
        cycle_log.append(f"← {agent_name}: {report[:500]}")

    summarizer.summarize(agent_name, task, report)
    return report


def _auto_commit(
    team: TeamConfig,
    project_dir: Path,
    summary: str,
) -> None:
    """Dispatch a worker to commit completed work after verification passes.

    Non-fatal: logs warnings on failure but never raises.
    """
    from kodo import log

    # Find a worker: prefer worker_fast, fall back to worker_smart, then any
    worker = (
        team.get("worker_fast")
        or team.get("worker_smart")
        or next((a for a in team.values()), None)
    )
    if worker is None:
        log.tprint("📝 [auto-commit] no worker available, skipping")
        log.emit("auto_commit_skip", reason="no_worker")
        return

    worker_name = next((n for n, a in team.items() if a is worker), "worker")

    directive = (
        "Review `git diff` and `git status`. Stage the relevant changed files "
        "and commit with a clear, concise message describing what was accomplished. "
        "Do NOT push. Do NOT commit unrelated or generated files.\n\n"
        f"Summary of completed work:\n{summary}"
    )

    log.tprint(f"📝 [auto-commit] dispatching {worker_name} to commit...")
    log.emit("auto_commit_start", worker=worker_name)

    try:
        result = worker.run(
            directive,
            project_dir,
            new_conversation=True,
            agent_name=f"{worker_name}_auto_commit",
        )
        report = (result.text or "")[:2000]
        log.emit("auto_commit_done", worker=worker_name, report=report)
        log.tprint(f"📝 [auto-commit] {worker_name} finished")
    except Exception as exc:
        log.emit("auto_commit_error", worker=worker_name, error=str(exc))
        log.tprint(f"📝 [auto-commit] {worker_name} failed: {exc}")


def handle_done(
    summary: str,
    success: bool,
    done_signal: "DoneSignal",
    goal: str,
    team: TeamConfig,
    project_dir: Path,
    *,
    verification_state: "VerificationState | None" = None,
    browser_testing: bool = False,
    verifiers: dict | None = None,
    orchestrator_tag: str | None = None,
    auto_commit: bool = False,
) -> str:
    """Shared done-handler logic for both orchestrators.

    Returns the string result to pass back to the orchestrator model.
    """
    from kodo import log

    tag = {"orchestrator": orchestrator_tag} if orchestrator_tag else {}

    log.emit("orchestrator_done_attempt", **tag, summary=summary, success=success)
    log.tprint(f"🏁 [orchestrator] DONE requested (success={success}): {summary}")

    if not success:
        done_signal.called = True
        done_signal.summary = summary
        done_signal.success = False
        return "Acknowledged (marked as unsuccessful)."

    rejection = verify_done(
        goal,
        summary,
        team,
        project_dir,
        state=verification_state,
        browser_testing=browser_testing,
        verifiers=verifiers,
    )
    if rejection:
        log.emit("orchestrator_done_rejected", **tag, rejection=rejection[:5000])
        log.tprint("❌ [done] REJECTED — verification found issues")
        return rejection

    # Auto-commit after successful verification
    if auto_commit:
        _auto_commit(team, project_dir, summary)

    done_signal.called = True
    done_signal.summary = summary
    done_signal.success = True
    log.emit("orchestrator_done_accepted", **tag, summary=summary)
    log.tprint("🎉 [done] ACCEPTED — all checks pass")
    return "Verified and accepted. All checks pass."


# Verification signal strings — used in agent prompts and _check_passed()
PASS_SIGNAL = "ALL CHECKS PASS"
MINOR_SIGNAL = "MINOR ISSUES FIXED"


class DoneSignal:
    """Shared mutable to communicate between the ``done`` tool and the cycle loop."""

    def __init__(self) -> None:
        self.called = False
        self.summary = ""
        self.success = False


def build_cycle_prompt(goal: str, project_dir: Path, prior_summary: str = "") -> str:
    """Build the user-turn prompt sent to the orchestrator each cycle."""
    prompt = f"# Goal\n\n{goal}\n\nProject directory: {project_dir}"
    if prior_summary:
        prompt += (
            f"\n\n# Previous progress\n\n{prior_summary}"
            "\n\nContinue working toward the goal."
        )
    return prompt


def build_mcp_server(
    team: TeamConfig,
    project_dir: Path,
    summarizer,
    done_signal: "DoneSignal",
    goal: str,
    orchestrator_tag: str = "unknown",
    verification_state: "VerificationState | None" = None,
    browser_testing: bool = False,
    verifiers: dict | None = None,
    auto_commit: bool = False,
):
    """Build a FastMCP server exposing each team agent as a tool.

    Shared by all orchestrators that use MCP (ClaudeCode, GeminiCli, Codex, Cursor).
    """
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("team")

    for name, agent in team.items():

        def _make_handler(agent_name, agent_obj, agent_desc):
            def handler(task: str, new_conversation: bool = False) -> str:
                """Delegate a task to this agent."""
                return handle_agent_call(
                    agent_name,
                    agent_obj,
                    task,
                    project_dir,
                    summarizer,
                    new_conversation=new_conversation,
                    orchestrator_tag=orchestrator_tag,
                )

            handler.__name__ = f"ask_{agent_name}"
            handler.__doc__ = (
                f"Delegate a task to the {agent_name} agent.\n{agent_desc}"
            )
            return handler

        mcp.add_tool(
            _make_handler(name, agent, agent.description.strip()), name=f"ask_{name}"
        )

    def done(summary: str, success: bool) -> str:
        """Signal that the goal is complete. Runs automated verification first — \
if the tester or architect find issues, the call is rejected and you must fix them."""
        return handle_done(
            summary,
            success,
            done_signal,
            goal,
            team,
            project_dir,
            verification_state=verification_state,
            browser_testing=browser_testing,
            verifiers=verifiers,
            orchestrator_tag=orchestrator_tag,
            auto_commit=auto_commit,
        )

    mcp.add_tool(done)
    return mcp


_STDIO_BRIDGE_SCRIPT = """\
import asyncio, sys, json
from mcp.client.sse import sse_client
from mcp.server.stdio import stdio_server

async def main():
    async with sse_client("{url}") as (read_sse, write_sse):
        async with stdio_server() as (read_stdio, write_stdio):
            async def sse_to_stdio():
                async for msg in read_sse:
                    await write_stdio.send(msg)
            async def stdio_to_sse():
                async for msg in read_stdio:
                    await write_sse.send(msg)
            await asyncio.gather(sse_to_stdio(), stdio_to_sse())

asyncio.run(main())
"""


class McpServerContext:
    """Runs a FastMCP server on a random local port for CLI orchestrators.

    Usage::

        mcp = build_mcp_server(team, ...)
        with McpServerContext(mcp) as ctx:
            url = ctx.sse_url  # e.g. "http://127.0.0.1:54321/sse"
            # ... spawn CLI process that connects to this URL ...
    """

    def __init__(self, mcp) -> None:
        self._mcp = mcp
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self.port: int = 0
        self.sse_url: str = ""

    def __enter__(self) -> "McpServerContext":
        # Find a free port
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]

        self.sse_url = f"http://127.0.0.1:{self.port}/sse"
        self._mcp.settings.host = "127.0.0.1"
        self._mcp.settings.port = self.port

        ready = threading.Event()

        def _run():
            import uvicorn

            config = uvicorn.Config(
                self._mcp.sse_app(),
                host="127.0.0.1",
                port=self.port,
                log_level="error",
            )
            server = uvicorn.Server(config)
            self._server = server

            # Signal ready once server is started
            original_startup = server.startup

            async def _startup(sockets=None):
                await original_startup(sockets)
                ready.set()

            server.startup = _startup

            self._loop = asyncio.new_event_loop()
            self._loop.run_until_complete(server.serve())

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

        # Wait for the server to be ready (up to 10s)
        if not ready.wait(timeout=10):
            raise RuntimeError("MCP server failed to start within 10s")

        return self

    @property
    def stdio_bridge_cmd(self) -> list[str]:
        """Return a command that bridges stdio↔SSE for this MCP server.

        Useful for Codex CLI which only supports stdio MCP transport.
        Uses ``npx mcp-remote`` if available, or falls back to a Python script.
        """
        import shutil
        import sys

        if shutil.which("npx"):
            return ["npx", "-y", "mcp-remote", self.sse_url]
        return [
            sys.executable,
            "-u",
            "-c",
            _STDIO_BRIDGE_SCRIPT.format(url=self.sse_url),
        ]

    def __exit__(self, *exc) -> None:
        if hasattr(self, "_server"):
            self._server.should_exit = True
        if self._thread:
            self._thread.join(timeout=5)


@dataclass
class VerificationState:
    """Tracks verification attempts within a single cycle.

    Created fresh each ``cycle()`` call. First ``done()`` resets verifier
    sessions for a clean baseline; subsequent calls reuse the session so
    verifiers have persistent context from prior reviews.
    """

    done_attempt: int = 0


def _check_passed(report: str) -> bool:
    """Return True if a verifier report signals acceptance."""
    upper = report.upper()
    return PASS_SIGNAL in upper or MINOR_SIGNAL in upper


def verify_done(
    goal: str,
    summary: str,
    team: TeamConfig,
    project_dir: Path,
    *,
    state: VerificationState | None = None,
    browser_testing: bool = False,
    verifiers: dict | None = None,
) -> str | None:
    """Run tester + architect to verify the goal is met.

    Returns None if all checks pass, or a rejection message with issues found.
    *browser_testing*: when False, browser testers are skipped even if configured.
    *verifiers*: optional dict with ``testers``, ``browser_testers``, ``reviewers``
    lists referencing agent keys in *team*.  When ``None``, falls back to legacy
    hardcoded key lookups (``"tester"``, ``"tester_browser"``, ``"architect"``).
    """
    from kodo import log

    if state is None:
        state = VerificationState()

    state.done_attempt += 1
    attempt = state.done_attempt
    reset_session = attempt == 1

    issues = []
    verification_prompt = (
        f"The orchestrator claims the following goal is complete:\n\n"
        f"# Goal\n{goal}\n\n"
        f"# Orchestrator's summary\n{summary}\n\n"
    )

    # Resolve which agents to use for each verifier role
    if verifiers is not None:
        tester_keys = verifiers.get("testers", [])
        browser_tester_keys = verifiers.get("browser_testers", [])
        reviewer_keys = verifiers.get("reviewers", [])
    else:
        # Legacy fallback — same behavior as before
        tester_keys = ["tester"] if "tester" in team else []
        browser_tester_keys = ["tester_browser"] if "tester_browser" in team else []
        reviewer_keys = ["architect"] if "architect" in team else []

    # Collect tester agents — include browser testers only when requested
    tester_agents: list[tuple[str, Agent]] = []
    for key in tester_keys:
        if key in team:
            tester_agents.append((key, team[key]))
    if browser_testing:
        for key in browser_tester_keys:
            if key in team:
                tester_agents.append((key, team[key]))

    for tester_name, tester_agent in tester_agents:
        try:
            log.tprint(
                f"[done] running {tester_name} verification (attempt {attempt})..."
            )
            tester_result = tester_agent.run(
                verification_prompt
                + "Verify this works end-to-end. Report ONLY issues found. "
                "If everything works, say 'ALL CHECKS PASS'.",
                project_dir,
                new_conversation=reset_session,
                agent_name=f"{tester_name}_verification",
            )
            tester_report = tester_result.text or ""
            log.emit(
                "done_verification", agent=tester_name, report=tester_report[:5000]
            )
            if not _check_passed(tester_report):
                issues.append(
                    f"**{tester_name} found issues:**\n{tester_report[:3000]}"
                )
        except Exception as exc:
            log.emit("done_verification_error", agent=tester_name, error=str(exc))
            issues.append(f"**{tester_name} crashed:** {exc}")

    # Run reviewer agents
    for reviewer_key in reviewer_keys:
        reviewer_agent = team.get(reviewer_key)
        if not reviewer_agent:
            continue
        try:
            log.tprint(
                f"[done] running {reviewer_key} verification (attempt {attempt})..."
            )
            reviewer_result = reviewer_agent.run(
                verification_prompt
                + "Review the codebase for critical bugs, missing features, "
                "or deviations from the goal. Report ONLY issues found. "
                "If everything looks good, say 'ALL CHECKS PASS'.",
                project_dir,
                new_conversation=reset_session,
                agent_name=f"{reviewer_key}_verification",
            )
            reviewer_report = reviewer_result.text or ""
            log.emit(
                "done_verification", agent=reviewer_key, report=reviewer_report[:5000]
            )
            if not _check_passed(reviewer_report):
                label = reviewer_key.replace("_", " ").title()
                issues.append(f"**{label} found issues:**\n{reviewer_report[:3000]}")
        except Exception as exc:
            log.emit("done_verification_error", agent=reviewer_key, error=str(exc))
            label = reviewer_key.replace("_", " ").title()
            issues.append(f"**{label} crashed:** {exc}")

    # Fallback: if no dedicated verifiers exist, use a worker in a fresh session
    has_dedicated_verifiers = (
        bool(tester_agents)
        or bool(
            browser_tester_keys
        )  # exist even if skipped due to browser_testing=False
        or bool(reviewer_keys)
    )
    if not has_dedicated_verifiers:
        # Prefer worker_smart, fall back to any worker
        verifier = (
            team.get("worker_smart")
            or team.get("worker")
            or next((a for a in team.values()), None)
        )
        if verifier:
            verifier_name = next(
                (n for n, a in team.items() if a is verifier), "worker"
            )
            try:
                log.tprint(
                    f"[done] running {verifier_name} as verifier (fresh session)..."
                )
                verify_result = verifier.run(
                    verification_prompt
                    + "You are reviewing work done by another agent. "
                    "In a FRESH context, review the codebase changes against the goal. "
                    "Check: does it solve the goal? Is the code correct? Did anything break? "
                    "Run tests if available. Report ONLY issues found. "
                    "If everything looks good, say 'ALL CHECKS PASS'.",
                    project_dir,
                    new_conversation=True,
                    agent_name=f"{verifier_name}_verification",
                )
                verify_report = verify_result.text or ""
                log.emit(
                    "done_verification",
                    agent=verifier_name,
                    report=verify_report[:5000],
                )
                if not _check_passed(verify_report):
                    issues.append(
                        f"**{verifier_name} (verifier) found issues:**\n{verify_report[:3000]}"
                    )
            except Exception as exc:
                log.emit("done_verification_error", agent=verifier_name, error=str(exc))
                issues.append(f"**{verifier_name} (verifier) crashed:** {exc}")

    if issues:
        return (
            f"DONE REJECTED (attempt {attempt}) — verification found issues that must be fixed:\n\n"
            + "\n\n".join(issues)
            + "\n\nFix these issues and try calling done again."
        )
    return None


def compose_stage_goal(
    plan: GoalPlan,
    stage_index: int,
    completed_summaries: list[str],
) -> str:
    """Build the goal string for a specific stage.

    Includes project context, current stage description + acceptance criteria,
    summaries of completed stages, and a hint about the next stage.
    """
    stage = plan.stages[stage_index - 1]  # 1-based index
    total = len(plan.stages)

    parts: list[str] = []

    # Project context
    parts.append(f"# Project Context\n{plan.context}")

    # Progress so far
    if completed_summaries:
        parts.append("# Completed Stages")
        for i, summary in enumerate(completed_summaries, 1):
            parts.append(f"## Stage {i} — completed\n{summary}")

    # Current stage
    parts.append(
        f"# Current Stage ({stage.index}/{total}): {stage.name}\n{stage.description}"
    )
    if stage.acceptance_criteria:
        parts.append(f"## Acceptance Criteria\n{stage.acceptance_criteria}")

    # Hint about next stage
    if stage.index < total:
        next_stage = plan.stages[stage.index]  # 0-based for next
        parts.append(
            f"## Next Stage Preview\n"
            f"After this stage, the next stage will be: "
            f"**{next_stage.name}** — {next_stage.description[:200]}"
        )

    return "\n\n".join(parts)


def clone_team(team: TeamConfig) -> TeamConfig:
    """Create a deep copy of a team with fresh sessions (no shared state)."""
    return {name: agent.clone() for name, agent in team.items()}


def create_worktree(project_dir: Path, label: str) -> tuple[Path, str]:
    """Create a git worktree for isolated parallel execution.

    Returns ``(worktree_dir, branch_name)``.  The worktree is placed in a
    temp directory (outside the repo) and uses a unique branch name to avoid
    collisions with leftover branches from crashed runs.
    """
    import tempfile
    import uuid

    suffix = uuid.uuid4().hex[:8]
    branch_name = f"kodo-{label}-{suffix}"
    worktree_dir = Path(tempfile.mkdtemp(prefix=f"kodo-{label}-"))
    # mkdtemp already created the dir; git worktree add wants a non-existing
    # target, so remove the empty dir first.
    worktree_dir.rmdir()
    subprocess.run(
        ["git", "worktree", "add", str(worktree_dir), "-b", branch_name, "HEAD"],
        cwd=project_dir,
        capture_output=True,
        check=True,
    )
    return worktree_dir, branch_name


def remove_worktree(project_dir: Path, worktree_dir: Path, branch_name: str) -> None:
    """Remove a git worktree and its branch. Robust against partial failures."""
    from kodo import log

    # 1. Remove worktree from git's index (--force allows dirty state)
    result = subprocess.run(
        ["git", "worktree", "remove", str(worktree_dir), "--force"],
        cwd=project_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 and worktree_dir.exists():
        log.tprint(
            f"[worktree] git worktree remove failed ({result.stderr or result.stdout}), "
            "removing directory directly"
        )
        shutil.rmtree(worktree_dir, ignore_errors=True)

    # 2. Delete the branch (may already be gone if worktree remove succeeded)
    subprocess.run(
        ["git", "branch", "-D", branch_name],
        cwd=project_dir,
        capture_output=True,
    )

    # 3. Ensure directory is gone
    if worktree_dir.exists():
        shutil.rmtree(worktree_dir, ignore_errors=True)


def execution_groups(plan: GoalPlan) -> list[list[GoalStage]]:
    """Group stages into execution order for sequential and parallel running.

    Returns a list of groups. Each group is either ``[single_stage]`` (run
    sequentially) or ``[stage, stage, ...]`` (stages with the same
    ``parallel_group`` value, run concurrently).

    Parallel groups are inserted at the position of their *first* member in the
    original stage list.
    """
    groups: list[list[GoalStage]] = []
    active: dict[int, list[GoalStage]] = {}

    for stage in plan.stages:
        if stage.parallel_group is None:
            groups.append([stage])
        elif stage.parallel_group not in active:
            bucket: list[GoalStage] = [stage]
            active[stage.parallel_group] = bucket
            groups.append(bucket)
        else:
            active[stage.parallel_group].append(stage)

    return groups


@dataclass
class ResumeState:
    """State for resuming a previously interrupted run."""

    completed_cycles: int
    prior_summary: str
    agent_session_ids: dict[str, str]
    completed_stages: list[int]
    stage_summaries: list[str]
    current_stage_cycles: int
    pending_exchanges: list[dict] = field(default_factory=list)


class Orchestrator(Protocol):
    def cycle(
        self,
        goal: str,
        project_dir: Path,
        team: TeamConfig,
        *,
        max_exchanges: int = 30,
        prior_summary: str = "",
        browser_testing: bool = False,
        verifiers: dict | None = None,
        auto_commit: bool = False,
    ) -> CycleResult:
        """Run one cycle of orchestrated work."""
        ...

    def run(
        self,
        goal: str,
        project_dir: Path,
        team: TeamConfig,
        *,
        max_exchanges: int = 30,
        max_cycles: int = 5,
        resume: ResumeState | None = None,
        plan: GoalPlan | None = None,
        verifiers: dict | None = None,
        auto_commit: bool = False,
    ) -> RunResult:
        """Run multiple cycles until done or limit reached."""
        ...


class OrchestratorBase:
    """Shared run() logic for all orchestrator implementations.

    Subclasses must set ``self.model``, ``self._summarizer``, and
    ``self._orchestrator_name`` before calling ``super().__init__()``,
    and implement ``cycle()``.
    """

    model: str
    _orchestrator_name: str

    def cycle(
        self,
        goal: str,
        project_dir: Path,
        team: TeamConfig,
        *,
        max_exchanges: int = 30,
        prior_summary: str = "",
        browser_testing: bool = False,
        verifiers: dict | None = None,
        auto_commit: bool = False,
    ) -> CycleResult:
        raise NotImplementedError

    def for_parallel(self) -> "OrchestratorBase":
        """Return a copy safe for use in a parallel thread.

        The default implementation returns ``self``.  Subclasses that hold
        state tied to a specific asyncio event loop (e.g. cached HTTP clients)
        should override this to return an independent copy.
        """
        return self

    def run(
        self,
        goal: str,
        project_dir: Path,
        team: TeamConfig,
        *,
        max_exchanges: int = 30,
        max_cycles: int = 5,
        resume: ResumeState | None = None,
        plan: GoalPlan | None = None,
        verifiers: dict | None = None,
        auto_commit: bool = False,
    ) -> RunResult:
        from kodo import log
        from kodo.sessions.claude import ClaudeSession
        from kodo.sessions.codex import CodexSession
        from kodo.sessions.cursor import CursorSession
        from kodo.sessions.gemini_cli import GeminiCliSession

        # Inject resume session IDs into agents before starting
        if resume:
            for agent_name, sid in resume.agent_session_ids.items():
                agent = team.get(agent_name)
                if agent is None:
                    continue
                sess = agent.session
                if isinstance(sess, ClaudeSession):
                    sess.resume_session_id = sid
                elif isinstance(sess, CursorSession):
                    sess._chat_id = sid
                elif isinstance(sess, CodexSession):
                    sess._session_id = sid
                elif isinstance(sess, GeminiCliSession):
                    sess._resume_next = True

        start_cycle = (resume.completed_cycles if resume else 0) + 1
        prior_summary = resume.prior_summary if resume else ""

        log.emit(
            "run_start",
            orchestrator=self._orchestrator_name,
            model=self.model,
            goal=goal,
            project_dir=str(project_dir),
            max_exchanges=max_exchanges,
            max_cycles=max_cycles,
            team={
                name: {
                    "backend": agent.session.__class__.__name__,
                    "model": getattr(agent.session, "model", "?"),
                }
                for name, agent in team.items()
            },
            resumed=resume is not None,
            resume_from_cycle=start_cycle if resume else None,
            has_stages=plan is not None and len(plan.stages) > 0,
            num_stages=len(plan.stages) if plan else 0,
        )
        result = RunResult()

        try:
            if plan and plan.stages:
                self._run_staged(
                    goal,
                    project_dir,
                    team,
                    plan,
                    result,
                    max_exchanges=max_exchanges,
                    max_cycles=max_cycles,
                    resume=resume,
                    verifiers=verifiers,
                    auto_commit=auto_commit,
                )
            else:
                self._run_single(
                    goal,
                    project_dir,
                    team,
                    result,
                    max_exchanges=max_exchanges,
                    max_cycles=max_cycles,
                    start_cycle=start_cycle,
                    prior_summary=prior_summary,
                    verifiers=verifiers,
                    auto_commit=auto_commit,
                )
        finally:
            self._summarizer.shutdown()

            # Clean up agent sessions
            for agent in team.values():
                agent.close()

            log.emit(
                "run_end",
                orchestrator=self._orchestrator_name,
                total_cycles=len(result.cycles),
                finished=result.finished,
                total_cost_usd=result.total_cost_usd,
                total_exchanges=result.total_exchanges,
                summary=result.summary,
                stages_completed=len(result.stage_results),
            )
            log.print_stats_table(final=True)

            # Print a command to open the log viewer (don't auto-open)
            log_file = log.get_log_file()
            if log_file and log_file.exists():
                print(f"\n  View run: uv run python -m kodo.viewer {log_file}\n")

        return result

    def _run_single(
        self,
        goal: str,
        project_dir: Path,
        team: TeamConfig,
        result: RunResult,
        *,
        max_exchanges: int,
        max_cycles: int,
        start_cycle: int,
        prior_summary: str,
        verifiers: dict | None = None,
        auto_commit: bool = False,
    ) -> None:
        """Original single-goal execution loop."""
        from kodo import log

        for i in range(start_cycle, max_cycles + 1):
            if i > 1:
                print()
                log.tprint(f"[orchestrator] === CYCLE {i}/{max_cycles} ===")
            log.emit(
                "run_cycle",
                orchestrator=self._orchestrator_name,
                cycle=i,
                max_cycles=max_cycles,
            )

            cycle_result = self.cycle(
                goal,
                project_dir,
                team,
                max_exchanges=max_exchanges,
                prior_summary=prior_summary,
                verifiers=verifiers,
                auto_commit=auto_commit,
            )
            result.cycles.append(cycle_result)

            if cycle_result.finished:
                break

            prior_summary = cycle_result.summary

    def _run_one_stage(
        self,
        stage: GoalStage,
        plan: GoalPlan,
        project_dir: Path,
        team: TeamConfig,
        stage_summaries: list[str],
        *,
        max_exchanges: int,
        max_cycles_for_stage: int,
        initial_prior_summary: str = "",
        verifiers: dict | None = None,
        auto_commit: bool = False,
    ) -> StageResult:
        """Run a single stage through its cycle loop. Returns the StageResult.

        This is the inner loop extracted from _run_staged so it can be called
        both sequentially and from a ThreadPoolExecutor for parallel groups.
        """
        from kodo import log

        log.emit(
            "stage_start",
            stage_index=stage.index,
            stage_name=stage.name,
            max_cycles=max_cycles_for_stage,
        )
        print()
        log.tprint(
            f"[orchestrator] === STAGE {stage.index}/{len(plan.stages)}: "
            f"{stage.name} ==="
        )

        stage_goal = compose_stage_goal(plan, stage.index, stage_summaries)
        prior_summary = initial_prior_summary
        stage_res = StageResult(
            stage_index=stage.index,
            stage_name=stage.name,
        )

        cycles_used = 0
        while cycles_used < max_cycles_for_stage:
            cycles_used += 1

            print()
            log.tprint(
                f"[orchestrator] === CYCLE {cycles_used}/{max_cycles_for_stage} "
                f"(stage {stage.index}) ==="
            )
            log.emit(
                "run_cycle",
                orchestrator=self._orchestrator_name,
                cycle=cycles_used,
                max_cycles=max_cycles_for_stage,
                stage_index=stage.index,
            )

            cycle_result = self.cycle(
                stage_goal,
                project_dir,
                team,
                max_exchanges=max_exchanges,
                prior_summary=prior_summary,
                browser_testing=stage.browser_testing,
                verifiers=verifiers,
                auto_commit=auto_commit,
            )
            cycle_result.stage_index = stage.index
            stage_res.cycles.append(cycle_result)

            if cycle_result.finished:
                stage_res.finished = True
                stage_res.summary = cycle_result.summary
                log.emit(
                    "stage_end",
                    stage_index=stage.index,
                    stage_name=stage.name,
                    finished=True,
                    summary=cycle_result.summary[:1000],
                    cycles_used=len(stage_res.cycles),
                )
                log.tprint(
                    f"[orchestrator] Stage {stage.index} ({stage.name}) "
                    f"completed in {len(stage_res.cycles)} cycle(s)"
                )
                break

            prior_summary = cycle_result.summary
        else:
            # max_cycles exhausted
            stage_res.summary = prior_summary
            log.emit(
                "stage_end",
                stage_index=stage.index,
                stage_name=stage.name,
                finished=False,
                summary=prior_summary[:1000],
                cycles_used=len(stage_res.cycles),
                reason="max_cycles_exhausted",
            )
            log.tprint(
                f"[orchestrator] Stage {stage.index} ({stage.name}) "
                f"— cycle limit reached after {len(stage_res.cycles)} cycle(s)"
            )

        return stage_res

    def _run_staged(
        self,
        goal: str,
        project_dir: Path,
        team: TeamConfig,
        plan: GoalPlan,
        result: RunResult,
        *,
        max_exchanges: int,
        max_cycles: int,
        resume: ResumeState | None = None,
        verifiers: dict | None = None,
        auto_commit: bool = False,
    ) -> None:
        """Staged execution: iterate over plan stages with a shared cycle limit.

        Supports parallel execution: stages with the same ``parallel_group``
        run concurrently via ThreadPoolExecutor.  Each parallel stage runs in
        its own git worktree for filesystem isolation — any source modifications
        are discarded when the worktree is cleaned up.  Findings files (under
        ``~/.kodo/runs/``) are outside the worktree and persist normally.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        from kodo import log

        stage_summaries: list[str] = []

        # Resume support: skip completed stages
        start_stage_idx = 0
        if resume and resume.completed_stages:
            start_stage_idx = len(resume.completed_stages)
            stage_summaries = list(resume.stage_summaries)

        # Build execution groups, then skip already-completed ones.
        # Each group is [stage] (sequential) or [stage, stage, ...] (parallel).
        groups = execution_groups(plan)

        # Figure out which groups to skip based on resume state
        remaining_groups: list[list[GoalStage]] = []
        for group in groups:
            max_idx = max(s.index for s in group)
            if max_idx > start_stage_idx:
                remaining_groups.append(group)

        # Divide remaining cycles across remaining groups
        remaining_cycles = max_cycles - (resume.completed_cycles if resume else 0)

        for group in remaining_groups:
            if remaining_cycles <= 0:
                skipped = sum(
                    len(g) for g in remaining_groups[remaining_groups.index(group) :]
                )
                log.tprint(
                    f"[orchestrator] Stopping run — all {max_cycles} cycle(s) used, "
                    f"{skipped} stage(s) remaining"
                )
                break

            if len(group) == 1:
                # Sequential: single stage gets remaining cycles
                stage = group[0]
                initial_prior = ""
                if (
                    resume
                    and resume.current_stage_cycles > 0
                    and stage.index == start_stage_idx + 1
                ):
                    initial_prior = resume.prior_summary

                try:
                    stage_res = self._run_one_stage(
                        stage,
                        plan,
                        project_dir,
                        team,
                        stage_summaries,
                        max_exchanges=max_exchanges,
                        max_cycles_for_stage=remaining_cycles,
                        initial_prior_summary=initial_prior,
                        verifiers=verifiers,
                        auto_commit=auto_commit,
                    )
                except Exception as exc:
                    log.tprint(
                        f"[orchestrator] Stage {stage.index} "
                        f"({stage.name}) crashed: {exc}"
                    )
                    log.emit(
                        "stage_error",
                        stage_index=stage.index,
                        error=str(exc),
                    )
                    stage_res = StageResult(
                        stage_index=stage.index,
                        stage_name=stage.name,
                        summary=f"Stage crashed: {exc}",
                    )

                remaining_cycles -= len(stage_res.cycles)
                result.cycles.extend(stage_res.cycles)
                result.stage_results.append(stage_res)

                if stage_res.finished:
                    stage_summaries.append(stage_res.summary)
                else:
                    log.tprint("[orchestrator] Stopping run — stage did not complete")
                    break
            else:
                # Parallel group: split cycles evenly, run concurrently
                per_stage_cycles = max(1, remaining_cycles // len(group))
                stage_labels = ", ".join(f"{s.index}:{s.name}" for s in group)
                print()
                log.tprint(f"[orchestrator] === PARALLEL GROUP: {stage_labels} ===")
                log.emit(
                    "parallel_group_start",
                    stages=[s.index for s in group],
                    per_stage_cycles=per_stage_cycles,
                )

                # Snapshot stage_summaries so all parallel stages see the same
                # prior context (they shouldn't see each other's results).
                summaries_snapshot = list(stage_summaries)
                # When resuming mid-cycle, all stages in the first remaining
                # group need prior_summary from the interrupted cycle.
                initial_prior = ""
                if (
                    resume
                    and resume.current_stage_cycles > 0
                    and group is remaining_groups[0]
                ):
                    initial_prior = resume.prior_summary
                futures_map: dict = {}

                # Each parallel stage gets its own cloned team (fresh
                # sessions) so agents aren't shared across threads.
                stage_teams: dict[int, TeamConfig] = {
                    stage.index: clone_team(team) for stage in group
                }

                # Create git worktrees for isolation.  Each parallel stage
                # runs in its own worktree so it cannot corrupt the main
                # working directory even if it writes files.
                worktrees: dict[int, tuple[Path, str]] = {}

                def _run_in_own_loop(
                    orchestrator,
                    stage,
                    plan,
                    stage_dir,
                    stage_team,
                    summaries_snapshot,
                    **kwargs,
                ):
                    """Wrapper that gives each thread a fresh asyncio event
                    loop so pydantic-ai's run_sync() doesn't collide."""
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        return orchestrator._run_one_stage(
                            stage,
                            plan,
                            stage_dir,
                            stage_team,
                            summaries_snapshot,
                            **kwargs,
                        )
                    finally:
                        loop.close()

                try:
                    for stage in group:
                        try:
                            wt_dir, branch = create_worktree(
                                project_dir, f"stage-{stage.index}"
                            )
                            worktrees[stage.index] = (wt_dir, branch)
                            log.tprint(
                                f"[orchestrator] Worktree for stage {stage.index}: {wt_dir}"
                            )
                        except (subprocess.CalledProcessError, OSError) as exc:
                            log.tprint(
                                f"[orchestrator] Worktree creation failed for "
                                f"stage {stage.index}, using project dir: {exc}"
                            )
                    with ThreadPoolExecutor(max_workers=len(group)) as pool:
                        for stage in group:
                            stage_dir = (
                                worktrees[stage.index][0]
                                if stage.index in worktrees
                                else project_dir
                            )
                            future = pool.submit(
                                _run_in_own_loop,
                                self.for_parallel(),
                                stage,
                                plan,
                                stage_dir,
                                stage_teams[stage.index],
                                summaries_snapshot,
                                max_exchanges=max_exchanges,
                                max_cycles_for_stage=per_stage_cycles,
                                initial_prior_summary=initial_prior,
                                verifiers=verifiers,
                                auto_commit=False,
                            )
                            futures_map[future] = stage

                        # Collect results as they finish
                        parallel_results: list[StageResult] = []
                        for future in as_completed(futures_map):
                            stage = futures_map[future]
                            try:
                                stage_res = future.result()
                            except Exception as exc:
                                log.tprint(
                                    f"[orchestrator] Stage {stage.index} "
                                    f"({stage.name}) crashed: {exc}"
                                )
                                log.emit(
                                    "stage_error",
                                    stage_index=stage.index,
                                    error=str(exc),
                                )
                                stage_res = StageResult(
                                    stage_index=stage.index,
                                    stage_name=stage.name,
                                    summary=f"Stage crashed: {exc}",
                                )
                            parallel_results.append(stage_res)
                            result.cycles.extend(stage_res.cycles)
                            result.stage_results.append(stage_res)
                finally:
                    # Clean up cloned sessions and worktrees even on
                    # KeyboardInterrupt to avoid leaking temp directories.
                    for st in stage_teams.values():
                        for agent in st.values():
                            agent.close()
                    for stage_idx, (wt_dir, branch) in worktrees.items():
                        try:
                            remove_worktree(project_dir, wt_dir, branch)
                        except Exception as exc:
                            log.tprint(
                                f"[orchestrator] Worktree cleanup failed for "
                                f"stage {stage_idx}: {exc}"
                            )

                # Sort summaries by stage index for deterministic ordering
                parallel_results.sort(key=lambda r: r.stage_index)
                # For parallel work, count the max branch (wall-clock)
                remaining_cycles -= max(
                    (len(r.cycles) for r in parallel_results), default=0
                )

                # Add all parallel summaries to context for subsequent stages
                for pr in parallel_results:
                    stage_summaries.append(pr.summary)

                log.emit(
                    "parallel_group_end",
                    stages=[r.stage_index for r in parallel_results],
                    total_cycles=sum(len(r.cycles) for r in parallel_results),
                    all_finished=all(r.finished for r in parallel_results),
                )
