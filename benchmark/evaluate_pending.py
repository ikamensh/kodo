"""Fetch unevaluated predictions from the online server and evaluate locally.

Flow:
    1. GET /api/unevaluated/{dataset} → list of (instance_id, arm, patch)
    2. Write predictions files per arm
    3. Run combined swebench evaluation (all arms in one invocation for Docker reuse)
    4. Upload eval results as instances complete
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from benchmark._util import docker_safe, log
from benchmark.tasks import DATASET_MAP


def evaluate_pending(workspace: Path, *, dataset_arg: str = "pro", arms: list[str] | None = None) -> int:
    """Fetch unevaluated predictions from server, evaluate locally, upload results.

    Args:
        arms: If provided, only evaluate these arms (e.g. ["kodo:solo"]).
              If None, evaluate all unevaluated predictions.

    Uses combined evaluation to merge all arms into a single swebench
    invocation, maximizing Docker image reuse across arms.
    Returns 0 on success, 1 on error.
    """
    from benchmark.evaluate import evaluate_arms_combined
    from benchmark.online.client import (
        fetch_unevaluated,
        is_configured,
        upload_eval_results,
    )

    if not is_configured():
        log.error("KODO_BENCH_URL and KODO_BENCH_TOKEN must be set")
        return 1

    from benchmark._util import ensure_docker_running

    if not ensure_docker_running():
        log.error("Docker is required for evaluation but could not be started")
        return 1

    full_dataset = DATASET_MAP.get(dataset_arg, dataset_arg)

    log.info("Fetching unevaluated predictions for '%s'...", dataset_arg)
    predictions = fetch_unevaluated(full_dataset)

    if predictions is None:
        log.error("Failed to fetch unevaluated predictions from server")
        return 1

    if not predictions:
        log.info("No unevaluated predictions found. Nothing to do.")
        return 0

    # Group by arm
    by_arm: dict[str, list[dict]] = {}
    for pred in predictions:
        by_arm.setdefault(pred["arm"], []).append(pred)

    # Filter to requested arms if specified
    if arms:
        arm_set = set(arms)
        skipped = set(by_arm) - arm_set
        if skipped:
            log.info("Skipping arms not in --arm filter: %s", ", ".join(sorted(skipped)))
        by_arm = {a: preds for a, preds in by_arm.items() if a in arm_set}
        if not by_arm:
            log.info("No unevaluated predictions match --arm filter %s", arms)
            return 0

    total_predictions = sum(len(v) for v in by_arm.values())
    log.info(
        "Found %d unevaluated predictions across %d arm(s): %s",
        total_predictions,
        len(by_arm),
        ", ".join(sorted(by_arm)),
    )

    # Create a synthetic run directory
    run_id = "eval_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = workspace / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Write meta.json
    meta = {
        "dataset": full_dataset,
        "arms": list(by_arm),
        "task_count": total_predictions,
        "source": "evaluate-pending",
    }
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2))

    # Write per-arm prediction files
    for arm, preds in by_arm.items():
        safe_arm = docker_safe(arm)
        pred_file = run_dir / f"predictions-{safe_arm}.jsonl"
        with open(pred_file, "w") as f:
            for p in preds:
                entry = {
                    "instance_id": p["instance_id"],
                    "model_name_or_path": safe_arm,
                    "arm": arm,
                    "model_patch": p["patch"],
                }
                f.write(json.dumps(entry) + "\n")
        log.info("Wrote %d predictions to %s", len(preds), pred_file.name)

    total_resolved = 0
    total_evaluated = 0
    upload_errors = 0

    def _on_instance(instance_id: str, arm: str, resolved: bool) -> None:
        nonlocal total_resolved, total_evaluated, upload_errors
        total_evaluated += 1
        if resolved:
            total_resolved += 1
        try:
            upload_eval_results(
                full_dataset,
                arm,
                resolved=[instance_id] if resolved else [],
                failed=[] if resolved else [instance_id],
                error=[],
            )
            status = "resolved" if resolved else "failed"
            log.info("Uploaded %s/%s: %s (%d/%d total)",
                     arm, instance_id, status, total_resolved, total_evaluated)
        except Exception as exc:
            upload_errors += 1
            log.debug("Upload failed for %s/%s: %s", arm, instance_id, exc)

    # Run combined evaluation (all arms in one swebench invocation)
    arm_results = evaluate_arms_combined(
        run_dir, list(by_arm), run_id, full_dataset, on_instance=_on_instance,
    )

    for arm, results in arm_results.items():
        resolved = results.get("resolved", [])
        failed = results.get("failed", [])
        error = results.get("error", [])
        log.info(
            "Arm '%s': %d resolved, %d failed, %d error",
            arm, len(resolved), len(failed), len(error),
        )
        # Bulk upload results that weren't streamed via on_instance
        if resolved or failed or error:
            try:
                upload_eval_results(
                    full_dataset, arm,
                    resolved=resolved, failed=failed, error=error,
                )
                log.info("Bulk-uploaded eval results for %s", arm)
            except Exception as exc:
                upload_errors += 1
                log.warning("Bulk upload failed for %s: %s", arm, exc)

    log.info("Evaluation complete: %d/%d resolved", total_resolved, total_evaluated)
    return 0 if upload_errors == 0 else 1


def main() -> int:
    """CLI entrypoint for standalone evaluate-pending runs."""
    import argparse
    import warnings

    warnings.filterwarnings("ignore", message=r"urllib3.*doesn't match a supported version")
    from dotenv import find_dotenv, load_dotenv
    load_dotenv(find_dotenv(usecwd=True))

    from benchmark._util import setup_logging

    parser = argparse.ArgumentParser(
        description="Fetch unevaluated predictions from the online server, "
        "run Docker-based swebench evaluation locally, and upload results.",
    )
    parser.add_argument(
        "--dataset",
        choices=["pro", "verified", "lite"],
        default="pro",
        help="SWE-bench variant (default: pro)",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path.home() / ".kodo" / "benchmark",
        help="Workspace directory (default: ~/.kodo/benchmark)",
    )
    parser.add_argument(
        "--arm",
        action="append",
        default=None,
        help="Only evaluate this arm. Repeatable. Default: all unevaluated arms.",
    )
    args = parser.parse_args()

    setup_logging()
    args.workspace.mkdir(parents=True, exist_ok=True)
    return evaluate_pending(args.workspace, dataset_arg=args.dataset, arms=args.arm)


if __name__ == "__main__":
    import sys
    sys.exit(main())
