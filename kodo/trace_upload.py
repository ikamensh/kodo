"""Best-effort upload of run traces to GCS + Firestore.

Gated behind KODO_TRACE_UPLOAD=1 env var.
Uploads the entire run directory (log.jsonl + conversations/) as a tarball
to GCS and writes a metadata index doc to Firestore.

GCS:        gs://{bucket}/traces/{run_id}/trace.tar.gz
Firestore:  traces/{run_id}  — metadata doc
"""

from __future__ import annotations

import getpass
import io
import logging
import os
import platform
import re
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_log = logging.getLogger(__name__)

_GCP_PROJECT = "covenance-469421"
_GCS_BUCKET = "kodo-bench"
_TEXT_ARCHIVE_FILES = frozenset(
    {"config.json", "goal.md", "log.jsonl", "run.jsonl", "team.json"}
)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"""(?ix)
    (?P<prefix>
        (?:["'])?
        (?P<key>[a-z0-9_.-]*(?:api[_-]?key|secret|token|password|passwd|private[_-]?key|access[_-]?key)[a-z0-9_.-]*)
        (?:["'])?
        \s*(?P<sep>=|:)\s*
    )
    (?P<quote>["'])?
    (?P<value>[^"',\s}]+)
    (?P=quote)?
    """
)

# Lazy-initialized clients
_db_client = None
_gcs_bucket_obj = None
_pii_cleaner = None


@dataclass(frozen=True)
class ArchiveStats:
    redactions: int = 0
    files_changed: int = 0

    def add(self, *, redactions: int = 0, file_changed: bool = False) -> ArchiveStats:
        return ArchiveStats(
            redactions=self.redactions + redactions,
            files_changed=self.files_changed + int(file_changed),
        )


@dataclass(frozen=True)
class ArchiveResult:
    path: Path
    stats: ArchiveStats


def is_enabled() -> bool:
    return os.environ.get("KODO_TRACE_UPLOAD", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _db():
    global _db_client
    if _db_client is None:
        from google.cloud import firestore

        _db_client = firestore.Client(project=_GCP_PROJECT)
    return _db_client


def _bucket():
    global _gcs_bucket_obj
    if _gcs_bucket_obj is None:
        from google.cloud import storage

        client = storage.Client(project=_GCP_PROJECT)
        _gcs_bucket_obj = client.bucket(_GCS_BUCKET)
    return _gcs_bucket_obj


def _get_pii_cleaner():
    global _pii_cleaner
    if _pii_cleaner is None:
        from piicleaner import Cleaner

        _pii_cleaner = Cleaner()
    return _pii_cleaner


def _scrub_pii(text: str) -> tuple[str, int]:
    cleaner = _get_pii_cleaner()
    matches = cleaner.detect_pii(text)
    if not matches:
        return text, 0
    return cleaner.clean_pii(text, "redact"), len(matches)


def _scrub_secrets(text: str) -> tuple[str, int]:
    from detect_secrets.core import scan
    from detect_secrets.settings import default_settings

    redactions = 0
    with tempfile.TemporaryDirectory() as tmpdir:
        sample = Path(tmpdir) / "archive.txt"
        sample.write_text(text, encoding="utf-8")
        with default_settings():
            secrets = {
                finding.secret_value
                for finding in scan.scan_file(str(sample))
                if getattr(finding, "secret_value", "")
            }

    for secret in sorted(secrets, key=len, reverse=True):
        count = text.count(secret)
        if count:
            redactions += count
            text = text.replace(secret, "[secret-redacted]")

    def redact_assignment(match: re.Match[str]) -> str:
        quote = match.group("quote") or ""
        return f"{match.group('prefix')}{quote}[secret-redacted]{quote}"

    text, assignment_count = _SECRET_ASSIGNMENT_RE.subn(redact_assignment, text)
    return text, redactions + assignment_count


def _scrub_archive_text(text: str) -> tuple[str, int]:
    text, secret_redactions = _scrub_secrets(text)
    text, pii_redactions = _scrub_pii(text)
    return text, secret_redactions + pii_redactions


def _archive_payload(path: Path) -> tuple[bytes, ArchiveStats]:
    if path.name in _TEXT_ARCHIVE_FILES or path.parent.name == "conversations":
        text = path.read_text(encoding="utf-8")
        scrubbed_text, redactions = _scrub_archive_text(text)
        stats = ArchiveStats(
            redactions=redactions, files_changed=int(scrubbed_text != text)
        )
        return scrubbed_text.encode("utf-8"), stats
    return path.read_bytes(), ArchiveStats()


def _add_archive_file(tar: tarfile.TarFile, path: Path, arcname: str) -> ArchiveStats:
    payload, stats = _archive_payload(path)
    stat = path.stat()
    info = tarfile.TarInfo(arcname)
    info.size = len(payload)
    info.mode = stat.st_mode & 0o777
    info.mtime = stat.st_mtime
    tar.addfile(info, io.BytesIO(payload))
    return stats


def _tar_run_dir(run_dir: Path) -> tuple[bytes, ArchiveStats]:
    """Create a gzipped tar of log + conversations + config/goal."""
    buf = io.BytesIO()
    stats = ArchiveStats()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        log_file = run_dir / "log.jsonl"
        if not log_file.exists():
            log_file = run_dir / "run.jsonl"  # legacy
        if log_file.exists():
            file_stats = _add_archive_file(tar, log_file, log_file.name)
            stats = stats.add(
                redactions=file_stats.redactions,
                file_changed=bool(file_stats.files_changed),
            )
        conv_dir = run_dir / "conversations"
        if conv_dir.is_dir():
            for f in sorted(conv_dir.iterdir()):
                file_stats = _add_archive_file(tar, f, f"conversations/{f.name}")
                stats = stats.add(
                    redactions=file_stats.redactions,
                    file_changed=bool(file_stats.files_changed),
                )
        for name in ("config.json", "team.json", "goal.md"):
            p = run_dir / name
            if p.exists():
                file_stats = _add_archive_file(tar, p, name)
                stats = stats.add(
                    redactions=file_stats.redactions,
                    file_changed=bool(file_stats.files_changed),
                )
    return buf.getvalue(), stats


def pack_run_archive(run_dir: Path) -> ArchiveResult:
    """Pack run directory into run.tar.gz for sharing (e.g. GitHub issue attach).

    Includes log, conversations/, config.json, team.json, goal.md.
    Returns the created archive path and scrub summary.
    """
    out_path = run_dir / "run.tar.gz"
    payload, stats = _tar_run_dir(run_dir)
    out_path.write_bytes(payload)
    return ArchiveResult(path=out_path, stats=stats)


def upload_trace(
    *,
    run_id: str,
    run_dir: Path,
    project_dir: Path,
    goal: str,
    agent_count: int,
    total_cost_usd: float,
    total_exchanges: int,
    total_cycles: int,
    finished: bool,
    run_error: BaseException | None,
    orchestrator: str,
    model: str,
    elapsed_s: float | None,
) -> None:
    """Upload run trace to GCS and write Firestore index doc. Best-effort."""
    if not is_enabled():
        return

    from kodo import __version__

    # 1. Tar and upload to GCS
    try:
        payload, _ = _tar_run_dir(run_dir)
        blob = _bucket().blob(f"traces/{run_id}/trace.tar.gz")
        blob.upload_from_string(payload, content_type="application/gzip")
    except Exception as exc:
        _log.debug("Trace upload to GCS failed: %s", exc)
        return  # if GCS fails, skip Firestore too

    # 2. Write metadata to Firestore
    outcome = "error" if run_error else ("completed" if finished else "partial")
    meta = {
        "run_id": run_id,
        "user": getpass.getuser(),
        "host": platform.node(),
        "platform": f"{platform.system()} {platform.machine()}",
        "project_dir": str(project_dir),
        "goal": goal[:2000],
        "agent_count": agent_count,
        "total_cost_usd": round(total_cost_usd, 4),
        "total_exchanges": total_exchanges,
        "total_cycles": total_cycles,
        "finished": finished,
        "outcome": outcome,
        "kodo_version": __version__,
        "orchestrator": orchestrator,
        "model": model,
        "elapsed_s": round(elapsed_s, 1) if elapsed_s is not None else None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "trace_gcs_path": f"gs://{_GCS_BUCKET}/traces/{run_id}/trace.tar.gz",
        "trace_size_bytes": len(payload),
    }
    if run_error:
        meta["error"] = f"{type(run_error).__name__}: {run_error}"[:500]

    try:
        from google.cloud import firestore

        _db().collection("traces").document(run_id).set(
            {
                **meta,
                "created_at": firestore.SERVER_TIMESTAMP,
            }
        )
    except Exception as exc:
        _log.debug("Trace metadata write to Firestore failed: %s", exc)
