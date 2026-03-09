"""Fetch unevaluated predictions from the online server and evaluate locally.

Flow:
    1. GET /api/unevaluated/{dataset} → list of (instance_id, arm, patch)
    2. For each arm:
       a. Write predictions-{arm}.jsonl
       b. Run Docker-based swebench evaluation
       c. Upload eval results immediately
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from benchmark._util import docker_safe, log
from benchmark.tasks import DATASET_MAP


def evaluate_pending(workspace: Path, *, dataset_arg: str = "pro") -> int:
    """Fetch unevaluated predictions from server, evaluate locally, upload results.

    Evaluates and uploads one arm at a time so results appear immediately.
    Returns 0 on success, 1 on error.
    """
    from benchmark.evaluate import evaluate_arm
    from benchmark.online.client import (
        fetch_unevaluated,
        is_configured,
        upload_eval_results,
    )

    if not is_configured():
        log.error("KODO_BENCH_URL and KODO_BENCH_TOKEN must be set")
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

    log.info(
        "Found %d unevaluated predictions across %d arm(s): %s",
        len(predictions),
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
        "task_count": len(predictions),
        "source": "evaluate-pending",
    }
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2))

    total_resolved = 0
    total_evaluated = 0
    upload_errors = 0

    # Process largest arm first — Docker images get cached and reused by later arms.
    # (swebench deduplicates by instance_id internally, so we can't combine arms
    # into one invocation when instances overlap across arms.)
    for arm, preds in sorted(by_arm.items(), key=lambda kv: -len(kv[1])):
        safe_arm = docker_safe(arm)

        # Write predictions file for this arm
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

        # Upload each instance result as it completes
        _arm = arm  # capture by value for closure safety
        def _on_instance(instance_id: str, resolved: bool) -> None:
            nonlocal total_resolved, total_evaluated
            total_evaluated += 1
            if resolved:
                total_resolved += 1
            try:
                upload_eval_results(
                    full_dataset,
                    _arm,
                    resolved=[instance_id] if resolved else [],
                    failed=[] if resolved else [instance_id],
                    error=[],
                )
                status = "resolved" if resolved else "failed"
                log.info("Uploaded %s/%s: %s (%d/%d total)",
                         _arm, instance_id, status, total_resolved, total_evaluated)
            except Exception as exc:
                nonlocal upload_errors
                upload_errors += 1
                log.debug("Upload failed for %s/%s: %s", _arm, instance_id, exc)

        # Evaluate this arm with per-instance streaming
        results = evaluate_arm(run_dir, arm, run_id, full_dataset, on_instance=_on_instance)

        resolved = results.get("resolved", [])
        failed = results.get("failed", [])
        error = results.get("error", [])

        log.info(
            "Arm '%s' done: %d resolved, %d failed, %d error",
            arm, len(resolved), len(failed), len(error),
        )

    log.info("Evaluation complete: %d/%d resolved", total_resolved, total_evaluated)
    return 0 if upload_errors == 0 else 1
