"""Rename a benchmark arm in Firestore and GCS.

Copies all data from old_arm to new_arm, then deletes the old entries.
Use this when a default model changes and historical results need
a more specific label (e.g. "cursor" → "cursor:composer-1.5").

Dry-run by default:

    uv run python -m benchmark.online.rename_arm --dataset pro --old cursor --new cursor:composer-1.5
    uv run python -m benchmark.online.rename_arm --dataset pro --old cursor --new cursor:composer-1.5 --apply
"""

from __future__ import annotations

import argparse
import logging

from benchmark.online import db

log = logging.getLogger(__name__)


def find_rows(dataset: str, old_arm: str) -> list[dict]:
    """Return all task-result rows that have the old arm."""
    return [row for row in db.iter_task_results(dataset) if row["arm"] == old_arm]


def rename_arm(dataset: str, old_arm: str, new_arm: str, *, apply: bool = False) -> int:
    """Rename *old_arm* → *new_arm* in Firestore and GCS.

    Returns the number of rows affected.
    """
    from google.cloud import firestore

    rows = find_rows(dataset, old_arm)
    if not rows:
        print(f"  No rows found for arm={old_arm!r} in dataset={dataset!r}")
        return 0

    print(f"  Found {len(rows)} rows with arm={old_arm!r}")
    for row in rows[:10]:
        print(
            f"    {row['instance_id']} | status={row.get('status', '')} "
            f"| elapsed={row.get('elapsed_s', 0):.0f}s"
        )
    if len(rows) > 10:
        print(f"    ... {len(rows) - 10} more")

    if not apply:
        return len(rows)

    # ── Firestore: copy arm data under new key, delete old key ──────────
    coll = db._db().collection("datasets").document(dataset).collection("results")
    batch_size = 250  # two writes per row → 500 ops max per batch
    for i in range(0, len(rows), batch_size):
        batch = db._db().batch()
        for row in rows[i : i + batch_size]:
            doc_ref = coll.document(row["instance_id"])
            # Strip fields added by iter_task_results (not part of stored data)
            arm_data = {
                k: v
                for k, v in row.items()
                if k not in ("dataset", "instance_id", "arm")
            }
            batch.set(doc_ref, {"arms": {new_arm: arm_data}}, merge=True)
            batch.update(doc_ref, {f"arms.{old_arm}": firestore.DELETE_FIELD})
        batch.commit()
        print(
            f"  Firestore batch {i // batch_size + 1}: {min(batch_size, len(rows) - i)} rows"
        )

    # ── GCS: copy patch blobs to new arm path, delete old ───────────────
    bucket = db._bucket()
    copied = 0
    for row in rows:
        iid = row["instance_id"]
        seed = row.get("seed", 0)
        old_path = db._patch_blob_path(dataset, iid, old_arm, seed)
        old_blob = bucket.blob(old_path)
        if old_blob.exists():
            new_path = db._patch_blob_path(dataset, iid, new_arm, seed)
            new_blob = bucket.blob(new_path)
            new_blob.upload_from_string(
                old_blob.download_as_text(), content_type="text/plain"
            )
            old_blob.delete()
            copied += 1
    print(f"  GCS: {copied} patches moved")

    # ── Trigger index rebuild ───────────────────────────────────────────
    db._mark_dirty(dataset)
    print("  Marked dataset dirty for index rebuild")

    return len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rename a benchmark arm in online storage"
    )
    parser.add_argument("--dataset", required=True, help="Dataset key, e.g. pro")
    parser.add_argument("--old", required=True, help="Current arm name to rename")
    parser.add_argument("--new", required=True, help="New arm name")
    parser.add_argument(
        "--apply", action="store_true", help="Actually rename (default: dry-run)"
    )
    args = parser.parse_args(argv)

    from benchmark.online.config import dataset_key

    dataset = dataset_key(args.dataset) or args.dataset

    print(
        f"Rename arm: {args.old!r} → {args.new!r}  dataset={dataset}  apply={args.apply}"
    )

    if args.old == args.new:
        print("  old and new are the same, nothing to do")
        return 0

    count = rename_arm(dataset, args.old, args.new, apply=args.apply)

    if count and not args.apply:
        print(f"\n  Dry run — {count} rows would be renamed. Pass --apply to execute.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
