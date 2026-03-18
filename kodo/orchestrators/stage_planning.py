"""Stage-level planning helpers: goal composition, team cloning, grouping."""

from __future__ import annotations

from kodo.orchestrators.types import (
    GoalPlan,
    GoalStage,
    StageResult,
    TeamConfig,
)


def _handle_stage_crash(stage: GoalStage, exc: Exception) -> StageResult:
    """Log and wrap a stage crash into a StageResult."""
    from kodo import log

    log.tprint(
        f"[orchestrator] Stage {stage.index} ({stage.name}) crashed: {exc}",
    )
    log.emit("stage_error", stage_index=stage.index, error=str(exc))
    return StageResult(
        stage_index=stage.index,
        stage_name=stage.name,
        success=False,
        summary=f"Stage crashed: {exc}",
    )


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
