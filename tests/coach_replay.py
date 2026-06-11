"""Replay harness — feed historical log files through the Coach to see what it flags.

Usage:
    # Replay a specific log file
    uv run python -m tests.coach_replay ~/.kodo/runs/20260321_213413/log.jsonl

    # Replay the N most recent runs
    uv run python -m tests.coach_replay --recent 5

    # Replay with custom tenets
    uv run python -m tests.coach_replay --tenets .kodo/tenets.md ~/.kodo/runs/*/log.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _extract_events(log_path: Path) -> list[dict]:
    """Parse a JSONL log file and extract tool call/result pairs."""
    events = []
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("event") in (
                "orchestrator_tool_call",
                "orchestrator_tool_result",
                "run_start",
            ):
                events.append(entry)
    return events


def replay(
    log_path: Path,
    *,
    tenets: str | None = None,
    assess_every_n: int = 3,
    model: str | None = None,
    mode: str | None = None,
    skip: int = 0,
    dual: bool = False,
    filtered: bool = False,
) -> list[dict]:
    """Replay a log file through the Coach. Returns list of coach messages."""
    from kodo.advisory import AdvisoryQueue
    from kodo.coach import Coach, FilteredCoach

    events = _extract_events(log_path)
    if not events:
        print(f"  No tool events found in {log_path}")
        return []

    # Extract goal from run_start event
    goal = "unknown"
    for e in events:
        if e.get("event") == "run_start":
            goal = e.get("goal", "unknown")
            break

    queue = AdvisoryQueue()
    if filtered:
        coach_cls = FilteredCoach
    else:
        coach_cls = Coach
    coach = coach_cls(
        queue,
        goal,
        Path("."),  # doesn't matter for replay
        assess_every_n=assess_every_n,
        model=model,
        tenets=tenets,
        mode=mode,
    )

    # Pair up tool calls and results
    dispatches = [e for e in events if e["event"] == "orchestrator_tool_call"]
    results = [e for e in events if e["event"] == "orchestrator_tool_result"]

    print(f"\n{'=' * 70}")
    print(f"Log: {log_path}")
    print(f"Goal: {goal[:100]}")
    print(f"Dispatches: {len(dispatches)}, Results: {len(results)}")
    print(f"Assess every: {assess_every_n} dispatches")
    print(f"{'=' * 70}")

    # Override _maybe_assess to not spawn threads — we call _assess synchronously
    coach._maybe_assess = lambda: None

    messages = []
    result_idx = 0

    def _drain_and_print(context_dispatch: int, context_t: float):
        drained = queue.drain()
        for adv in drained:
            msg = {
                "dispatch_num": context_dispatch,
                "time_s": context_t,
                "priority": adv.priority,
                "message": adv.message,
                "source": adv.source,
            }
            messages.append(msg)
            icon = {"info": "ℹ️", "warning": "⚠️", "correction": "🚨"}[adv.priority]
            print(f"  {icon}  COACH [{adv.priority}]: {adv.message}")

    for i, dispatch in enumerate(dispatches):
        agent = dispatch.get("agent", "?")
        task = dispatch.get("task", "?")
        t = dispatch.get("t", 0)

        # Record dispatch
        coach._dispatches.append(
            {"agent": agent, "task": task[:500], "t": t}
        )

        # Find matching result
        report = ""
        is_error = False
        elapsed = 0.0
        if result_idx < len(results):
            r = results[result_idx]
            report = r.get("report", "")
            is_error = r.get("is_error", False)
            elapsed = r.get("elapsed_s", 0)
            result_idx += 1

        coach.record_result(agent, task, is_error, report=report)

        status = "ERROR" if is_error else "ok"
        print(f"\n  [{t:7.1f}s] #{i+1} ask_{agent}: {task[:80]}...")
        print(f"           → {status} ({elapsed:.1f}s, {len(report)} chars)")

        # Assess after every result (coach maintains conversational state)
        if i >= skip:
            coach._assess()
            _drain_and_print(i + 1, t)
        else:
            print(f"           (skipped — dispatch #{i+1} < --skip {skip})")

        # Note: after the first message, subsequent coach feedback is only
        # conditionally correct — in a real run the orchestrator would have
        # reacted, changing the trajectory. We continue anyway to see what
        # patterns the coach catches across the full run.
        if messages and len(messages) == 1:
            print(f"\n  ── coach spoke. Subsequent feedback assumes orchestrator didn't react. ──")

    if not messages:
        print("\n  Coach stayed silent — no issues detected.")

    return messages


def _find_recent_logs(n: int = 5) -> list[Path]:
    """Find the N most recent kodo log files, biggest first."""
    runs_dir = Path.home() / ".kodo" / "runs"
    if not runs_dir.exists():
        return []
    logs = sorted(runs_dir.glob("*/log.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    # Filter to logs with at least a few tool events (> 5K)
    logs = [l for l in logs if l.stat().st_size > 5000]
    return logs[:n]


def main():
    parser = argparse.ArgumentParser(description="Replay kodo logs through the Coach")
    parser.add_argument("logs", nargs="*", help="Log file paths to replay")
    parser.add_argument("--recent", type=int, default=0, help="Replay N most recent runs")
    parser.add_argument("--tenets", type=str, default=None, help="Path to custom tenets file")
    parser.add_argument("--every", type=int, default=3, help="Assess every N dispatches (default: 3)")
    parser.add_argument("--model", type=str, default=None, help="Override coach LLM model")
    parser.add_argument("--mode", type=str, default=None, choices=["test", "improve"], help="Use mode-specific tenets")
    parser.add_argument("--skip", type=int, default=0, help="Skip first N dispatches (record but don't assess)")
    parser.add_argument("--dual", action="store_true", help="Use DualCoach (two models + veto)")
    parser.add_argument("--filtered", action="store_true", help="Use FilteredCoach (single coach + filter gate)")
    args = parser.parse_args()

    # Resolve tenets
    tenets = None
    if args.tenets:
        tenets_path = Path(args.tenets)
        if tenets_path.is_file():
            tenets = tenets_path.read_text().strip()
        else:
            print(f"Error: tenets file not found: {args.tenets}", file=sys.stderr)
            sys.exit(1)

    # Collect log paths
    log_paths: list[Path] = []
    if args.recent > 0:
        log_paths = _find_recent_logs(args.recent)
        if not log_paths:
            print("No recent kodo runs found in ~/.kodo/runs/", file=sys.stderr)
            sys.exit(1)
    for p in args.logs:
        log_paths.append(Path(p))

    if not log_paths:
        parser.print_help()
        sys.exit(1)

    # Check API key
    has_key = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
    if not has_key:
        print("Warning: No GEMINI_API_KEY/GOOGLE_API_KEY — coach assessment will be skipped", file=sys.stderr)

    all_messages = []
    for log_path in log_paths:
        if not log_path.exists():
            print(f"Warning: {log_path} not found, skipping", file=sys.stderr)
            continue
        messages = replay(
            log_path,
            tenets=tenets,
            assess_every_n=args.every,
            model=args.model,
            mode=args.mode,
            skip=args.skip,
            dual=args.dual,
            filtered=args.filtered,
        )
        all_messages.extend(messages)

    # Summary
    print(f"\n{'=' * 70}")
    print(f"SUMMARY: {len(all_messages)} coach message(s) across {len(log_paths)} run(s)")
    if all_messages:
        by_priority = {}
        for m in all_messages:
            by_priority.setdefault(m["priority"], []).append(m)
        for priority in ("correction", "warning", "info"):
            msgs = by_priority.get(priority, [])
            if msgs:
                icon = {"info": "ℹ️", "warning": "⚠️", "correction": "🚨"}[priority]
                print(f"\n  {icon}  {priority.upper()} ({len(msgs)}):")
                for m in msgs:
                    print(f"     - {m['message'][:120]}")
    print()


if __name__ == "__main__":
    main()
