"""Firestore + GCS operations for benchmark data.

Firestore collections:
    runs/{run_id}                              — run metadata
    datasets/{dataset}/results/{instance_id}   — task results (arms as nested map)
    datasets/{dataset}                         — dirty flag + last_materialized
    tokens/{token_hash}                        — API token registry

GCS layout:
    gs://{bucket}/patches/{dataset}/{instance_id}/{arm}.diff
    gs://{bucket}/data/{dataset}/index.json    — materialized index (rebuilt on demand)
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import datetime, timezone

STALE_SECONDS = 3600  # rebuild materialized index if older than 1 hour

from .config import GCP_PROJECT, GCS_BUCKET

log = logging.getLogger("benchmark.online")

# Lazy-initialized clients (Cloud Run handles credentials automatically)
_db_client = None
_gcs_client = None
_gcs_bucket_obj = None


def _db():
    """Get or create Firestore client."""
    global _db_client
    if _db_client is None:
        from google.cloud import firestore

        _db_client = firestore.Client(project=GCP_PROJECT)
    return _db_client


def _bucket():
    """Get or create GCS bucket handle."""
    global _gcs_client, _gcs_bucket_obj
    if _gcs_bucket_obj is None:
        from google.cloud import storage

        _gcs_client = storage.Client(project=GCP_PROJECT)
        _gcs_bucket_obj = _gcs_client.bucket(GCS_BUCKET)
    return _gcs_bucket_obj


# ── Write operations ─────────────────────────────────────────────────────


def save_task_result(
    dataset: str, instance_id: str, arm: str, data: dict,
) -> None:
    """Upsert a single task result. Merges into the arms map."""
    from google.cloud import firestore

    doc_ref = (
        _db()
        .collection("datasets")
        .document(dataset)
        .collection("results")
        .document(instance_id)
    )
    arm_data = {**data, "updated_at": firestore.SERVER_TIMESTAMP}
    doc_ref.set({"arms": {arm: arm_data}}, merge=True)
    _mark_dirty(dataset)


def iter_task_results(dataset: str) -> list[dict]:
    """Return flattened task-result rows for a dataset."""
    coll = _db().collection("datasets").document(dataset).collection("results")
    rows: list[dict] = []
    for doc in coll.stream():
        data = doc.to_dict() or {}
        for arm, arm_data in (data.get("arms") or {}).items():
            rows.append({
                "dataset": dataset,
                "instance_id": doc.id,
                "arm": arm,
                **arm_data,
            })
    return rows


def delete_task_result(
    dataset: str,
    instance_id: str,
    arm: str,
    *,
    delete_patch_blob: bool = True,
) -> None:
    """Delete a single task result and, optionally, its stored patch."""
    from google.cloud import firestore

    doc_ref = (
        _db()
        .collection("datasets")
        .document(dataset)
        .collection("results")
        .document(instance_id)
    )
    doc_ref.update({f"arms.{arm}": firestore.DELETE_FIELD})
    snapshot = doc_ref.get()
    data = snapshot.to_dict() or {} if snapshot.exists else {}
    if not (data.get("arms") or {}):
        doc_ref.delete()
    if delete_patch_blob:
        delete_patch(dataset, instance_id, arm)
    _mark_dirty(dataset)


def delete_task_results_batch(dataset: str, rows: list[tuple[str, str]]) -> None:
    """Delete many task-result arms in Firestore batches."""
    from google.cloud import firestore

    coll = _db().collection("datasets").document(dataset).collection("results")
    for i in range(0, len(rows), 500):
        batch = _db().batch()
        for instance_id, arm in rows[i : i + 500]:
            doc_ref = coll.document(instance_id)
            batch.update(doc_ref, {f"arms.{arm}": firestore.DELETE_FIELD})
        batch.commit()
    _mark_dirty(dataset)


def delete_empty_result_docs(dataset: str) -> int:
    """Delete result docs that no longer contain any arms."""
    coll = _db().collection("datasets").document(dataset).collection("results")
    deleted = 0
    for doc in coll.stream():
        data = doc.to_dict() or {}
        if data.get("arms"):
            continue
        doc.reference.delete()
        deleted += 1
    if deleted:
        _mark_dirty(dataset)
    return deleted


def clear_eval_status_batch(dataset: str, rows: list[tuple[str, str]]) -> None:
    """Remove eval_status and resolved fields so instances can be re-evaluated."""
    from google.cloud import firestore

    coll = _db().collection("datasets").document(dataset).collection("results")
    for i in range(0, len(rows), 500):
        batch = _db().batch()
        for instance_id, arm in rows[i : i + 500]:
            doc_ref = coll.document(instance_id)
            batch.update(doc_ref, {
                f"arms.{arm}.eval_status": firestore.DELETE_FIELD,
                f"arms.{arm}.resolved": firestore.DELETE_FIELD,
            })
        batch.commit()
    if rows:
        _mark_dirty(dataset)


def save_patch(dataset: str, instance_id: str, arm: str, patch: str) -> None:
    """Upload patch text to GCS."""
    if not patch:
        return
    blob = _bucket().blob(f"patches/{dataset}/{instance_id}/{arm}.diff")
    blob.upload_from_string(patch, content_type="text/plain")


def delete_patch(dataset: str, instance_id: str, arm: str) -> None:
    """Delete a stored patch if it exists."""
    try:
        blob = _bucket().blob(f"patches/{dataset}/{instance_id}/{arm}.diff")
    except ImportError:
        log.warning(
            "Skipping patch deletion for %s/%s in %s: google-cloud-storage is not installed",
            instance_id,
            arm,
            dataset,
        )
        return
    if blob.exists():
        blob.delete()


def save_run(run_id: str, meta: dict) -> None:
    """Save run metadata to Firestore."""
    from google.cloud import firestore

    _db().collection("runs").document(run_id).set(
        {**meta, "created_at": firestore.SERVER_TIMESTAMP},
    )


def save_eval_results(
    dataset: str,
    arm: str,
    resolved: list[str],
    failed: list[str],
    error: list[str],
) -> None:
    """Batch-update eval results for an arm across many instance_ids."""
    from google.cloud import firestore

    coll = _db().collection("datasets").document(dataset).collection("results")

    # Firestore batches max 500 operations
    updates: list[tuple[str, dict]] = []
    for iid in resolved:
        updates.append((iid, {"resolved": True, "eval_status": True}))
    for iid in failed:
        updates.append((iid, {"resolved": False, "eval_status": True}))
    # Don't mark eval errors as evaluated — leave them unevaluated so they
    # can be retried on the next --evaluate-pending run.
    if error:
        log.info("Skipping %d eval errors for %s (will be retried)", len(error), arm)

    for i in range(0, len(updates), 500):
        batch = _db().batch()
        for iid, eval_data in updates[i : i + 500]:
            doc_ref = coll.document(iid)
            batch.set(
                doc_ref,
                {"arms": {arm: {**eval_data, "updated_at": firestore.SERVER_TIMESTAMP}}},
                merge=True,
            )
        batch.commit()
    _mark_dirty(dataset)


# ── Dirty flag + materialization ─────────────────────────────────────────


def _mark_dirty(dataset: str) -> None:
    """Flag a dataset as needing index.json rebuild."""
    from google.cloud import firestore

    _db().collection("datasets").document(dataset).set(
        {"dirty": True, "dirty_since": firestore.SERVER_TIMESTAMP},
        merge=True,
    )


def get_index_json(dataset: str) -> bytes:
    """Return materialized index.json, rebuilding if dirty or stale.

    Cheap path (common): read a single GCS blob.
    Expensive path (rare): query Firestore, rebuild, write GCS.
    """
    blob = _bucket().blob(f"data/{dataset}/index.json")

    # Check if we need to rebuild
    needs_rebuild = False
    if not blob.exists():
        needs_rebuild = True
    else:
        # Check dirty flag (1 Firestore read — small doc)
        meta_doc = _db().collection("datasets").document(dataset).get()
        meta = meta_doc.to_dict() or {} if meta_doc.exists else {}
        if meta.get("dirty"):
            needs_rebuild = True
        else:
            # Check staleness from blob metadata
            blob.reload()
            if blob.updated:
                age = (datetime.now(timezone.utc) - blob.updated.replace(tzinfo=timezone.utc)).total_seconds()
                if age > STALE_SECONDS:
                    needs_rebuild = True

    if needs_rebuild:
        _materialize_index(dataset, blob)

    return blob.download_as_bytes()


def _materialize_index(dataset: str, blob=None) -> None:
    """Rebuild index.json from Firestore and write to GCS."""
    from google.cloud import firestore as fs

    log.info("Materializing index.json for %s", dataset)
    data = _build_index_from_firestore(dataset)
    body = json.dumps(data).encode()

    if blob is None:
        blob = _bucket().blob(f"data/{dataset}/index.json")
    blob.upload_from_string(body, content_type="application/json")

    # Clear dirty flag
    _db().collection("datasets").document(dataset).set(
        {"dirty": False, "last_materialized": fs.SERVER_TIMESTAMP},
        merge=True,
    )


def _build_index_from_firestore(dataset: str) -> dict:
    """Query all results from Firestore and build the index structure."""
    coll = _db().collection("datasets").document(dataset).collection("results")

    tasks = []
    results = {}
    all_arms: set[str] = set()

    for doc in coll.stream():
        iid = doc.id
        data = doc.to_dict() or {}
        tasks.append({"instance_id": iid})
        arms = data.get("arms", {})
        results[iid] = {}
        for arm_name, arm_data in arms.items():
            all_arms.add(arm_name)
            clean = {}
            for k, v in arm_data.items():
                if k == "updated_at":
                    continue
                if hasattr(v, "isoformat"):
                    clean[k] = v.isoformat()
                else:
                    clean[k] = v
            results[iid][arm_name] = clean

    total_evaluated = sum(
        1
        for iid_results in results.values()
        if any(r.get("eval_status") for r in iid_results.values())
    )

    return {
        "tasks": sorted(tasks, key=lambda t: t["instance_id"]),
        "arms": sorted(all_arms),
        "results": results,
        "meta": {
            "dataset": dataset,
            "total_tasks": len(tasks),
            "total_evaluated": total_evaluated,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        },
    }


# ── Read operations ──────────────────────────────────────────────────────


def get_dataset_index(dataset: str) -> dict:
    """Build the index.json structure the viewer expects from Firestore."""
    coll = _db().collection("datasets").document(dataset).collection("results")
    docs = coll.stream()

    tasks = []
    results = {}
    all_arms: set[str] = set()

    for doc in docs:
        iid = doc.id
        data = doc.to_dict() or {}
        tasks.append({"instance_id": iid})
        arms = data.get("arms", {})
        results[iid] = {}
        for arm_name, arm_data in arms.items():
            all_arms.add(arm_name)
            # Convert Firestore timestamps to strings for JSON
            clean = {}
            for k, v in arm_data.items():
                if k == "updated_at":
                    continue  # drop internal timestamp
                if hasattr(v, "isoformat"):
                    clean[k] = v.isoformat()
                else:
                    clean[k] = v
            results[iid][arm_name] = clean

    total_evaluated = sum(
        1
        for iid_results in results.values()
        if any(r.get("eval_status") for r in iid_results.values())
    )

    return {
        "tasks": sorted(tasks, key=lambda t: t["instance_id"]),
        "arms": sorted(all_arms),
        "results": results,
        "meta": {
            "dataset": dataset,
            "total_tasks": len(tasks),
            "total_evaluated": total_evaluated,
            "last_updated": datetime.now(timezone.utc).isoformat(),
        },
    }


def get_patch(dataset: str, instance_id: str, arm: str) -> str | None:
    """Read a single patch from GCS."""
    blob = _bucket().blob(f"patches/{dataset}/{instance_id}/{arm}.diff")
    if blob.exists():
        return blob.download_as_text()
    return None


def get_all_patches(dataset: str) -> dict[str, str]:
    """Read all patches for a dataset. Used for patches.json backward compat.

    Returns dict like {"instance_id/arm": "diff text", ...}.
    """
    patches: dict[str, str] = {}
    prefix = f"patches/{dataset}/"
    for blob in _bucket().list_blobs(prefix=prefix):
        # blob.name = "patches/verified/instance_id/arm.diff"
        relative = blob.name[len(prefix) :]
        parts = relative.rsplit("/", 1)
        if len(parts) == 2:
            iid, arm_file = parts
            arm = arm_file.removesuffix(".diff")
            try:
                patches[f"{iid}/{arm}"] = blob.download_as_text()
            except Exception:
                log.warning("Failed to download patch %s", blob.name)
    return patches


def get_unevaluated(dataset: str) -> list[dict]:
    """Find task results that have not been evaluated yet.

    Scans all result docs for the dataset and returns arms where
    ``status`` is present (task ran) but ``eval_status`` is absent.
    Fetches the corresponding patch from GCS for each entry.

    Only returns entries with status ``"ok"`` or ``"partial"`` and a
    non-empty patch — errors/timeouts without patches are skipped.
    """
    coll = _db().collection("datasets").document(dataset).collection("results")

    pending: list[tuple[str, str]] = []  # (instance_id, arm)
    for doc in coll.stream():
        iid = doc.id
        data = doc.to_dict() or {}
        for arm_name, arm_data in data.get("arms", {}).items():
            if not arm_data.get("status"):
                continue
            if arm_data.get("eval_status"):
                continue  # already evaluated
            if arm_data["status"] not in ("ok", "partial"):
                continue  # no useful patch
            pending.append((iid, arm_name))

    # Fetch patches from GCS
    results: list[dict] = []
    for iid, arm in pending:
        patch = get_patch(dataset, iid, arm)
        if not patch:
            continue
        results.append({"instance_id": iid, "arm": arm, "patch": patch})

    log.info("Found %d unevaluated predictions for dataset %s", len(results), dataset)
    return results


# ── Token management ─────────────────────────────────────────────────────
#
# Tokens stored in Firestore: tokens/{sha256_of_token}
# The actual token is only shown once at creation time.
# We store the hash so a Firestore data leak doesn't expose raw tokens.


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_token(*, name: str, issued_to: str, notes: str = "") -> str:
    """Create a new API token. Returns the raw token (shown once)."""
    from google.cloud import firestore

    token = f"kb_{secrets.token_urlsafe(32)}"
    doc_ref = _db().collection("tokens").document(_token_hash(token))
    doc_ref.set({
        "name": name,
        "issued_to": issued_to,
        "notes": notes,
        "prefix": token[:8],  # for identification in the UI
        "active": True,
        "created_at": firestore.SERVER_TIMESTAMP,
        "last_used_at": None,
        "usage_count": 0,
    })
    return token


def validate_token(token: str) -> dict | None:
    """Check if a token is valid. Returns token metadata or None.

    Also updates last_used_at and usage_count.
    """
    from google.cloud import firestore

    doc_ref = _db().collection("tokens").document(_token_hash(token))
    doc = doc_ref.get()
    if not doc.exists:
        return None
    data = doc.to_dict()
    if not data.get("active"):
        return None
    # Update usage stats (best-effort, don't fail auth on this)
    try:
        doc_ref.update({
            "last_used_at": firestore.SERVER_TIMESTAMP,
            "usage_count": firestore.Increment(1),
        })
    except Exception:
        pass
    return data


def list_tokens() -> list[dict]:
    """List all tokens with metadata (not the actual token values)."""
    tokens = []
    for doc in _db().collection("tokens").stream():
        data = doc.to_dict() or {}
        entry = {
            "id": doc.id,
            "name": data.get("name", ""),
            "issued_to": data.get("issued_to", ""),
            "notes": data.get("notes", ""),
            "prefix": data.get("prefix", ""),
            "active": data.get("active", False),
            "usage_count": data.get("usage_count", 0),
        }
        # Convert timestamps for JSON
        for ts_field in ("created_at", "last_used_at"):
            val = data.get(ts_field)
            entry[ts_field] = val.isoformat() if hasattr(val, "isoformat") else val
        tokens.append(entry)
    return sorted(tokens, key=lambda t: t.get("created_at") or "", reverse=True)


def revoke_token(token_hash_or_prefix: str) -> bool:
    """Revoke a token by its hash ID or prefix. Returns True if found."""
    # Try direct hash match first
    doc_ref = _db().collection("tokens").document(token_hash_or_prefix)
    doc = doc_ref.get()
    if doc.exists:
        doc_ref.update({"active": False})
        return True
    # Try prefix match
    for doc in _db().collection("tokens").stream():
        data = doc.to_dict() or {}
        if data.get("prefix", "").startswith(token_hash_or_prefix):
            doc.reference.update({"active": False})
            return True
    return False


# ── Task distribution ─────────────────────────────────────────────────────

CLAIM_TTL_SECONDS = 14400  # 4 hours
ACTIVITY_TTL_SECONDS = 1800  # 30 minutes — soft signal window


def get_next_tasks(
    dataset: str,
    instance_ids: list[str],
    backends: list[str],
    contributor: str,
    limit: int = 20,
    ttl_seconds: int = CLAIM_TTL_SECONDS,
) -> list[dict]:
    """Get prioritized task assignments with claim creation.

    Uses client-provided instance_ids as the task pool and checks
    Firestore for existing results and active claims.
    """
    from .distribute import prioritize_assignments

    # Get existing results from materialized index (cheap GCS read)
    try:
        index_bytes = get_index_json(dataset)
        index = json.loads(index_bytes)
        results = index.get("results", {})
    except Exception:
        results = {}

    active = _get_active_claims(dataset)
    pressure = get_arm_pressure(dataset)

    # Record this contributor's activity for all their backends
    touch_activity(dataset, contributor, backends)

    assignments = prioritize_assignments(
        all_instance_ids=instance_ids,
        results=results,
        backends=backends,
        active_claims=active,
        arm_pressure=pressure,
        limit=limit,
    )

    if assignments:
        _create_claims(dataset, assignments, contributor, ttl_seconds)

    return assignments


def _get_active_claims(dataset: str) -> set[tuple[str, str]]:
    """Return (instance_id, arm) pairs with active (non-expired) claims."""
    now = datetime.now(timezone.utc)
    claims: set[tuple[str, str]] = set()
    coll = _db().collection("datasets").document(dataset).collection("claims")
    for doc in coll.stream():
        data = doc.to_dict() or {}
        expires_at = data.get("expires_at")
        if expires_at is None:
            continue
        # Firestore timestamps may or may not have tzinfo
        if hasattr(expires_at, "replace"):
            exp = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
        else:
            continue
        if exp > now:
            iid = data.get("instance_id", "")
            arm = data.get("arm", "")
            if iid and arm:
                claims.add((iid, arm))
    return claims


def _create_claims(
    dataset: str,
    assignments: list[dict],
    contributor: str,
    ttl_seconds: int,
) -> None:
    """Batch-create claims for assignments."""
    from datetime import timedelta

    from google.cloud import firestore

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=ttl_seconds)

    coll = _db().collection("datasets").document(dataset).collection("claims")

    # Firestore batches max 500 operations
    for i in range(0, len(assignments), 500):
        batch = _db().batch()
        for a in assignments[i : i + 500]:
            doc_id = f"{a['instance_id']}___{a['arm']}"
            batch.set(coll.document(doc_id), {
                "instance_id": a["instance_id"],
                "arm": a["arm"],
                "contributor": contributor,
                "claimed_at": firestore.SERVER_TIMESTAMP,
                "expires_at": expires_at,
            })
        batch.commit()


def release_claim(dataset: str, instance_id: str, arm: str) -> None:
    """Release a claim (called when a result is uploaded)."""
    doc_id = f"{instance_id}___{arm}"
    try:
        _db().collection("datasets").document(dataset).collection("claims").document(doc_id).delete()
    except Exception:
        pass  # best-effort


# ── Arm activity tracking ─────────────────────────────────────────────────


def touch_activity(
    dataset: str, contributor: str, arms: list[str],
) -> None:
    """Record that a contributor is actively working on these arms.

    Called from two places:
    - get_next_tasks(): contributor explicitly requests work (declares backends)
    - save_task_result() via server: contributor uploads a result (implicit signal)

    Each (contributor, arm) pair gets a document with a 30-min TTL.
    """
    from datetime import timedelta

    from google.cloud import firestore

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=ACTIVITY_TTL_SECONDS)
    coll = _db().collection("datasets").document(dataset).collection("activity")

    batch = _db().batch()
    for arm in arms:
        doc_id = f"{contributor}___{arm}"
        batch.set(coll.document(doc_id), {
            "contributor": contributor,
            "arm": arm,
            "last_seen": firestore.SERVER_TIMESTAMP,
            "expires_at": expires_at,
        })
    batch.commit()


def get_arm_pressure(dataset: str) -> dict[str, int]:
    """Return {arm: active_contributor_count} from non-expired activity records."""
    now = datetime.now(timezone.utc)
    pressure: dict[str, int] = {}
    coll = _db().collection("datasets").document(dataset).collection("activity")
    for doc in coll.stream():
        data = doc.to_dict() or {}
        expires_at = data.get("expires_at")
        if expires_at is None:
            continue
        if hasattr(expires_at, "replace"):
            exp = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
        else:
            continue
        if exp > now:
            arm = data.get("arm", "")
            if arm:
                pressure[arm] = pressure.get(arm, 0) + 1
    return pressure


def get_scheduling_info(dataset: str) -> dict:
    """Return combined scheduling state for the dashboard.

    Returns:
        {
            "arm_pressure": {arm: count},
            "activity": [{contributor, arm, last_seen, expires_at}, ...],
            "claims": [{instance_id, arm, contributor, claimed_at, expires_at}, ...],
        }
    """
    now = datetime.now(timezone.utc)

    # Active activity records
    activity: list[dict] = []
    pressure: dict[str, int] = {}
    act_coll = _db().collection("datasets").document(dataset).collection("activity")
    for doc in act_coll.stream():
        data = doc.to_dict() or {}
        expires_at = data.get("expires_at")
        if expires_at is None:
            continue
        if hasattr(expires_at, "replace"):
            exp = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
        else:
            continue
        if exp > now:
            arm = data.get("arm", "")
            contributor = data.get("contributor", "")
            last_seen = data.get("last_seen")
            if arm:
                pressure[arm] = pressure.get(arm, 0) + 1
                activity.append({
                    "contributor": contributor,
                    "arm": arm,
                    "last_seen": last_seen.isoformat() if hasattr(last_seen, "isoformat") else str(last_seen or ""),
                    "expires_at": exp.isoformat(),
                })

    # Active claims
    claims: list[dict] = []
    claim_coll = _db().collection("datasets").document(dataset).collection("claims")
    for doc in claim_coll.stream():
        data = doc.to_dict() or {}
        expires_at = data.get("expires_at")
        if expires_at is None:
            continue
        if hasattr(expires_at, "replace"):
            exp = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
        else:
            continue
        if exp > now:
            claimed_at = data.get("claimed_at")
            claims.append({
                "instance_id": data.get("instance_id", ""),
                "arm": data.get("arm", ""),
                "contributor": data.get("contributor", ""),
                "claimed_at": claimed_at.isoformat() if hasattr(claimed_at, "isoformat") else str(claimed_at or ""),
                "expires_at": exp.isoformat(),
            })

    return {
        "arm_pressure": pressure,
        "activity": activity,
        "claims": claims,
    }
