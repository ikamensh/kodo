"""Upload benchmark results to GCS and regenerate index.json files.

Usage:
    uv run python -m benchmark --upload                    # upload all runs
    uv run python -m benchmark --upload --run-id <id>      # upload specific run
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


BUCKET = "gs://kodo-bench"


def upload_results(workspace: Path, run_id: str | None = None) -> int:
    """Upload benchmark results to GCS. Returns 0 on success."""
    runs_dir = workspace / "runs"
    if not runs_dir.is_dir():
        print("No runs found.")
        return 1

    if run_id:
        run_dirs = [runs_dir / run_id]
        if not run_dirs[0].is_dir():
            print(f"Run {run_id} not found.")
            return 1
    else:
        run_dirs = sorted(d for d in runs_dir.iterdir() if d.is_dir())

    # Build index per dataset
    datasets: dict[str, dict] = {}  # "verified" -> index data

    for rd in run_dirs:
        meta = _load_json(rd / "meta.json")
        if not meta:
            continue

        dataset_key = _dataset_key(meta.get("dataset", ""))
        if not dataset_key:
            continue

        if dataset_key not in datasets:
            datasets[dataset_key] = {"tasks": {}, "arms": set(), "results": {}, "meta": {}}

        idx = datasets[dataset_key]
        arms = meta.get("arms", [])
        idx["arms"].update(arms)

        # Add task stubs
        for iid in meta.get("instance_ids", []):
            if iid not in idx["tasks"]:
                idx["tasks"][iid] = {"instance_id": iid}

        # Load results
        results = _load_jsonl(rd / "results.jsonl")
        for r in results:
            iid = r.get("instance_id", "")
            arm = r.get("arm", "")
            if not iid or not arm:
                continue

            if iid not in idx["results"]:
                idx["results"][iid] = {}

            idx["results"][iid][arm] = {
                "status": r.get("status", ""),
                "elapsed_s": r.get("elapsed_s", 0),
                "patch_len": r.get("patch_len", 0),
                "error": r.get("error", ""),
                "run_id": rd.name,
            }

        # Load eval summary
        eval_summary = _load_json(rd / "eval-summary.json")
        for arm in arms:
            safe_arm = _eval_key(arm)
            e = eval_summary.get(safe_arm, {})
            resolved_set = set(e.get("resolved", []))
            failed_set = set(e.get("failed", []))
            error_set = set(e.get("error", []))

            for iid in resolved_set | failed_set | error_set:
                if iid not in idx["results"]:
                    idx["results"][iid] = {}
                if arm not in idx["results"][iid]:
                    idx["results"][iid][arm] = {}
                idx["results"][iid][arm]["resolved"] = iid in resolved_set
                idx["results"][iid][arm]["eval_status"] = True

        # Upload patches as artifacts
        for pred_file in rd.glob("predictions-*.jsonl"):
            for line in pred_file.read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    pred = json.loads(line)
                    iid = pred["instance_id"]
                    arm = pred.get("model_name_or_path", "")
                    patch = pred.get("model_patch", "")
                    if patch:
                        artifact_key = f"data/artifacts/{iid}/{arm}/patch.diff"
                        _upload_text(artifact_key, patch)
                        # Record patch URL in results
                        if iid in idx["results"] and arm in idx["results"][iid]:
                            idx["results"][iid][arm]["patch_url"] = f"/data/artifacts/{iid}/{arm}/patch.diff"
                        # Also try original arm name
                        orig_arm = arm.replace("_", ":")
                        if iid in idx["results"] and orig_arm in idx["results"][iid]:
                            idx["results"][iid][orig_arm]["patch_url"] = f"/data/artifacts/{iid}/{arm}/patch.diff"
                except (json.JSONDecodeError, KeyError):
                    continue

    # Generate and upload index.json for each dataset
    for ds_key, idx in datasets.items():
        index_data = {
            "tasks": sorted(idx["tasks"].values(), key=lambda t: t["instance_id"]),
            "arms": sorted(idx["arms"]),
            "results": idx["results"],
            "meta": {
                "dataset": ds_key,
                "total_tasks": len(idx["tasks"]),
                "total_evaluated": sum(
                    1 for iid in idx["tasks"]
                    if any(idx["results"].get(iid, {}).get(a, {}).get("eval_status")
                           for a in idx["arms"])
                ),
            },
        }

        index_path = f"data/{ds_key}/index.json"
        content = json.dumps(index_data, indent=2)
        _upload_text(index_path, content)
        print(f"Uploaded {index_path} ({len(idx['tasks'])} tasks, {len(idx['arms'])} arms)")

    return 0


def _dataset_key(dataset: str) -> str:
    low = dataset.lower()
    if "verified" in low:
        return "verified"
    if "pro" in low:
        return "pro"
    if "lite" in low:
        return "lite"
    return ""


def _eval_key(arm: str) -> str:
    import re
    return re.sub(r"[^a-zA-Z0-9_.-]", "_", arm)


def _upload_text(key: str, content: str) -> None:
    """Upload text content to GCS."""
    dest = f"{BUCKET}/{key}"
    proc = subprocess.run(
        ["gcloud", "storage", "cp", "-", dest],
        input=content.encode(),
        capture_output=True,
        timeout=30,
    )
    if proc.returncode != 0:
        print(f"  Warning: failed to upload {key}: {proc.stderr.decode()[:200]}")


def _load_json(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _load_jsonl(path: Path) -> list[dict]:
    results = []
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip():
                try:
                    results.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return results
