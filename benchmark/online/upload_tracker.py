"""Track which task results have been uploaded to the online server.

Uses an append-only JSONL file at ``workspace/uploaded.jsonl``.
The runner appends on each successful upload; ``flush_pending_uploads``
diffs local results against this file to find what still needs uploading.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from benchmark._util import load_json, load_jsonl, log

UPLOADED_FILE = "uploaded.jsonl"


def mark_uploaded(workspace: Path, instance_id: str, arm: str, run_id: str) -> None:
    """Record a successful upload."""
    entry = {"instance_id": instance_id, "arm": arm, "run_id": run_id}
    path = workspace / UPLOADED_FILE
    with open(path, "a") as f:
        f.write(json.dumps(entry, separators=(",", ":")) + "\n")
        f.flush()
        os.fsync(f.fileno())


def load_uploaded(workspace: Path) -> set[tuple[str, str]]:
    """Load set of (instance_id, arm) pairs that have been uploaded."""
    uploaded: set[tuple[str, str]] = set()
    path = workspace / UPLOADED_FILE
    if not path.exists():
        return uploaded
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            uploaded.add((entry["instance_id"], entry["arm"]))
        except (json.JSONDecodeError, KeyError):
            continue
    return uploaded


def flush_pending_uploads(workspace: Path) -> int:
    """Upload all results not yet uploaded. Returns 0 on success, 1 on errors."""
    from benchmark.online.client import is_configured, upload_task_result

    if not is_configured():
        log.error("Cannot upload: KODO_BENCH_URL and KODO_BENCH_TOKEN must be set")
        return 1

    uploaded = load_uploaded(workspace)
    runs_dir = workspace / "runs"
    if not runs_dir.is_dir():
        log.info("No runs found.")
        return 0

    pending = 0
    success = 0
    failed = 0

    for run_dir in sorted(runs_dir.iterdir()):
        if not run_dir.is_dir():
            continue
        meta = load_json(run_dir / "meta.json")
        dataset = meta.get("dataset", "")
        results = load_jsonl(run_dir / "results.jsonl")

        # Load patches keyed by (instance_id, arm)
        patches: dict[tuple[str, str], str] = {}
        for pred_file in run_dir.glob("predictions-*.jsonl"):
            for entry in load_jsonl(pred_file):
                iid = entry.get("instance_id", "")
                arm = entry.get("arm", entry.get("model_name_or_path", ""))
                if iid and arm:
                    patches[(iid, arm)] = entry.get("model_patch", "")

        for r in results:
            iid = r.get("instance_id", "")
            arm = r.get("arm", "")
            if not iid or not arm:
                continue
            if (iid, arm) in uploaded:
                continue

            pending += 1
            patch = patches.get((iid, arm), "")
            try:
                upload_task_result(
                    instance_id=iid,
                    arm=arm,
                    status=r.get("status", ""),
                    elapsed_s=r.get("elapsed_s", 0),
                    patch=patch,
                    error=r.get("error", ""),
                    run_id=run_dir.name,
                    dataset=dataset,
                    agent_output=r.get("agent_output"),
                )
                mark_uploaded(workspace, iid, arm, run_dir.name)
                success += 1
                uploaded.add((iid, arm))
            except Exception as exc:
                log.warning("Upload failed for %s/%s: %s", iid, arm, exc)
                failed += 1

    log.info("Upload: %d pending, %d uploaded, %d failed", pending, success, failed)
    return 0 if failed == 0 else 1
