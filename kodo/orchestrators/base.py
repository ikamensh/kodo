"""Orchestrator protocol and shared types."""

from __future__ import annotations

import asyncio
import concurrent.futures
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from kodo.agent import Agent
from kodo.orchestrators.git_ops import (
    _GIT_TIMEOUT,
    _git,
    _remove_worktree_keep_branch,
    commit_worktree_changes,
    create_worktree,
    merge_worktree_branch,
    remove_worktree,
)
from kodo.orchestrators.verification import VerificationState, handle_done, verify_done

# ANSI formatting (duplicated from cli._ui to avoid circular import)
_BOLD = "\033[1m"
_DIM = "\033[2m"
_CYAN = "\033[36m"
_RESET = "\033[0m"

# Team is just a named dict of agents
TeamConfig = dict[str, Agent]

# Patterns that indicate unrecoverable errors — retrying won't help.
_FATAL_ERROR_PATTERNS = re.compile(
    r"Subscription/billing issue|Authentication failed|Binary not working",
    re.IGNORECASE,
)


class FatalAgentError(Exception):
    """Raised when all workers have hit unrecoverable errors."""


def _plural(n: int, word: str) -> str:
    """Return e.g. '1 cycle' or '3 cycles'."""
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


@dataclass
class QuickCheck:
    """Lightweight scripted check that replaces agent-based verification.

    Used for stages where a simple file-existence check is sufficient
    (e.g. analytical stages that write a findings file).
    """

    path: str  # file that must exist (can use {run_dir} placeholder)
    description: str  # shown to orchestrator as what we're verifying
    error_message: str  # fed back to agent if check fails


@dataclass
class GoalStage:
    """One stage in a multi-stage goal plan."""

    index: int  # 1-based
    name: str  # short label
    description: str  # full prose for orchestrator
    acceptance_criteria: str  # verifiable "done" definition
    browser_testing: bool = False  # whether this stage needs browser verification
    parallel_group: int | None = None  # stages with same group run concurrently
    persist_changes: bool = False  # merge worktree changes back after completion
    verification: Literal["full", "skip"] | list[QuickCheck] = "full"


@dataclass
class CycleConfig:
    """Pass-through configuration for a single cycle.

    Bundles stage-level settings (browser_testing, verification) and
    run-level settings (verifiers, auto_commit) so they don't have to be
    threaded as individual keyword arguments through every layer.
    """

    browser_testing: bool = False
    verifiers: dict | None = None
    auto_commit: bool = False
    verification: Literal["full", "skip"] | list[QuickCheck] = "full"


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
    done_msg = f"{icon} [{_CYAN}{agent_name}{_RESET}] done ({agent_result.elapsed_s:.1f}s)"
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
        "Add Co-Authored-By: kodo <noreply@github.com>\n"
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


class DoneSignal:
    """Shared mutable to communicate between the ``done`` tool and the cycle loop."""

    def __init__(self) -> None:
        self.called = False
        self.summary = ""
        self.success = False


def build_cycle_prompt(goal: str, project_dir: Path, prior_summary: str = "") -> str:
    """Build the user-turn prompt sent to the orchestrator each cycle."""
    from kodo import log

    prompt = f"# Goal\n\n{goal}\n\nProject directory: {project_dir}"
    log_file = log.get_log_file()
    if log_file:
        prompt += f"\nRun log (JSONL): {log_file}"
    if prior_summary:
        prompt += (
            f"\n\n# Previous progress\n\n{prior_summary}"
            "\n\nContinue working toward the goal."
        )
    return prompt


def compose_stage_goal(
    plan: GoalPlan,
    stage_index: int,
    completed_summaries: list[str],
) -> str:
    """Build the goal string for a specific stage.

    Includes project context, current stage description + acceptance criteria,
    summaries of completed stages, and a hint about the next stage.

    Args:
        plan: The goal plan containing stages
        stage_index: 1-based stage index (1 to len(plan.stages))
        completed_summaries: Summaries of completed stages

    Raises:
        ValueError: If stage_index is out of valid range
    """
    if stage_index < 1 or stage_index > len(plan.stages):
        raise ValueError(
            f"stage_index must be between 1 and {len(plan.stages)}, got {stage_index}"
        )

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
        f"# Current Stage ({stage.index}/{total}): {stage.name}\n{stage.description}",
    )
    if stage.acceptance_criteria:
        parts.append(f"## Acceptance Criteria\n{stage.acceptance_criteria}")

    # Hint about next stage
    if stage.index < total:
        next_stage = plan.stages[stage.index]  # 0-based for next
        parts.append(
            f"## Next Stage Preview\n"
            f"After this stage, the next stage will be: "
            f"**{next_stage.name}** — {next_stage.description[:200]}",
        )

    return "\n\n".join(parts)


def clone_team(team: TeamConfig) -> TeamConfig:
    """Create a deep copy of a team with fresh sessions (no shared state)."""
    return {name: agent.clone() for name, agent in team.items()}


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
        config: CycleConfig | None = None,
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
        config: CycleConfig | None = None,
    ) -> CycleResult:
        raise NotImplementedError

    def for_parallel(self) -> "OrchestratorBase":
        """Return a copy safe for use in a parallel thread.

        The default implementation returns ``self``.  Subclasses that hold
        state tied to a specific asyncio event loop (e.g. cached HTTP clients)
        should override this to return an independent copy.
        """
        return self

    async def close(self) -> None:
        """Clean up resources (HTTP clients, etc.).

        Default is a no-op.  Subclasses that acquire resources in
        ``for_parallel()`` should override this.
        """

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
        run_config = CycleConfig(verifiers=verifiers, auto_commit=auto_commit)

        try:
            if plan and not plan.stages:
                log.tprint(
                    "[orchestrator] Warning: GoalPlan has no stages, "
                    "running as single-goal",
                )
                log.emit("run_empty_plan_fallback", goal=goal)
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
                    config=run_config,
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
                    config=run_config,
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
        config: CycleConfig,
    ) -> None:
        """Original single-goal execution loop."""
        from kodo import log

        for i in range(start_cycle, max_cycles + 1):
            if i > 1:
                print()
                log.tprint(f"{'─' * 40}")
                log.tprint(f"{_BOLD}CYCLE {i}/{max_cycles}{_RESET}")
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
                config=config,
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
        config: CycleConfig,
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
            f"{stage.name} ===",
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
                f"(stage {stage.index}) ===",
            )
            log.emit(
                "run_cycle",
                orchestrator=self._orchestrator_name,
                cycle=cycles_used,
                max_cycles=max_cycles_for_stage,
                stage_index=stage.index,
            )

            # Build stage-specific config: merge stage settings with run config
            stage_config = CycleConfig(
                browser_testing=stage.browser_testing,
                verifiers=config.verifiers,
                auto_commit=config.auto_commit,
                verification=stage.verification,
            )
            cycle_result = self.cycle(
                stage_goal,
                project_dir,
                team,
                max_exchanges=max_exchanges,
                prior_summary=prior_summary,
                config=stage_config,
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
                    f"completed in {_plural(len(stage_res.cycles), 'cycle')}",
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
                f"— cycle limit reached after {_plural(len(stage_res.cycles), 'cycle')}",
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
        config: CycleConfig,
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
                    f"[orchestrator] Stopping run — all {_plural(max_cycles, 'cycle')} used, "
                    f"{_plural(skipped, 'stage')} remaining",
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
                        config=config,
                    )
                except Exception as exc:
                    log.tprint(
                        f"[orchestrator] Stage {stage.index} "
                        f"({stage.name}) crashed: {exc}",
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
                parallel_results, cycles_used = self._run_parallel_group(
                    group,
                    plan,
                    project_dir,
                    team,
                    stage_summaries,
                    result,
                    max_exchanges=max_exchanges,
                    per_stage_cycles=remaining_cycles,
                    initial_prior=(
                        resume.prior_summary
                        if resume
                        and resume.current_stage_cycles > 0
                        and group is remaining_groups[0]
                        else ""
                    ),
                    config=config,
                )
                remaining_cycles -= cycles_used

                # Add all parallel summaries to context for subsequent stages
                for pr in parallel_results:
                    stage_summaries.append(pr.summary)

    def _run_parallel_group(
        self,
        group: list[GoalStage],
        plan: GoalPlan,
        project_dir: Path,
        team: TeamConfig,
        stage_summaries: list[str],
        result: RunResult,
        *,
        max_exchanges: int,
        per_stage_cycles: int,
        initial_prior: str,
        config: CycleConfig,
    ) -> tuple[list[StageResult], int]:
        """Run a parallel group of stages concurrently.

        Returns ``(parallel_results, cycles_used)`` where *cycles_used* is the
        max branch length (wall-clock cost).
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        from kodo import log

        stage_labels = ", ".join(f"{s.index}:{s.name}" for s in group)
        print()
        log.tprint(f"{'─' * 40}")
        log.tprint(f"{_BOLD}PARALLEL GROUP: {stage_labels}{_RESET}")
        log.emit(
            "parallel_group_start",
            stages=[s.index for s in group],
            per_stage_cycles=per_stage_cycles,
        )

        # Snapshot stage_summaries so all parallel stages see the same
        # prior context (they shouldn't see each other's results).
        summaries_snapshot = list(stage_summaries)
        futures_map: dict[concurrent.futures.Future, GoalStage] = {}

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
                try:
                    loop.run_until_complete(orchestrator.close())
                except (OSError, RuntimeError) as e:
                    from kodo import log

                    log.emit("orchestrator_close_error", error=str(e))
                finally:
                    loop.close()

        parallel_results: list[StageResult] = []
        try:
            worktree_failed = False
            for stage in group:
                try:
                    wt_dir, branch = create_worktree(
                        project_dir, f"stage-{stage.index}",
                    )
                    worktrees[stage.index] = (wt_dir, branch)
                    log.tprint(
                        f"[orchestrator] Worktree for stage {stage.index}: {wt_dir}",
                    )
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
                    log.tprint(
                        f"⚠️  [orchestrator] Worktree creation failed for "
                        f"stage {stage.index}: {exc}",
                    )
                    worktree_failed = True
            # If any worktree failed, clean up the ones that succeeded
            # and fall back to running stages sequentially to avoid
            # multiple agents writing to the same project_dir.
            if worktree_failed:
                log.tprint(
                    "⚠️  [orchestrator] Cannot isolate parallel stages — "
                    "running sequentially instead",
                )
                for idx, (wt_dir, branch) in list(worktrees.items()):
                    try:
                        remove_worktree(project_dir, wt_dir, branch)
                    except Exception as exc:
                        log.emit(
                            "worktree_cleanup_error",
                            stage_index=idx,
                            error=str(exc),
                        )
                worktrees.clear()
                for stage in group:
                    stage_res = self._run_one_stage(
                        stage,
                        plan,
                        project_dir,
                        stage_teams[stage.index],
                        stage_summaries,
                        max_exchanges=max_exchanges,
                        max_cycles_for_stage=per_stage_cycles,
                        initial_prior_summary=initial_prior,
                        config=CycleConfig(
                            verifiers=config.verifiers,
                            auto_commit=(
                                stage.persist_changes and config.auto_commit
                            ),
                        ),
                    )
                    parallel_results.append(stage_res)
                    result.cycles.extend(stage_res.cycles)
                    result.stage_results.append(stage_res)
                    stage_summaries.append(stage_res.summary)
                cycles_used = max(
                    (len(r.cycles) for r in parallel_results), default=0,
                )
                return parallel_results, cycles_used
            max_parallel = int(os.environ.get("KODO_MAX_PARALLEL", "2"))
            workers = min(len(group), max_parallel)
            with ThreadPoolExecutor(max_workers=workers) as pool:
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
                        config=CycleConfig(
                            verifiers=config.verifiers,
                            auto_commit=(
                                stage.persist_changes and config.auto_commit
                            ),
                        ),
                    )
                    futures_map[future] = stage

                # Collect results as they finish
                for future in as_completed(futures_map):
                    stage = futures_map[future]
                    try:
                        stage_res = future.result()
                    except Exception as exc:
                        log.tprint(
                            f"[orchestrator] Stage {stage.index} "
                            f"({stage.name}) crashed: {exc}",
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

            # Build lookup for persist_changes stages
            stages_by_idx = {s.index: s for s in group}
            finished_indices = (
                {pr.stage_index for pr in parallel_results if pr.finished}
                if parallel_results
                else set()
            )

            # 1. Commit uncommitted changes in persist_changes
            #    worktrees (safety net before merge).
            branches_to_merge: list[tuple[str, str, int]] = []
            for stage_idx, (wt_dir, branch) in worktrees.items():
                stg = stages_by_idx.get(stage_idx)
                if (
                    stg
                    and stg.persist_changes
                    and stage_idx in finished_indices
                ):
                    try:
                        commit_worktree_changes(wt_dir, stg.name)
                        branches_to_merge.append((branch, stg.name, stage_idx))
                    except Exception as exc:
                        log.tprint(
                            f"[persist] Commit failed for "
                            f"stage {stage_idx}: {exc}",
                        )

            # 2. Close cloned sessions
            for st in stage_teams.values():
                for agent in st.values():
                    agent.close()

            # 3. Remove worktrees — keep branches that need merging
            branches_to_keep = {b for b, _, _ in branches_to_merge}
            for stage_idx, (wt_dir, branch) in worktrees.items():
                try:
                    if branch in branches_to_keep:
                        _remove_worktree_keep_branch(project_dir, wt_dir)
                    else:
                        remove_worktree(project_dir, wt_dir, branch)
                except Exception as exc:
                    log.tprint(
                        f"[orchestrator] Worktree cleanup failed for "
                        f"stage {stage_idx}: {exc}",
                    )

            # 4. Merge persist_changes branches sequentially
            branches_to_merge.sort(key=lambda x: x[2])
            for branch, stage_name, stage_idx in branches_to_merge:
                try:
                    merge_result = merge_worktree_branch(
                        project_dir, branch, stage_name,
                    )
                    log.emit(
                        "persist_stage_merge",
                        stage_index=stage_idx,
                        success=merge_result.success,
                        had_changes=merge_result.had_changes,
                        conflict=merge_result.conflict,
                    )
                except Exception as exc:
                    log.tprint(
                        f"[persist] Merge failed for stage {stage_idx}: {exc}",
                    )
                finally:
                    # Always clean up the branch after merge attempt
                    subprocess.run(
                        [_git(), "branch", "-D", branch],
                        cwd=project_dir,
                        capture_output=True,
                        timeout=_GIT_TIMEOUT,
                    )

        # Sort summaries by stage index for deterministic ordering
        parallel_results.sort(key=lambda r: r.stage_index)
        # For parallel work, count the max branch (wall-clock)
        cycles_used = max(
            (len(r.cycles) for r in parallel_results), default=0,
        )

        log.emit(
            "parallel_group_end",
            stages=[r.stage_index for r in parallel_results],
            total_cycles=sum(len(r.cycles) for r in parallel_results),
            all_finished=all(r.finished for r in parallel_results),
        )

        return parallel_results, cycles_used


# ---------------------------------------------------------------------------
# Backward-compatible re-exports for symbols that moved to mcp_server.py.
