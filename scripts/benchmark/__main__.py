"""SWE-bench benchmark: kodo vs raw Claude Code / Cursor / Codex / Gemini.

Uses SWE-bench Pro (731 tasks) by default. Pass --dataset lite for SWE-bench Lite.

Arms: "claude", "cursor", "codex", "gemini", "kodo", "kodo:<team>".

Usage:
    uv run python -m scripts.benchmark --subset scripts/benchmark/subsets/pro-20.json
    uv run python -m scripts.benchmark --subset scripts/benchmark/subsets/pro-20.json --arm kodo:solo --limit 2
    uv run python -m scripts.benchmark --arm cursor --arm kodo:solo --limit 2 --skip-eval
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
        choices=["pro", "verified", "lite"],
        default="pro",
        help="SWE-bench variant (default: pro)",
    )
    parser.add_argument(
        "--subset",
        type=Path,
        default=None,
        help="Path to a subset JSON file (e.g. subsets/pro-20.json). "
        "Overrides --dataset and --instance-ids.",
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
        help="Arm to benchmark. Repeatable. 'claude', 'cursor', 'codex', "
        "'gemini' for raw CLI tools; 'kodo' for default team, "
        "'kodo:<team>' for a specific team (e.g. 'kodo:quick'). "
        "Default: claude + kodo.",
    )

    # Execution
    parser.add_argument(
        "--timeout",
        type=int,
        default=7200,
        help="Per-task timeout for non-orchestrated arms in seconds (default: 7200 / 2h)",
    )
    parser.add_argument(
        "--timeout-kodo",
        type=int,
        default=43200,
        help="Per-task timeout for kodo arms in seconds (default: 43200 / 12h)",
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
    import json as _json

    from scripts.benchmark.runner import run_benchmark
    from scripts.benchmark.tasks import DATASET_LITE, DATASET_PRO, DATASET_VERIFIED, load_tasks

    # Resolve dataset and instance_ids from --subset if provided
    instance_ids = args.instance_ids
    _DATASET_MAP = {"pro": DATASET_PRO, "verified": DATASET_VERIFIED, "lite": DATASET_LITE}
    dataset = _DATASET_MAP[args.dataset]
    if args.subset:
        subset_data = _json.loads(args.subset.read_text())
        instance_ids = subset_data["instance_ids"]
        dataset = subset_data.get("dataset", dataset)

    tasks = load_tasks(
        dataset=dataset,
        limit=args.limit,
        instance_ids=instance_ids,
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
        timeout_kodo=args.timeout_kodo,
        parallel=args.parallel,
        dataset=dataset,
    )

    if not args.skip_eval:
        evaluate_predictions(workspace, run_id)

    return generate_report(workspace, run_id)


if __name__ == "__main__":
    sys.exit(main())
