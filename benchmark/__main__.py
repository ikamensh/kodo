"""SWE-bench benchmark: kodo vs raw Claude Code / Cursor / Codex / Gemini.

Uses SWE-bench Pro (731 tasks) by default. Pass --dataset lite for SWE-bench Lite.

Arms: "claude", "cursor", "codex", "gemini", "kodo", "kodo:<team>".

Usage:
    uv run python -m benchmark --subset benchmark/subsets/pro-20.json
    uv run python -m benchmark --subset benchmark/subsets/pro-20.json --arm kodo:solo --limit 2
    uv run python -m benchmark --arm cursor --arm kodo:solo --limit 2 --skip-eval
"""

from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from benchmark._util import log, setup_logging

WORKSPACE = Path.home() / ".kodo" / "benchmark"


def main() -> int:
    """CLI entrypoint for the SWE-bench benchmark harness."""
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
        "--status",
        action="store_true",
        help="Show status of all benchmark runs and exit",
    )
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
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Publish results to GitHub Pages for the online viewer",
    )
    parser.add_argument(
        "--extract-patch",
        nargs=2,
        metavar=("INSTANCE_ID", "ARM"),
        help="Print a patch from published data",
    )
    parser.add_argument(
        "--upload-pending",
        action="store_true",
        help="Upload results not yet sent to the online server (requires KODO_BENCH_URL/TOKEN)",
    )

    args = parser.parse_args()
    setup_logging()
    workspace: Path = args.workspace
    workspace.mkdir(parents=True, exist_ok=True)

    # UTC timestamp as run ID
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    arms = args.arm if args.arm else ["claude", "kodo"]

    if args.status:
        from benchmark.report import print_status

        return print_status(workspace)

    if args.publish:
        from benchmark.online.publish import publish_results

        return publish_results(workspace, run_id=args.run_id)

    if args.extract_patch:
        from benchmark.online.publish import extract_patch

        return extract_patch(args.extract_patch[0], args.extract_patch[1])

    if args.upload_pending:
        from benchmark.online.upload_tracker import flush_pending_uploads

        return flush_pending_uploads(workspace)

    from benchmark.evaluate import evaluate_predictions
    from benchmark.report import generate_report

    if args.report_only:
        return generate_report(workspace, run_id)

    if args.evaluate_only:
        evaluate_predictions(workspace, run_id)
        return generate_report(workspace, run_id)

    # Run agents
    import json as _json

    from benchmark.runner import run_benchmark
    from benchmark.tasks import DATASET_LITE, DATASET_PRO, DATASET_VERIFIED, load_tasks

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
        log.error("No tasks matched the filters.")
        return 1

    log.info("Loaded %d tasks", len(tasks))

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
