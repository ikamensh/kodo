"""Orchestrator protocol and shared types."""

from __future__ import annotations

import concurrent.futures
import os
from pathlib import Path
from typing import TYPE_CHECKING

from kodo.formatting import BOLD as _BOLD, RESET as _RESET, plural as _plural

# Re-export all types for backward compatibility — every consumer that
# does ``from kodo.orchestrators.base import CycleResult, ...`` keeps working.
from kodo.orchestrators.types import (  # noqa: F401
    CycleConfig,
    CycleResult,
    DoneSignal,
    FatalAgentError,
    GoalPlan,
    GoalStage,
    QuickCheck,
    ResumeState,
    RunResult,
    StageResult,
    TeamConfig,
)

from kodo.orchestrators.agent_tools import (  # noqa: F401
    _FATAL_ERROR_PATTERNS,
    handle_agent_call,
)
from kodo.orchestrators.cycle_utils import (  # noqa: F401
    apply_done_signal,
    build_cycle_prompt,
)
from kodo.orchestrators.git_ops import (  # noqa: F401
    _auto_commit,
    _remove_worktree_keep_branch,
    cleanup_stale_worktrees,
    commit_worktree_changes,
    create_worktree,
    merge_worktree_branch,
    remove_worktree,
)
from kodo.orchestrators.stage_planning import (  # noqa: F401
    _handle_stage_crash,
    clone_team,
    compose_stage_goal,
    execution_groups,
)
from kodo.orchestrators.resume import inject_resume_sessions  # noqa: F401
from kodo.orchestrators.parallel import (  # noqa: F401
    cleanup_and_merge_worktrees,
    create_stage_worktrees,
    run_group_sequentially,
    run_stage_in_isolated_loop,
)

if TYPE_CHECKING:
    from kodo.advisor import Advisor


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

    def _fallback_summary(self, result: CycleResult, context: str = "") -> None:
        """Fill result.summary from accumulated agent reports when cycle ended without done."""
        if result.finished or result.summary:
            return
        accumulated = self._summarizer.get_accumulated_summary()
        tag = f" {context}" if context else ""
        if accumulated:
            result.summary = f"[Cycle ended:{tag} Work so far:]\n{accumulated}"
        else:
            result.summary = f"[Cycle ended:{tag} No summary available — check logs.]"

    def _cycle_epilogue(
        self,
        result: CycleResult,
        *,
        cost_bucket: str,
        context: str = "",
    ) -> CycleResult:
        """Shared cycle teardown: fallback summary, cycle_end log, clear summarizer."""
        from kodo import log

        self._fallback_summary(result, context)
        log.emit(
            "cycle_end",
            orchestrator=self._orchestrator_name,
            exchanges=result.exchanges,
            finished=result.finished,
            summary=result.summary,
            cost_usd=result.total_cost_usd,
            cost_bucket=cost_bucket,
        )
        self._summarizer.clear()
        return result

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
        effort: str = "standard",
        advisor: "Advisor | None" = None,
        config: CycleConfig | None = None,
    ) -> RunResult:
        from kodo import log

        inject_resume_sessions(team, resume)

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
        if config is not None:
            run_config = config
        else:
            run_config = CycleConfig(
                verifiers=verifiers,
                auto_commit=auto_commit,
                effort=effort,
            )

        _run_error: BaseException | None = None
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
                    advisor=advisor,
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
        except BaseException as exc:
            _run_error = exc
            raise
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
                **(
                    {"error": f"{type(_run_error).__name__}: {_run_error}"}
                    if _run_error is not None
                    else {}
                ),
            )

            # Best-effort trace upload (gated behind KODO_TRACE_UPLOAD env var)
            log_file = log.get_log_file()
            if log_file and log_file.exists():
                try:
                    from kodo.trace_upload import upload_trace

                    upload_trace(
                        run_id=log_file.parent.name,
                        run_dir=log_file.parent,
                        project_dir=project_dir,
                        goal=goal,
                        agent_count=len(team),
                        total_cost_usd=result.total_cost_usd,
                        total_exchanges=result.total_exchanges,
                        total_cycles=len(result.cycles),
                        finished=result.finished,
                        run_error=_run_error,
                        orchestrator=self._orchestrator_name,
                        model=self.model,
                        elapsed_s=log.get_elapsed_s(),
                    )
                except Exception:
                    pass  # never crash on trace upload failure

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
        from kodo.orchestrators.run_status import write_run_status

        for i in range(start_cycle, max_cycles + 1):
            write_run_status(
                project_dir,
                goal,
                cycle_num=i,
                max_cycles=max_cycles,
            )
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

            try:
                cycle_result = self.cycle(
                    goal,
                    project_dir,
                    team,
                    max_exchanges=max_exchanges,
                    prior_summary=prior_summary,
                    config=config,
                )
            except Exception as exc:
                log.emit(
                    "cycle_error",
                    orchestrator=self._orchestrator_name,
                    cycle=i,
                    error=f"{type(exc).__name__}: {exc}",
                )
                raise
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

        from kodo.orchestrators.run_status import write_run_status

        cycles_used = 0
        while cycles_used < max_cycles_for_stage:
            cycles_used += 1

            write_run_status(
                project_dir,
                stage_goal,
                stage_label=f"{stage.index}/{len(plan.stages)}: {stage.name}",
                cycle_num=cycles_used,
                max_cycles=max_cycles_for_stage,
            )

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
                acceptance_criteria=stage.acceptance_criteria or None,
                effort=config.effort,
                done_mode=config.done_mode,
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
                stage_res.success = True
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

    def _run_adaptive(
        self,
        goal: str,
        project_dir: Path,
        team: TeamConfig,
        plan: GoalPlan,
        result: RunResult,
        *,
        stage_summaries: list[str],
        max_exchanges: int,
        remaining_cycles: int,
        start_stage_idx: int,
        config: CycleConfig,
        advisor: "Advisor",
    ) -> None:
        """Adaptive execution: advisor generates stages one at a time."""
        from kodo import log

        # Use a separate plan for compose_stage_goal.
        # Pre-populate with placeholders for completed stages so indices are valid.
        placeholder_stages = [
            GoalStage(
                index=i + 1,
                name=f"(completed stage {i + 1})",
                description="",
                acceptance_criteria="(completed)",
            )
            for i in range(start_stage_idx)
        ]
        adaptive_plan = GoalPlan(context=plan.context, stages=placeholder_stages)
        completed_count = start_stage_idx
        next_index = completed_count + 1

        while remaining_cycles > 0 and completed_count < advisor.max_stages:
            try:
                decision = advisor.assess(
                    goal,
                    plan,
                    stage_summaries,
                    completed_count,
                )
            except Exception as exc:
                log.tprint(
                    f"[advisor] assess() failed: {exc} — "
                    f"stopping after {_plural(completed_count, 'stage')}",
                )
                log.emit(
                    "advisor_assess_error",
                    error=str(exc),
                    completed_stages=completed_count,
                )
                break

            if decision.action == "done":
                log.tprint(
                    f"[advisor] Goal complete after "
                    f"{_plural(completed_count, 'stage')}: {decision.summary}",
                )
                log.emit(
                    "advisor_done",
                    completed_stages=completed_count,
                    summary=decision.summary,
                )
                # Mark run as finished
                if result.stage_results:
                    result.stage_results[-1].finished = True
                    result.stage_results[-1].success = True
                else:
                    # Advisor said "done" before any stages ran — create synthetic completed stage
                    result.stage_results.append(
                        StageResult(
                            stage_index=0,
                            stage_name="(advisor confirmed done)",
                            finished=True,
                            success=True,
                            summary=decision.summary,
                        )
                    )
                break

            stage = advisor.make_stage(decision, next_index)
            adaptive_plan.stages.append(stage)

            log.tprint(f"[advisor] Next: Stage {next_index} — {stage.name}")

            try:
                stage_res = self._run_one_stage(
                    stage,
                    adaptive_plan,
                    project_dir,
                    team,
                    stage_summaries,
                    max_exchanges=max_exchanges,
                    max_cycles_for_stage=remaining_cycles,
                    config=config,
                )
            except Exception as exc:
                stage_res = _handle_stage_crash(stage, exc)

            remaining_cycles -= len(stage_res.cycles)
            result.cycles.extend(stage_res.cycles)
            result.stage_results.append(stage_res)

            if stage_res.finished:
                stage_summaries.append(stage_res.summary)
                completed_count += 1
                next_index += 1
            else:
                log.tprint("[orchestrator] Stopping run — stage did not complete")
                break

        if completed_count >= advisor.max_stages:
            log.tprint(
                f"[advisor] Safety limit reached ({advisor.max_stages} stages)",
            )
            log.emit("advisor_safety_limit", max_stages=advisor.max_stages)

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
        advisor: "Advisor | None" = None,
    ) -> None:
        """Staged execution: iterate over plan stages with a shared cycle limit.

        When *advisor* is provided, uses adaptive planning: the advisor
        decides the next stage after each completion. When ``None``, falls
        back to the original waterfall execution.

        Supports parallel execution: stages with the same ``parallel_group``
        run concurrently via ThreadPoolExecutor.  Each parallel stage runs in
        its own git worktree for filesystem isolation — any source modifications
        are discarded when the worktree is cleaned up.  Findings files (under
        ``~/.kodo/runs/``) are outside the worktree and persist normally.
        """

        from kodo import log

        stage_summaries: list[str] = []

        # Resume support: skip completed stages
        start_stage_idx = 0
        if resume and resume.completed_stages:
            start_stage_idx = len(resume.completed_stages)
            stage_summaries = list(resume.stage_summaries)

        remaining_cycles = max_cycles - (resume.completed_cycles if resume else 0)

        # === ADAPTIVE MODE ===
        if advisor is not None:
            self._run_adaptive(
                goal,
                project_dir,
                team,
                plan,
                result,
                stage_summaries=stage_summaries,
                max_exchanges=max_exchanges,
                remaining_cycles=remaining_cycles,
                start_stage_idx=start_stage_idx,
                config=config,
                advisor=advisor,
            )
            return

        # === WATERFALL MODE ===
        # Build execution groups, then skip already-completed ones.
        # Each group is [stage] (sequential) or [stage, stage, ...] (parallel).
        groups = execution_groups(plan)

        # Figure out which groups to skip based on resume state
        remaining_groups: list[list[GoalStage]] = []
        for group in groups:
            max_idx = max(s.index for s in group)
            if max_idx > start_stage_idx:
                remaining_groups.append(group)

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
                    stage_res = _handle_stage_crash(stage, exc)

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

                # If every stage in the parallel group failed, stop the
                # run — subsequent stages would only receive crash
                # summaries and waste cycles.
                if not any(pr.finished for pr in parallel_results):
                    log.tprint(
                        "[orchestrator] Stopping run — no parallel stages completed",
                    )
                    break

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

        # Clean up stale worktrees from previous interrupted runs
        cleanup_stale_worktrees(project_dir)

        # Create git worktrees for isolation.
        worktrees, worktree_failed = create_stage_worktrees(group, project_dir)

        parallel_results: list[StageResult] = []
        try:
            # If any worktree failed, fall back to sequential execution.
            if worktree_failed:
                return run_group_sequentially(
                    self,
                    group,
                    plan,
                    project_dir,
                    stage_teams,
                    stage_summaries,
                    result,
                    worktrees,
                    max_exchanges=max_exchanges,
                    per_stage_cycles=per_stage_cycles,
                    initial_prior=initial_prior,
                    config=config,
                )
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
                        run_stage_in_isolated_loop,
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
                            browser_testing=config.browser_testing,
                            verifiers=config.verifiers,
                            auto_commit=(stage.persist_changes and config.auto_commit),
                            verification=config.verification,
                            acceptance_criteria=config.acceptance_criteria,
                            effort=config.effort,
                            done_mode=config.done_mode,
                        ),
                    )
                    futures_map[future] = stage

                # Collect results as they finish
                for future in as_completed(futures_map):
                    stage = futures_map[future]
                    try:
                        stage_res = future.result()
                    except Exception as exc:
                        stage_res = _handle_stage_crash(stage, exc)
                    parallel_results.append(stage_res)
                    result.cycles.extend(stage_res.cycles)
                    result.stage_results.append(stage_res)
        finally:
            cleanup_and_merge_worktrees(
                group,
                worktrees,
                stage_teams,
                parallel_results,
                project_dir,
            )

        # Sort by stage index for deterministic ordering — both the
        # local list (used for summaries) and the tail of
        # result.stage_results (which was appended in arrival order).
        parallel_results.sort(key=lambda r: r.stage_index)
        n_parallel = len(parallel_results)
        if n_parallel:
            result.stage_results[-n_parallel:] = sorted(
                result.stage_results[-n_parallel:],
                key=lambda r: r.stage_index,
            )
        # For parallel work, count the max branch (wall-clock)
        cycles_used = max(
            (len(r.cycles) for r in parallel_results),
            default=0,
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
