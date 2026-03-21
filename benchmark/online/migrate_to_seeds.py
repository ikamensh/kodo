"""One-shot migration: flat arm layout → seed-aware ``runs`` layout.

Firestore:
    For each result doc, any arm stored in the old flat format (no ``runs``
    sub-map) is rewritten as ``runs.0``.

GCS:
    Legacy patch blobs at ``{arm}.diff`` are copied to ``{arm}/0.diff``,
    then the legacy blob is deleted.

Usage:
    # Dry-run (default) — log what would change, touch nothing
    python -m benchmark.online.migrate_to_seeds

    # Apply changes
    python -m benchmark.online.migrate_to_seeds --apply
"""

from __future__ import annotations

import argparse
import logging
import sys

from . import db

log = logging.getLogger("benchmark.online.migrate")

DATASETS = ("pro", "verified")


def migrate_firestore(dataset: str, *, apply: bool = False) -> int:
    """Migrate flat-format arms to seed-aware layout. Returns count of arms migrated.

    Uses ``set(merge=True)`` with the full nested dict to avoid Firestore
    interpreting dots in arm names (e.g. ``cursor:composer-1.5``) as field
    path separators.
    """

    coll = db._db().collection("datasets").document(dataset).collection("results")
    migrated = 0

    for doc in coll.stream():
        data = doc.to_dict() or {}
        arms = data.get("arms") or {}
        new_arms: dict[str, dict] = {}
        changed = False

        for arm_name, arm_data in arms.items():
            if not isinstance(arm_data, dict):
                new_arms[arm_name] = arm_data
                continue
            if "runs" in arm_data and isinstance(arm_data["runs"], dict):
                new_arms[arm_name] = arm_data
                continue  # already in new format

            # Check for bare-seed format (keys are all digits, from botched migration)
            if arm_data and all(k.isdigit() for k in arm_data):
                # Already looks like seeds but missing the "runs" wrapper
                new_arms[arm_name] = {"runs": dict(arm_data)}
                changed = True
                log.info(
                    "[firestore] %s %s/%s/%s: bare-seeds → runs (seeds: %s)",
                    "MIGRATE" if apply else "DRY-RUN",
                    dataset,
                    doc.id,
                    arm_name,
                    sorted(arm_data.keys()),
                )
                migrated += 1
                continue

            # Flat format — wrap all fields into runs.0
            run_data = {k: v for k, v in arm_data.items()}
            new_arms[arm_name] = {"runs": {"0": run_data}}
            changed = True

            log.info(
                "[firestore] %s %s/%s/%s: flat → runs.0 (keys: %s)",
                "MIGRATE" if apply else "DRY-RUN",
                dataset,
                doc.id,
                arm_name,
                sorted(arm_data.keys()),
            )
            migrated += 1

        if changed and apply:
            # Write the full arms map to avoid dot-path issues with arm names
            doc.reference.set({"arms": new_arms}, merge=True)

    return migrated


def migrate_gcs(dataset: str, *, apply: bool = False) -> int:
    """Copy legacy ``{arm}.diff`` blobs to ``{arm}/0.diff``. Returns count migrated."""
    bucket = db._bucket()
    prefix = f"patches/{dataset}/"
    migrated = 0

    for blob in bucket.list_blobs(prefix=prefix):
        relative = blob.name[len(prefix) :]
        parts = relative.split("/")

        # Legacy layout: instance_id/arm.diff (2 parts)
        if len(parts) != 2:
            continue
        iid, arm_file = parts
        if not arm_file.endswith(".diff"):
            continue
        # Skip if it's already a seed file inside an arm directory
        # (e.g. instance_id/arm/0.diff has 3 parts, not 2)
        arm = arm_file.removesuffix(".diff")

        new_path = f"patches/{dataset}/{iid}/{arm}/0.diff"
        new_blob = bucket.blob(new_path)

        log.info(
            "[gcs] %s %s → %s",
            "MIGRATE" if apply else "DRY-RUN",
            blob.name,
            new_path,
        )

        if apply:
            if not new_blob.exists():
                bucket.copy_blob(blob, bucket, new_path)
            blob.delete()

        migrated += 1

    return migrated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Migrate benchmark data to seed-aware layout"
    )
    parser.add_argument(
        "--apply", action="store_true", help="Actually apply changes (default: dry-run)"
    )
    parser.add_argument(
        "--dataset",
        nargs="*",
        default=list(DATASETS),
        help="Datasets to migrate (default: all)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    mode = "APPLY" if args.apply else "DRY-RUN"
    log.info("Migration mode: %s", mode)

    total_fs = 0
    total_gcs = 0

    for dataset in args.dataset:
        log.info("=== Dataset: %s ===", dataset)
        total_fs += migrate_firestore(dataset, apply=args.apply)
        total_gcs += migrate_gcs(dataset, apply=args.apply)

    log.info("--- Summary (%s) ---", mode)
    log.info("Firestore arms migrated: %d", total_fs)
    log.info("GCS blobs migrated: %d", total_gcs)

    if not args.apply and (total_fs or total_gcs):
        log.info("Re-run with --apply to execute these changes.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
