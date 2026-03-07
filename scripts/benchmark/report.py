"""Generate benchmark report from results and evaluation data."""

from __future__ import annotations

import json
import re
from pathlib import Path


def generate_report(workspace: Path, run_id: str) -> int:
    """Generate and print a benchmark report. Returns 0 on success."""
    run_dir = workspace / "runs" / run_id

    meta = _load_json(run_dir / "meta.json")
    eval_summary = _load_json(run_dir / "eval-summary.json")
    results = _load_jsonl(run_dir / "results.jsonl")

    lines: list[str] = []
    dataset_label = meta.get("dataset", "").rsplit("/", 1)[-1] or "SWE-bench"
    lines.append(f"# {dataset_label} Benchmark Report")
    lines.append(f"Run: {run_id}")
    lines.append(f"Tasks: {meta.get('task_count', '?')}")
    lines.append("")

    arms = meta.get("arms", [])

    def _eval_key(arm: str) -> str:
        """Map arm name to eval-summary key (sanitized for Docker container names)."""
        return re.sub(r"[^a-zA-Z0-9_.-]", "_", arm)

    # Resolution rates (only if eval was run)
    if eval_summary:
        lines.append("## Resolution Rates")
        lines.append("")
        lines.append("| Arm | Resolved | Failed | Error | Rate |")
        lines.append("|-----|----------|--------|-------|------|")
        for arm in arms:
            e = eval_summary.get(_eval_key(arm), {})
            r, f, err = (
                len(e.get("resolved", [])),
                len(e.get("failed", [])),
                len(e.get("error", [])),
            )
            rate = e.get("resolve_rate", 0)
            lines.append(f"| {arm} | {r} | {f} | {err} | {rate:.1%} |")
        lines.append("")

    # Timing stats
    lines.append("## Timing")
    lines.append("")
    for arm in arms:
        arm_results = [r for r in results if r.get("arm") == arm]
        times = [r["elapsed_s"] for r in arm_results if r.get("elapsed_s")]
        if times:
            lines.append(
                f"- **{arm}**: median={_median(times):.0f}s, "
                f"mean={sum(times) / len(times):.0f}s, "
                f"p90={_percentile(times, 90):.0f}s, "
                f"total={sum(times) / 3600:.1f}h"
            )
    lines.append("")

    # Status breakdown
    lines.append("## Status Breakdown")
    lines.append("")
    for arm in arms:
        arm_results = [r for r in results if r.get("arm") == arm]
        statuses: dict[str, int] = {}
        for r in arm_results:
            s = r.get("status", "unknown")
            statuses[s] = statuses.get(s, 0) + 1
        if statuses:
            parts = ", ".join(f"{k}={v}" for k, v in sorted(statuses.items()))
            lines.append(f"- **{arm}**: {parts}")
    lines.append("")

    # Head-to-head (pairwise comparison for all arm pairs)
    eval_arms = [a for a in arms if _eval_key(a) in eval_summary]
    if len(eval_arms) >= 2:
        lines.append("## Head-to-Head")
        lines.append("")
        for i, arm_a in enumerate(eval_arms):
            for arm_b in eval_arms[i + 1 :]:
                set_a = set(eval_summary[_eval_key(arm_a)].get("resolved", []))
                set_b = set(eval_summary[_eval_key(arm_b)].get("resolved", []))
                both = set_a & set_b
                only_a = set_a - set_b
                only_b = set_b - set_a
                lines.append(f"### {arm_a} vs {arm_b}")
                lines.append(f"- Both resolved: {len(both)}")
                lines.append(f"- {arm_a} only: {len(only_a)}")
                if only_a:
                    for tid in sorted(only_a):
                        lines.append(f"  - {tid}")
                lines.append(f"- {arm_b} only: {len(only_b)}")
                if only_b:
                    for tid in sorted(only_b):
                        lines.append(f"  - {tid}")
                lines.append("")

    report_text = "\n".join(lines)

    report_file = run_dir / "report.md"
    report_file.write_text(report_text)
    print(report_text)
    print(f"\nReport written to: {report_file}")
    return 0


def _load_json(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _load_jsonl(path: Path) -> list[dict]:
    results: list[dict] = []
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    results.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return results


def _median(values: list[float]) -> float:
    s = sorted(values)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2


def _percentile(values: list[float], p: int) -> float:
    s = sorted(values)
    idx = int(len(s) * p / 100)
    return s[min(idx, len(s) - 1)]
