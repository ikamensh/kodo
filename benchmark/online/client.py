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

from .config import collect_provenance, dataset_key, get_client_credentials
from .validation import suspicious_upload_reason

log = logging.getLogger("benchmark.online")

_provenance: dict | None = None  # cached per process


def is_configured() -> bool:
    url, token = get_client_credentials()
    return bool(url and token)


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
    agent_output: dict | list | str | None = None,
) -> None:
    """Upload a single task result + patch to the online store."""
    ds = dataset_key(dataset)
    if not ds:
        return
    reason = suspicious_upload_reason(
        arm=arm,
        status=status,
        elapsed_s=elapsed_s,
        patch=patch,
        error=error,
        agent_output=agent_output,
    )
    if reason:
        log.info(
            "Skipping suspicious benchmark upload for %s/%s: %s",
            instance_id,
            arm,
            reason,
        )
        return
    _post(
        "/api/task-result",
        {
            "dataset": ds,
            "run_id": run_id,
            "instance_id": instance_id,
            "arm": arm,
            "status": status,
            "elapsed_s": round(elapsed_s, 1),
            "patch_len": len(patch),
            "error": error,
            "patch": patch,
            "agent_output": agent_output,
            "provenance": _get_provenance(),
        },
    )


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
    _post(
        "/api/run",
        {
            "run_id": run_id,
            "kodo_version": kodo_version,
            "task_count": task_count,
            "arms": arms or [],
            "timeout": timeout,
            "dataset": dataset_key(dataset) or dataset,
            "instance_ids": instance_ids or [],
            "provenance": _get_provenance(),
        },
    )


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
    _post(
        "/api/eval-results",
        {
            "dataset": ds,
            "arm": arm,
            "resolved": resolved or [],
            "failed": failed or [],
            "error": error or [],
        },
    )


# ── Internals ────────────────────────────────────────────────────────


def _get_provenance() -> dict:
    global _provenance
    if _provenance is None:
        _provenance = collect_provenance()
    # Always use fresh timestamp (the rest is cached for performance — ipinfo etc.)
    from datetime import datetime, timezone

    return {**_provenance, "timestamp": datetime.now(timezone.utc).isoformat()}


def _request(
    method: str, path: str, data: dict | None = None, timeout: int = 30
) -> bytes:
    """Send an authenticated request to the benchmark server. Returns response body."""
    base_url, token = get_client_credentials()
    if not base_url or not token:
        raise RuntimeError("Benchmark online client is not configured")
    url = f"{base_url.rstrip('/')}{path}"
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    return urllib.request.urlopen(req, timeout=timeout).read()


def _post(path: str, data: dict) -> None:
    """POST JSON to the benchmark server. Raises on HTTP errors."""
    _request("POST", path, data)


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
    agent_output: dict | list | str | None = None,
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
            agent_output=agent_output,
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


# ── Task distribution ────────────────────────────────────────────────


def fetch_assignments(
    backends: list[str],
    *,
    datasets: dict[str, list[str]] | None = None,
    limit: int = 20,
    contributor: str = "",
) -> list[dict] | None:
    """Fetch task assignments from the central server.

    Args:
        backends: Available backends (e.g. ["claude", "kodo:solo"]).
        datasets: {dataset_key: [instance_ids]} — server picks across all.
        limit: Max assignments to return.
        contributor: Who is requesting (auto-detected if empty).

    Returns list of {"instance_id": ..., "arm": ..., "dataset": ...} dicts,
    or None if the server is unreachable or unconfigured.
    """
    if not is_configured():
        return None

    if not contributor:
        prov = _get_provenance()
        contributor = f"{prov.get('user', 'unknown')}@{prov.get('host', 'unknown')}"

    result = _post_json(
        "/api/next-tasks",
        {
            "datasets": datasets or {},
            "backends": backends,
            "limit": limit,
            "contributor": contributor,
        },
    )
    return result.get("assignments", [])


def fetch_unevaluated(dataset: str) -> list[dict] | None:
    """Fetch predictions that need evaluation from the central server.

    Returns list of {"instance_id": ..., "arm": ..., "patch": ...} dicts,
    or None if the server is unreachable or unconfigured.
    """
    if not is_configured():
        return None
    ds = dataset_key(dataset) or dataset
    try:
        result = _get_json(f"/api/unevaluated/{ds}", timeout=300)
        return result.get("predictions", [])
    except Exception as exc:
        log.warning("Failed to fetch unevaluated predictions: %s", exc)
        return None


def whoami() -> str | None:
    """Return the display name for the current token, or None."""
    if not is_configured():
        return None
    try:
        result = _get_json("/api/whoami")
        return result.get("name") or result.get("issued_to") or None
    except Exception:
        return None


def _post_json(path: str, data: dict) -> dict:
    """POST JSON and return parsed response."""
    return json.loads(_request("POST", path, data))


def _get_json(path: str, *, timeout: int = 60) -> dict:
    """GET and return parsed response."""
    return json.loads(_request("GET", path, timeout=timeout))
