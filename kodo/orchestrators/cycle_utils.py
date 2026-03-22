"""Cycle-level helpers shared across orchestrator implementations."""

from __future__ import annotations

from pathlib import Path

from kodo.orchestrators.types import CycleResult, DoneSignal


def apply_done_signal(result: CycleResult, done_signal: DoneSignal) -> None:
    """Translate DoneSignal state into CycleResult fields.

    - ``goal_done``: finished=True, success=True
    - ``end_cycle``: finished=False, success=False (run continues)
    - ``raise_issue``: finished=True, success=False
    - ``legacy``: finished=True, success=done_signal.success
    - Not called: no-op
    """
    if not done_signal.called:
        return
    result.summary = done_signal.summary
    terminal = done_signal.terminal
    if terminal == "end_cycle":
        result.finished = False
        result.success = False
    elif terminal == "raise_issue":
        result.finished = True
        result.success = False
    elif terminal == "goal_done":
        result.finished = True
        result.success = True
    else:
        # legacy or unknown
        result.finished = True
        result.success = done_signal.success


def build_cycle_prompt(
    goal: str,
    project_dir: Path,
    prior_summary: str = "",
    advisory_queue=None,
) -> str:
    """Build the user-turn prompt sent to the orchestrator each cycle."""
    from kodo.orchestrators.run_status import read_run_status

    prompt = f"# Goal\n\n{goal}\n\nProject directory: {project_dir}"

    run_status = read_run_status(project_dir)
    if run_status:
        prompt += f"\n\n{run_status}"

    if prior_summary:
        prompt += (
            f"\n\n# Previous progress\n\n{prior_summary}"
            "\n\nContinue working toward the goal."
        )

    # Inject strategic-level advisories between cycles
    if advisory_queue is not None and advisory_queue.pending_count > 0:
        from kodo.advisory import format_advisories_for_prompt

        advisories = advisory_queue.drain()
        if advisories:
            prompt += f"\n\n{format_advisories_for_prompt(advisories)}"

    return prompt
