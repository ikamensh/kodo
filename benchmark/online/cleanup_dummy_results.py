"""Remove suspicious benchmark rows from the online store.

Dry-run by default:

    uv run python -m benchmark.online.cleanup_dummy_results --dataset pro
    uv run python -m benchmark.online.cleanup_dummy_results --dataset pro --apply
"""

from __future__ import annotations

import argparse
from collections import Counter

from benchmark.online import db
from benchmark.online.config import dataset_key
from benchmark.online.validation import suspicious_upload_reason


def candidate_rows(dataset: str, *, run_id: str = "", arm: str = "") -> list[dict]:
    """Return suspicious rows that match the optional filters."""
    rows = []
    for row in db.iter_task_results(dataset):
        if run_id and row.get("run_id") != run_id:
            continue
        if arm and row.get("arm") != arm:
            continue
        reason = suspicious_upload_reason(
            status=row.get("status", ""),
            elapsed_s=row.get("elapsed_s", 0),
            patch_len=row.get("patch_len"),
            error=row.get("error", ""),
        )
        if reason:
            rows.append({**row, "reason": reason})
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Clean suspicious benchmark uploads from online storage")
    parser.add_argument("--dataset", required=True, help="Dataset key stored online, e.g. pro")
    parser.add_argument("--run-id", default="", help="Only inspect a specific run_id")
    parser.add_argument("--arm", default="", help="Only inspect a specific arm")
    parser.add_argument(
        "--patch-len-eq",
        type=int,
        default=None,
        help="Keep only rows whose patch_len equals this value.",
    )
    parser.add_argument(
        "--reason",
        action="append",
        default=[],
        help="Keep only matching reason(s). Repeatable.",
    )
    parser.add_argument(
        "--max-elapsed",
        type=float,
        default=None,
        help="Keep only rows with elapsed_s <= this threshold.",
    )
    parser.add_argument("--apply", action="store_true", help="Delete matching rows instead of dry-run")
    args = parser.parse_args(argv)
    dataset = dataset_key(args.dataset) or args.dataset

    rows = candidate_rows(dataset, run_id=args.run_id, arm=args.arm)
    if args.patch_len_eq is not None:
        rows = [row for row in rows if (row.get("patch_len") or 0) == args.patch_len_eq]
    if args.reason:
        wanted = set(args.reason)
        rows = [row for row in rows if row["reason"] in wanted]
    if args.max_elapsed is not None:
        rows = [row for row in rows if (row.get("elapsed_s") or 0) <= args.max_elapsed]
    counts = Counter(row["reason"] for row in rows)

    print(f"dataset={dataset} candidates={len(rows)} apply={args.apply}")
    for reason, count in sorted(counts.items()):
        print(f"  {reason}: {count}")
    for row in rows[:20]:
        print(
            f"  {row['instance_id']} | {row['arm']} | {row.get('status', '')} | "
            f"{row.get('elapsed_s', 0)}s | patch_len={row.get('patch_len', 0)} | {row['reason']}"
        )
    if len(rows) > 20:
        print(f"  ... {len(rows) - 20} more")

    if not args.apply:
        return 0

    if rows and all((row.get("patch_len") or 0) == 0 for row in rows):
        db.delete_task_results_batch(
            dataset,
            [(row["instance_id"], row["arm"]) for row in rows],
        )
        empty_docs = db.delete_empty_result_docs(dataset)
        print(f"deleted={len(rows)} empty_docs={empty_docs}")
        return 0

    for row in rows:
        db.delete_task_result(
            dataset,
            row["instance_id"],
            row["arm"],
            delete_patch_blob=(row.get("patch_len") or 0) > 0,
        )
    print(f"deleted={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
