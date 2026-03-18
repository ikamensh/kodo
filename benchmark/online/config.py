"""Configuration for benchmark online services.

Environment variables:
    Server-side (Cloud Run):
        KODO_BENCH_PROJECT   — GCP project ID (default: covenance-469421)
        KODO_BENCH_BUCKET    — GCS bucket name (default: kodo-bench)

    Client-side (benchmark runner):
        KODO_BENCH_URL       — server URL (e.g. https://bench-xyz.run.app)
        KODO_BENCH_TOKEN     — API key for authenticating uploads

    Firestore setup:
        1. gcloud firestore databases create --project=PROJECT_ID --location=us-central1
        2. Deploy server to Cloud Run with service account that has
           roles/datastore.user + roles/storage.objectAdmin
        3. Use the /admin/tokens endpoint or CLI to create API tokens
        4. Give users KODO_BENCH_URL + their token
"""

from __future__ import annotations

import getpass
import json
import os
import platform
from datetime import datetime, timezone


GCP_PROJECT = os.environ.get("KODO_BENCH_PROJECT", "covenance-469421")
GCS_BUCKET = os.environ.get("KODO_BENCH_BUCKET", "kodo-bench")
VIEW_MODE = os.environ.get("KODO_BENCH_VIEW_MODE", "").strip().lower()
HEAD_TO_HEAD_OPPONENT = (
    os.environ.get(
        "KODO_BENCH_HEAD_TO_HEAD_OPPONENT",
        "cursor",
    )
    .strip()
    .lower()
)
SNAPSHOT_PREFIX = os.environ.get("KODO_BENCH_SNAPSHOT_PREFIX", "").strip().strip("/")
ALLOWED_DATASETS = frozenset(
    dataset.strip().lower()
    for dataset in os.environ.get("KODO_BENCH_ALLOWED_DATASETS", "").split(",")
    if dataset.strip()
)

# Bootstrap token: used to create the first token via /admin/tokens.
# After that, tokens live in Firestore and this can be unset.
ADMIN_TOKEN = os.environ.get("KODO_BENCH_ADMIN_TOKEN", "")

# Client-side config is resolved lazily so dotenv/load-order doesn't freeze an
# unconfigured state before the CLI has a chance to populate the environment.
_CLIENT_CREDENTIALS: tuple[str, str] | None = None


def get_client_credentials() -> tuple[str, str]:
    """Return benchmark client credentials, caching the first complete pair.

    We intentionally do not cache empty values: an early import may happen
    before dotenv or tests populate the environment, and that should not poison
    the process permanently. Once both values are present, they stay frozen for
    the rest of the run.
    """
    global _CLIENT_CREDENTIALS
    if _CLIENT_CREDENTIALS is not None:
        return _CLIENT_CREDENTIALS

    credentials = (
        os.environ.get("KODO_BENCH_URL", ""),
        os.environ.get("KODO_BENCH_TOKEN", ""),
    )
    if all(credentials):
        _CLIENT_CREDENTIALS = credentials
    return credentials


def dataset_key(dataset: str) -> str:
    """Map dataset string to short key: 'ScaleAI/SWE-bench_Pro' -> 'pro'."""
    low = dataset.lower()
    if "verified" in low:
        return "verified"
    if "pro" in low:
        return "pro"
    if "lite" in low:
        return "lite"
    return ""


def collect_provenance() -> dict:
    """Auto-collect uploader provenance from the local machine."""
    prov = {
        "user": getpass.getuser(),
        "host": platform.node(),
        "platform": f"{platform.system()} {platform.machine()}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    try:
        import urllib.request

        resp = urllib.request.urlopen("https://ipinfo.io/json", timeout=5)
        info = json.loads(resp.read())
        for field in ("ip", "city", "region", "country"):
            if field in info:
                prov[field] = info[field]
    except Exception:
        pass
    return prov
