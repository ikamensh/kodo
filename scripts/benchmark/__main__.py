"""SWE-bench benchmark: kodo vs raw Claude Code.

Uses SWE-bench Pro (731 tasks) by default. Pass --dataset lite for SWE-bench Lite.

Arms are specified as strings. "claude" runs raw Claude Code CLI.
"kodo" runs kodo with the default team. "kodo:quick" or "kodo:full"
runs kodo with a specific --team flag.

Usage:
    uv run python -m scripts.benchmark --limit 5
    uv run python -m scripts.benchmark --arm kodo:quick --arm claude
    uv run python -m scripts.benchmark --language python --limit 20
    uv run python -m scripts.benchmark --dataset lite --limit 10
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

WORKSPACE = Path.home() / ".kodo" / "benchmark"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="SWE-bench benchmark: kodo vs raw Claude Code",
    )

    # Dataset and task selection
    parser.add_argument(
        "--dataset",
        choices=["pro", "lite"],
        default="pro",
        help="SWE-bench variant (default: pro)",
    )
    parser.add_argument("--limit", type=int, default=None, help="Run first N tasks")
    parser.add_argument(
        "--instance-ids", nargs="+", default=None, help="Specific instance IDs"
    )
    parser.add_argument(
        "--repo",
        type=str,
        default=None,
        help="Filter to repo (e.g. 'ansible/ansible')",
    )
    parser.add_argument(
        "--language",
        type=str,
        default=None,
        help="Filter by language (e.g. 'python', 'go', 'js'). Pro only.",
    )
    parser.add_argument("--offset", type=int, default=0, help="Skip first N tasks")

    # Arm selection
    parser.add_argument(
        "--arm",
        action="append",
        default=None,
        help="Arm to benchmark. Repeatable. 'claude' for raw Claude Code, "
        "'kodo' for default team, 'kodo:<team>' for a specific team "
        "(e.g. 'kodo:quick', 'kodo:full'). Default: claude + kodo.",
    )

    # Execution
    parser.add_argument(
        "--timeout",
        type=int,
        default=1800,
        help="Per-task timeout in seconds (default: 1800)",
    )
    parser.add_argument(
        "--workspace", type=Path, default=WORKSPACE, help="Workspace directory"
    )
    parser.add_argument(
        "--run-id", type=str, default=None, help="Resume or reference a run ID"
    )
    parser.add_argument(
        "--parallel", type=int, default=1, help="Concurrent tasks (default: 1)"
    )

    # Phase control
    parser.add_argument(
        "--skip-eval", action="store_true", help="Skip swebench evaluation"
    )
    parser.add_argument(
        "--evaluate-only",
        action="store_true",
        help="Only evaluate existing predictions",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="Only generate report from existing results",
    )

    args = parser.parse_args()
    workspace: Path = args.workspace
    workspace.mkdir(parents=True, exist_ok=True)

    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    arms = args.arm if args.arm else ["claude", "kodo"]

    from scripts.benchmark.evaluate import evaluate_predictions
    from scripts.benchmark.report import generate_report

    if args.report_only:
        return generate_report(workspace, run_id)

    if args.evaluate_only:
        evaluate_predictions(workspace, run_id)
        return generate_report(workspace, run_id)

    # Run agents
    from scripts.benchmark.runner import run_benchmark
    from scripts.benchmark.tasks import DATASET_LITE, DATASET_PRO, load_tasks

    dataset = DATASET_PRO if args.dataset == "pro" else DATASET_LITE
    tasks = load_tasks(
        dataset=dataset,
        limit=args.limit,
        instance_ids=args.instance_ids,
        repo_filter=args.repo,
        language=args.language,
        offset=args.offset,
    )

    if not tasks:
        print("No tasks matched the filters.")
        return 1

    print(f"Loaded {len(tasks)} tasks")

    run_benchmark(
        tasks=tasks,
        arms=arms,
        workspace=workspace,
        run_id=run_id,
        timeout=args.timeout,
        parallel=args.parallel,
    )

    if not args.skip_eval:
        evaluate_predictions(workspace, run_id)

    return generate_report(workspace, run_id)


if __name__ == "__main__":
    sys.exit(main())
