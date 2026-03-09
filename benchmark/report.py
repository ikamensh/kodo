"""Generate benchmark report from results and evaluation data."""

from __future__ import annotations

import json
import re
from datetime import datetime
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


def _dataset_short(dataset: str) -> str:
    """Map full dataset name to short label."""
    low = dataset.lower()
    if "verified" in low:
        return "Verified"
    if "pro" in low:
        return "Pro"
    if "lite" in low:
        return "Lite"
    return dataset.rsplit("/", 1)[-1]


def _eval_key(arm: str) -> str:
    """Map arm name to eval-summary key (sanitized for Docker container names)."""
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", arm)


def print_status(workspace: Path) -> int:
    """Scan all runs and print a compact status table. Returns 0."""
    runs_dir = workspace / "runs"
    if not runs_dir.is_dir():
        print("No runs found.")
        return 0

    run_dirs = sorted(
        [d for d in runs_dir.iterdir() if d.is_dir()],
        key=lambda d: d.name,
    )
    if not run_dirs:
        print("No runs found.")
        return 0

    # Collect row data
    rows: list[dict] = []
    total_evaluated: set[str] = set()

    for rd in run_dirs:
        meta = _load_json(rd / "meta.json")
        if not meta:
            continue

        arms = meta.get("arms", [])
        task_count = meta.get("task_count", 0)
        dataset = _dataset_short(meta.get("dataset", ""))

        # Count completed results
        results = _load_jsonl(rd / "results.jsonl")
        # Unique instance_ids that have results (across all arms)
        done_ids = {r["instance_id"] for r in results if "instance_id" in r}
        done_count = len(done_ids)

        # Eval info
        eval_summary = _load_json(rd / "eval-summary.json")
        has_eval = bool(eval_summary)
        rates: list[str] = []
        if has_eval:
            for arm in arms:
                e = eval_summary.get(_eval_key(arm), {})
                rate = e.get("resolve_rate", 0)
                rates.append(f"{rate:.0%}")
            # Count evaluated tasks
            for arm in arms:
                e = eval_summary.get(_eval_key(arm), {})
                total_evaluated.update(e.get("resolved", []))
                total_evaluated.update(e.get("failed", []))
                total_evaluated.update(e.get("error", []))

        # Date from meta.json mtime
        meta_path = rd / "meta.json"
        mtime = meta_path.stat().st_mtime
        date_str = datetime.fromtimestamp(mtime).strftime("%b %d")

        rows.append(
            {
                "run_id": rd.name,
                "arms": ",".join(arms),
                "dataset": dataset,
                "tasks": f"{done_count}/{task_count}",
                "done": str(done_count),
                "eval": "\u2713" if has_eval else "-",
                "rate": ",".join(rates) if rates else "-",
                "date": date_str,
            }
        )

    # Column definitions: (header, key, min_width)
    columns = [
        ("Run ID", "run_id", 10),
        ("Arms", "arms", 10),
        ("Dataset", "dataset", 8),
        ("Tasks", "tasks", 5),
        ("Done", "done", 4),
        ("Eval", "eval", 4),
        ("Rate", "rate", 5),
        ("Date", "date", 6),
    ]

    # Calculate column widths
    widths: list[int] = []
    for header, key, min_w in columns:
        w = max(min_w, len(header), *(len(r[key]) for r in rows))
        widths.append(w)

    total_width = sum(widths) + 2 * (len(columns) - 1)  # 2-space gap

    # Print header
    print()
    print("SWE-bench Benchmark Status")
    print("\u2550" * total_width)

    header_parts = []
    for i, (header, _, _) in enumerate(columns):
        header_parts.append(header.ljust(widths[i]))
    print("  ".join(header_parts))
    print("\u2500" * total_width)

    # Print rows
    for row in rows:
        parts = []
        for i, (_, key, _) in enumerate(columns):
            parts.append(row[key].ljust(widths[i]))
        print("  ".join(parts))

    print("\u2500" * total_width)
    print(f"{len(rows)} runs | {len(total_evaluated)} unique tasks evaluated")
    print()
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
