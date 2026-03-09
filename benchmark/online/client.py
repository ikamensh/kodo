"""HTTP client for uploading benchmark results to the online server.

Best-effort: all functions silently no-op if KODO_BENCH_URL / KODO_BENCH_TOKEN
are not set, and silently swallow errors to never crash the benchmark runner.

Configuration:
    KODO_BENCH_URL=https://bench-xyz.run.app
    KODO_BENCH_TOKEN=your-api-key
"""

from __future__ import annotations

import json
import logging
import urllib.request

from .config import BENCH_TOKEN, BENCH_URL, collect_provenance, dataset_key

log = logging.getLogger("benchmark.online")

_provenance: dict | None = None  # cached per process


def is_configured() -> bool:
    return bool(BENCH_URL and BENCH_TOKEN)


def upload_task_result(
    *,
    instance_id: str,
    arm: str,
    status: str,
    elapsed_s: float,
    patch: str,
    error: str,
    run_id: str,
    dataset: str,
) -> None:
    """Upload a single task result + patch to the online store."""
    ds = dataset_key(dataset)
    if not ds:
        return
    _post("/api/task-result", {
        "dataset": ds,
        "run_id": run_id,
        "instance_id": instance_id,
        "arm": arm,
        "status": status,
        "elapsed_s": round(elapsed_s, 1),
        "patch_len": len(patch),
        "error": error,
        "patch": patch,
        "provenance": _get_provenance(),
    })


def upload_run(
    run_id: str,
    *,
    kodo_version: str = "",
    task_count: int = 0,
    arms: list[str] | None = None,
    timeout: int = 0,
    dataset: str = "",
    instance_ids: list[str] | None = None,
) -> None:
    """Register a benchmark run."""
    _post("/api/run", {
        "run_id": run_id,
        "kodo_version": kodo_version,
        "task_count": task_count,
        "arms": arms or [],
        "timeout": timeout,
        "dataset": dataset_key(dataset) or dataset,
        "instance_ids": instance_ids or [],
        "provenance": _get_provenance(),
    })


def upload_eval_results(
    dataset: str,
    arm: str,
    *,
    resolved: list[str] | None = None,
    failed: list[str] | None = None,
    error: list[str] | None = None,
) -> None:
    """Upload evaluation results for an arm."""
    ds = dataset_key(dataset)
    if not ds:
        return
    _post("/api/eval-results", {
        "dataset": ds,
        "arm": arm,
        "resolved": resolved or [],
        "failed": failed or [],
        "error": error or [],
    })


# ── Internals ────────────────────────────────────────────────────────


def _get_provenance() -> dict:
    global _provenance
    if _provenance is None:
        _provenance = collect_provenance()
    return _provenance


def _post(path: str, data: dict) -> None:
    """POST JSON to the benchmark server. Raises on HTTP errors."""
    url = f"{BENCH_URL.rstrip('/')}{path}"
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {BENCH_TOKEN}")
    req.add_header("Content-Type", "application/json")
    urllib.request.urlopen(req, timeout=30)


# ── Safe wrappers for runner integration ─────────────────────────────


def maybe_upload_task_result(
    *,
    instance_id: str,
    arm: str,
    status: str,
    elapsed_s: float,
    patch: str,
    error: str,
    run_id: str,
    dataset: str,
) -> bool:
    """Best-effort upload. No-op if unconfigured, swallows all errors.

    Returns True if the upload succeeded, False otherwise.
    """
    if not is_configured():
        return False
    try:
        upload_task_result(
            instance_id=instance_id,
            arm=arm,
            status=status,
            elapsed_s=elapsed_s,
            patch=patch,
            error=error,
            run_id=run_id,
            dataset=dataset,
        )
        return True
    except Exception as exc:
        log.debug("Online upload failed for %s/%s: %s", instance_id, arm, exc)
        return False


def maybe_upload_run(run_id: str, **kwargs) -> None:
    """Best-effort run registration. No-op if unconfigured."""
    if not is_configured():
        return
    try:
        upload_run(run_id, **kwargs)
    except Exception as exc:
        log.debug("Online run registration failed: %s", exc)
