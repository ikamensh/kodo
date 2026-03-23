"""System-generated run status file for orchestrator awareness."""

from __future__ import annotations

from pathlib import Path

from kodo import log


def _fmt_time(s: float) -> str:
    """Format seconds as human-readable time."""
    if s < 60:
        return f"{s:.0f}s"
    m = int(s // 60)
    sec = s % 60
    if m < 60:
        return f"{m}m{sec:02.0f}s"
    return f"{m // 60}h{m % 60:02d}m"


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.0f}k"
    return str(n)


def write_run_status(
    project_dir: Path,
    goal: str,
    *,
    stage_label: str = "",
    cycle_num: int = 0,
    max_cycles: int = 0,
) -> str:
    """Write .kodo/run-status.md with structured run progress. Returns content."""
    # Update the live progress state for the interactive toolbar
    log.get_run_progress().update(
        cycle=cycle_num,
        max_cycles=max_cycles,
        stage_label=stage_label,
    )

    lines: list[str] = ["# Run Status", ""]

    # Goal (truncated)
    truncated_goal = goal[:500] + ("..." if len(goal) > 500 else "")
    lines += ["## Goal", truncated_goal, ""]

    # Progress
    lines.append("## Progress")
    if stage_label:
        lines.append(f"- Stage: {stage_label}")
    if cycle_num and max_cycles:
        lines.append(f"- Cycle: {cycle_num}/{max_cycles}")
    elapsed = log.get_elapsed_s()
    if elapsed is not None:
        lines.append(f"- Elapsed: {_fmt_time(elapsed)}")
    lines.append("")

    # Agent stats table
    stats = log.get_run_stats()
    agents, _orch_cost, _orch_bucket = stats.snapshot()
    if agents:
        lines += [
            "## Agent Stats",
            "| Agent | Calls | Errors | Tokens | Time |",
            "|-------|-------|--------|--------|------|",
        ]
        for name, s in sorted(agents.items()):
            total_tok = _fmt_tokens(s.input_tokens + s.output_tokens)
            lines.append(
                f"| {name} | {s.calls} | {s.errors} | {total_tok} | {_fmt_time(s.elapsed_s)} |"
            )
        lines.append("")

    content = "\n".join(lines)

    status_file = project_dir / ".kodo" / "run-status.md"
    status_file.parent.mkdir(parents=True, exist_ok=True)
    status_file.write_text(content, encoding="utf-8")

    return content


def read_run_status(project_dir: Path) -> str:
    """Read .kodo/run-status.md content, or empty string if missing."""
    status_file = project_dir / ".kodo" / "run-status.md"
    try:
        return status_file.read_text(encoding="utf-8")
    except (OSError, FileNotFoundError):
        return ""
