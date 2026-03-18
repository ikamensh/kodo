#!/usr/bin/env python3
"""Upload all local benchmark runs to the online store.

Usage:
    uv run python -m benchmark.online.upload_history
    uv run python -m benchmark.online.upload_history --run-id kodo_solo_verified_50
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

from benchmark.online.validation import suspicious_upload_reason

WORKSPACE = Path.home() / ".kodo" / "benchmark"


# Dataset string -> key mapping
def _ds_key(dataset: str) -> str:
    low = dataset.lower()
    if "verified" in low:
        return "verified"
    if "pro" in low:
        return "pro"
    if "lite" in low:
        return "lite"
    return ""


def _post(url: str, token: str, path: str, data: dict) -> bool:
    """POST JSON, return True on success."""
    full = f"{url.rstrip('/')}{path}"
    body = json.dumps(data).encode()
    req = urllib.request.Request(full, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    try:
        resp = urllib.request.urlopen(req, timeout=60)
        return resp.status == 200
    except Exception as e:
        print(f"  ERROR: {e}")
        return False


def upload_run(run_dir: Path, url: str, token: str) -> dict:
    """Upload a single run. Returns {uploaded, skipped, failed} counts."""
    name = run_dir.name
    meta_file = run_dir / "meta.json"
    if not meta_file.exists():
        print(f"  {name}: no meta.json, skipping")
        return {"uploaded": 0, "skipped": 0, "failed": 0}

    meta = json.loads(meta_file.read_text())
    dataset = meta.get("dataset", "")
    ds = _ds_key(dataset)
    if not ds:
        print(f"  {name}: unknown dataset '{dataset}', skipping")
        return {"uploaded": 0, "skipped": 0, "failed": 0}

    arms = meta.get("arms", [])
    print(f"\n{name}: {meta.get('task_count', 0)} tasks, arms={arms}, dataset={ds}")

    # Upload run metadata
    _post(
        url,
        token,
        "/api/run",
        {
            "run_id": name,
            **meta,
        },
    )

    # Load results
    results_file = run_dir / "results.jsonl"
    if not results_file.exists():
        print("  no results.jsonl")
        return {"uploaded": 0, "skipped": 0, "failed": 0}

    results = []
    for line in results_file.read_text().splitlines():
        if line.strip():
            try:
                results.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    # Load patches from prediction files
    patches: dict[tuple[str, str], str] = {}  # (iid, arm) -> patch
    for pred_file in run_dir.glob("predictions-*.jsonl"):
        for line in pred_file.read_text().splitlines():
            if not line.strip():
                continue
            try:
                pred = json.loads(line)
                iid = pred["instance_id"]
                arm = pred.get("arm", pred.get("model_name_or_path", ""))
                patch = pred.get("model_patch", "")
                if iid and arm and patch:
                    patches[(iid, arm)] = patch
            except (json.JSONDecodeError, KeyError):
                continue

    # Load already-uploaded set for delta logic
    from benchmark.online.upload_tracker import load_uploaded, mark_uploaded

    uploaded = load_uploaded(WORKSPACE)

    # Upload each result
    counts = {"uploaded": 0, "skipped": 0, "failed": 0}
    for r in results:
        iid = r.get("instance_id", "")
        arm = r.get("arm", "")
        if not iid or not arm:
            counts["skipped"] += 1
            continue
        if (iid, arm) in uploaded:
            counts["skipped"] += 1
            continue

        patch = patches.get((iid, arm), "")
        reason = suspicious_upload_reason(
            arm=arm,
            status=r.get("status", ""),
            elapsed_s=r.get("elapsed_s", 0),
            patch=patch,
            patch_len=r.get("patch_len"),
            error=r.get("error", ""),
            agent_output=r.get("agent_output"),
        )
        if reason:
            print(f"  skip suspicious {iid} {arm}: {reason}")
            counts["skipped"] += 1
            mark_uploaded(WORKSPACE, iid, arm, name)
            continue

        ok = _post(
            url,
            token,
            "/api/task-result",
            {
                "dataset": ds,
                "run_id": name,
                "instance_id": iid,
                "arm": arm,
                "status": r.get("status", ""),
                "elapsed_s": r.get("elapsed_s", 0),
                "patch_len": r.get("patch_len", 0),
                "error": r.get("error", ""),
                "patch": patch,
                "agent_output": r.get("agent_output"),
                "provenance": {"source": "historical_upload", "run_id": name},
            },
        )
        if ok:
            counts["uploaded"] += 1
            mark_uploaded(WORKSPACE, iid, arm, name)
        else:
            counts["failed"] += 1

    # Upload eval results if available
    eval_file = run_dir / "eval-summary.json"
    if eval_file.exists():
        eval_data = json.loads(eval_file.read_text())
        for arm_key, arm_eval in eval_data.items():
            # arm_key might be docker_safe'd, try to find original arm
            orig_arm = arm_key
            for a in arms:
                import re

                if re.sub(r"[^a-zA-Z0-9_.-]", "_", a) == arm_key:
                    orig_arm = a
                    break
            _post(
                url,
                token,
                "/api/eval-results",
                {
                    "dataset": ds,
                    "arm": orig_arm,
                    "resolved": arm_eval.get("resolved", []),
                    "failed": arm_eval.get("failed", []),
                    "error": arm_eval.get("error", []),
                },
            )
        print(f"  eval results uploaded for {len(eval_data)} arm(s)")

    print(
        f"  {counts['uploaded']} uploaded, {counts['skipped']} skipped, {counts['failed']} failed"
    )
    return counts


def main():
    import os

    parser = argparse.ArgumentParser(description="Upload historical benchmark runs")
    parser.add_argument("--run-id", help="Upload a specific run only")
    parser.add_argument(
        "--url",
        default=os.environ.get(
            "KODO_BENCH_URL", "https://kodo-bench-430011644943.europe-west1.run.app"
        ),
    )
    parser.add_argument("--token", default=os.environ.get("KODO_BENCH_TOKEN", ""))
    args = parser.parse_args()

    if not args.token:
        print("Set KODO_BENCH_TOKEN or pass --token")
        return 1

    runs_dir = WORKSPACE / "runs"
    if not runs_dir.is_dir():
        print(f"No runs at {runs_dir}")
        return 1

    if args.run_id:
        run_dirs = [runs_dir / args.run_id]
        if not run_dirs[0].is_dir():
            print(f"Run {args.run_id} not found")
            return 1
    else:
        run_dirs = sorted(d for d in runs_dir.iterdir() if d.is_dir())

    print(f"Uploading {len(run_dirs)} run(s) to {args.url}")
    totals = {"uploaded": 0, "skipped": 0, "failed": 0}

    for rd in run_dirs:
        counts = upload_run(rd, args.url, args.token)
        for k in totals:
            totals[k] += counts[k]

    print(
        f"\nDone: {totals['uploaded']} uploaded, {totals['skipped']} skipped, {totals['failed']} failed"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
