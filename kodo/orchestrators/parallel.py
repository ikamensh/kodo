"""Parallel execution helpers for running stage groups concurrently."""

from __future__ import annotations

import asyncio
import signal
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from kodo.orchestrators.git_ops import (
    _GIT,
    _GIT_TIMEOUT,
    _remove_worktree_keep_branch,
    commit_worktree_changes,
    create_worktree,
    merge_worktree_branch,
    remove_worktree,
)
from kodo.orchestrators.types import (
    CycleConfig,
    GoalPlan,
    GoalStage,
    RunResult,
    StageResult,
    TeamConfig,
)

if TYPE_CHECKING:
    from kodo.orchestrators.base import OrchestratorBase


def run_stage_in_isolated_loop(
    orchestrator: "OrchestratorBase",
    stage: GoalStage,
    plan: GoalPlan,
    stage_dir: Path,
    stage_team: TeamConfig,
    summaries_snapshot: list[str],
    **kwargs,
) -> StageResult:
    """Wrapper that gives each thread a fresh asyncio event loop.

    Prevents pydantic-ai's ``run_sync()`` from colliding across threads
    by ensuring each thread has its own event loop.
    """
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
            # Stop the persistent event loop if the orchestrator has one
            # (ApiOrchestrator uses a dedicated loop thread for httpx).
            if hasattr(orchestrator, "shutdown"):
                orchestrator.shutdown()
            loop.close()


def create_stage_worktrees(
    group: list[GoalStage],
    project_dir: Path,
) -> tuple[dict[int, tuple[Path, str]], bool]:
    """Create git worktrees for each stage in a parallel group.

    Returns ``(worktrees, failed)`` where *worktrees* maps stage index
    to ``(worktree_dir, branch_name)`` and *failed* is ``True`` if any
    worktree creation failed.
    """
    from kodo import log

    worktrees: dict[int, tuple[Path, str]] = {}
    failed = False

    for stage in group:
        try:
            wt_dir, branch = create_worktree(
                project_dir,
                f"stage-{stage.index}",
            )
            worktrees[stage.index] = (wt_dir, branch)
            log.tprint(
                f"[orchestrator] Worktree for stage {stage.index}: {wt_dir}",
            )
        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            OSError,
        ) as exc:
            log.tprint(
                f"⚠️  [orchestrator] Worktree creation failed for "
                f"stage {stage.index}: {exc}",
            )
            failed = True

    return worktrees, failed


def run_group_sequentially(
    orchestrator: "OrchestratorBase",
    group: list[GoalStage],
    plan: GoalPlan,
    project_dir: Path,
    stage_teams: dict[int, TeamConfig],
    stage_summaries: list[str],
    result: RunResult,
    worktrees: dict[int, tuple[Path, str]],
    *,
    max_exchanges: int,
    per_stage_cycles: int,
    initial_prior: str,
    config: CycleConfig,
    advisory_queue=None,
    observer=None,
) -> tuple[list[StageResult], int]:
    """Fallback: run parallel stages sequentially when worktree creation fails.

    Cleans up any partially-created worktrees, then runs each stage in
    sequence using the main project directory.

    Returns ``(parallel_results, cycles_used)``.
    """
    from kodo import log

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

    parallel_results: list[StageResult] = []
    for stage in group:
        # Build stage-specific config preserving all fields from original config
        # but setting auto_commit based on persist_changes
        stage_config = CycleConfig(
            browser_testing=config.browser_testing,
            verifiers=config.verifiers,
            auto_commit=(stage.persist_changes and config.auto_commit),
            verification=config.verification,
            acceptance_criteria=config.acceptance_criteria,
            effort=config.effort,
            done_mode=config.done_mode,
        )
        stage_res = orchestrator._run_one_stage(
            stage,
            plan,
            project_dir,
            stage_teams[stage.index],
            stage_summaries,
            max_exchanges=max_exchanges,
            max_cycles_for_stage=per_stage_cycles,
            initial_prior_summary=initial_prior,
            config=stage_config,
            advisory_queue=advisory_queue,
            observer=observer,
        )
        parallel_results.append(stage_res)
        result.cycles.extend(stage_res.cycles)
        result.stage_results.append(stage_res)
        stage_summaries.append(stage_res.summary)

    cycles_used = max(
        (len(r.cycles) for r in parallel_results),
        default=0,
    )
    return parallel_results, cycles_used


@contextmanager
def _suppress_keyboard_interrupt():
    """Defer ``KeyboardInterrupt`` during critical cleanup.

    SIGINT is masked while the context is active.  If a signal arrives
    during that window it is re-raised on exit so the caller still sees
    the interrupt — but only *after* cleanup has finished.

    When the signal handler cannot be changed, such as from a non-main
    thread, this becomes a no-op and cleanup proceeds unprotected.
    """
    interrupted = False
    original_handler = None

    def _deferred_handler(signum, frame):  # noqa: ARG001
        nonlocal interrupted
        interrupted = True

    try:
        # signal.signal can only be called from the main thread
        original_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, _deferred_handler)
    except (OSError, ValueError):
        # Not main thread, or signals not supported
        pass

    try:
        yield
    finally:
        if original_handler is not None:
            try:
                signal.signal(signal.SIGINT, original_handler)
            except (OSError, ValueError):
                pass
        if interrupted:
            # Re-raise through Python's signal machinery so this stays
            # portable on Windows, where os.kill(..., SIGINT) terminates
            # the process instead of delivering an in-process SIGINT.
            signal.raise_signal(signal.SIGINT)


def cleanup_and_merge_worktrees(
    group: list[GoalStage],
    worktrees: dict[int, tuple[Path, str]],
    stage_teams: dict[int, TeamConfig],
    parallel_results: list[StageResult],
    project_dir: Path,
) -> None:
    """Clean up cloned sessions and worktrees; merge persist_changes branches.

    Called in a ``finally`` block to ensure cleanup even on
    ``KeyboardInterrupt``.  SIGINT is deferred for the duration of this
    function so a second Ctrl-C cannot leave worktrees or branches
    behind.  The signal is re-raised on exit.

    Steps:

    1. Commit uncommitted changes in ``persist_changes`` worktrees.
    2. Close cloned sessions.
    3. Remove worktrees (keeping branches that need merging).
    4. Merge ``persist_changes`` branches sequentially.
    """
    with _suppress_keyboard_interrupt():
        _cleanup_and_merge_worktrees_inner(
            group,
            worktrees,
            stage_teams,
            parallel_results,
            project_dir,
        )


def _cleanup_and_merge_worktrees_inner(
    group: list[GoalStage],
    worktrees: dict[int, tuple[Path, str]],
    stage_teams: dict[int, TeamConfig],
    parallel_results: list[StageResult],
    project_dir: Path,
) -> None:
    """Actual cleanup logic — called inside SIGINT suppression."""
    from kodo import log

    stages_by_idx = {s.index: s for s in group}
    finished_indices = (
        {pr.stage_index for pr in parallel_results if pr.finished}
        if parallel_results
        else set()
    )

    # 1. Commit uncommitted changes in persist_changes worktrees
    branches_to_merge: list[tuple[str, str, int]] = []
    for stage_idx, (wt_dir, branch) in worktrees.items():
        stg = stages_by_idx.get(stage_idx)
        if stg and stg.persist_changes and stage_idx in finished_indices:
            try:
                commit_worktree_changes(wt_dir, stg.name)
                branches_to_merge.append((branch, stg.name, stage_idx))
            except BaseException as exc:
                log.tprint(
                    f"[persist] Commit failed for stage {stage_idx}: {exc}",
                )

    # 2. Close cloned sessions — wrap each in try/except so one failure
    #    doesn't prevent closing the rest.
    for st in stage_teams.values():
        for agent in st.values():
            try:
                agent.close()
            except BaseException:
                pass

    # 3. Remove worktrees — keep branches that need merging
    branches_to_keep = {b for b, _, _ in branches_to_merge}
    for stage_idx, (wt_dir, branch) in worktrees.items():
        try:
            if branch in branches_to_keep:
                _remove_worktree_keep_branch(project_dir, wt_dir)
            else:
                remove_worktree(project_dir, wt_dir, branch)
        except BaseException as exc:
            log.tprint(
                f"[orchestrator] Worktree cleanup failed for stage {stage_idx}: {exc}",
            )

    # 4. Merge persist_changes branches sequentially
    branches_to_merge.sort(key=lambda x: x[2])
    for branch, stage_name, stage_idx in branches_to_merge:
        try:
            merge_result = merge_worktree_branch(
                project_dir,
                branch,
                stage_name,
            )
            log.emit(
                "persist_stage_merge",
                stage_index=stage_idx,
                success=merge_result.success,
                had_changes=merge_result.had_changes,
                conflict=merge_result.conflict,
            )
        except BaseException as exc:
            log.tprint(
                f"[persist] Merge failed for stage {stage_idx}: {exc}",
            )
        finally:
            # Always clean up the branch after merge attempt
            try:
                subprocess.run(
                    [_GIT, "branch", "-D", branch],
                    cwd=project_dir,
                    capture_output=True,
                    timeout=_GIT_TIMEOUT,
                )
            except BaseException:
                pass
