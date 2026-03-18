"""Task distribution logic for central benchmark coordination.

Pure functions for prioritizing task assignments. Used by the server
to decide which (instance_id, arm) pairs a contributor should work on.

Priority algorithm:
1. Find (instance_id, arm) gaps: tasks where requested backends haven't been evaluated
2. Score by OTHER backend coverage (more = higher comparison value)
3. Penalize arms with high active contributor pressure (steer toward underrepresented arms)
4. Exclude actively claimed pairs
5. Return top N assignments, sorted deterministically
"""

from __future__ import annotations


def prioritize_assignments(
    *,
    all_instance_ids: list[str],
    results: dict[str, dict[str, dict]],
    backends: list[str],
    active_claims: set[tuple[str, str]],
    arm_pressure: dict[str, int] | None = None,
    limit: int = 20,
) -> list[dict]:
    """Return prioritized (instance_id, arm) assignments.

    Args:
        all_instance_ids: Full list of task IDs in the dataset (from client).
        results: {instance_id: {arm: result_data}} from existing evaluations.
        backends: Backends the contributor can run.
        active_claims: (instance_id, arm) pairs currently claimed by others.
        arm_pressure: {arm: active_contributor_count} — soft signal from recent
            activity (both explicit distribute requests and implicit uploads).
            Arms with more active contributors are deprioritized so new work
            is steered toward underrepresented arms.
        limit: Max assignments to return.

    Returns:
        List of {"instance_id": ..., "arm": ...} dicts, ordered by priority.
    """
    pressure = arm_pressure or {}
    candidates: list[tuple[int, int, str, str]] = []

    for iid in all_instance_ids:
        task_results = results.get(iid, {})
        existing_arms = set(task_results.keys())

        for backend in backends:
            if backend in existing_arms:
                continue  # already evaluated
            if (iid, backend) in active_claims:
                continue  # someone else is working on it

            # Score: number of OTHER backends that have evaluated this task.
            # Higher = more comparison value (the key insight from GOALS.md).
            other_coverage = len(existing_arms)
            # Pressure: active contributors on this arm (lower = more attractive).
            arm_load = pressure.get(backend, 0)
            candidates.append((other_coverage, arm_load, iid, backend))

    # Sort: most other coverage first, then least arm pressure, then deterministic.
    candidates.sort(key=lambda c: (-c[0], c[1], c[2], c[3]))

    return [{"instance_id": iid, "arm": arm} for _, _, iid, arm in candidates[:limit]]
