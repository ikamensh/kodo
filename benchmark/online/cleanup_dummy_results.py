"""Remove suspicious benchmark rows from the online store.

Uses the same ``suspicious_upload_reason`` filter that gates uploads, so
anything forbidden from uploading is also prunable from storage.

Additionally supports cleaning eval errors (--eval-errors) to reset
instances whose Docker evaluation failed so they can be retried.

Dry-run by default:

    uv run python -m benchmark.online.cleanup_dummy_results --dataset pro
    uv run python -m benchmark.online.cleanup_dummy_results --dataset pro --eval-errors
    uv run python -m benchmark.online.cleanup_dummy_results --dataset pro --apply
"""

from __future__ import annotations

import argparse
from collections import Counter

from benchmark.online import db
from benchmark.online.config import dataset_key
from benchmark.online.validation import suspicious_upload_reason


def candidate_rows(dataset: str, *, run_id: str = "", arm: str = "", status: str = "") -> list[dict]:
    """Return suspicious rows that match the optional filters.

    Applies the same ``suspicious_upload_reason`` checks used at upload time.
    Note: ``agent_output`` is not persisted in Firestore, so checks that
    depend on it (agent_reported_error_without_patch) cannot be retroactively
    applied. The ``error`` field IS stored, so backend_failure markers are
    still caught via the error text.
    """
    rows = []
    for row in db.iter_task_results(dataset):
        if run_id and row.get("run_id") != run_id:
            continue
        if arm and row.get("arm") != arm:
            continue
        if status and row.get("status") != status:
            continue
        reason = suspicious_upload_reason(
            arm=row.get("arm", ""),
            status=row.get("status", ""),
            elapsed_s=row.get("elapsed_s", 0),
            patch_len=row.get("patch_len"),
            error=row.get("error", ""),
            agent_output=row.get("agent_output"),
        )
        if reason:
            rows.append({**row, "reason": reason})
    return rows


def eval_error_rows(dataset: str, *, arm: str = "") -> list[dict]:
    """Return rows where eval was attempted but the instance errored.

    These have eval_status=True and resolved=False but no successful
    evaluation — they should be reset so --evaluate-pending can retry them.
    """
    rows = []
    for row in db.iter_task_results(dataset):
        if arm and row.get("arm") != arm:
            continue
        if not row.get("eval_status"):
            continue
        # Genuine failures have resolved=False AND were evaluated;
        # we distinguish eval errors from real "failed" by checking whether
        # the underlying task status was "error" or "timeout".
        if row.get("resolved"):
            continue
        task_status = row.get("status", "")
        if task_status in ("error", "timeout", ""):
            rows.append({**row, "reason": f"eval_error (status={task_status})"})
    return rows


def _print_rows(rows: list[dict], label: str) -> None:
    counts = Counter(row["reason"] for row in rows)
    print(f"  {label}: {len(rows)} rows")
    for reason, count in sorted(counts.items()):
        print(f"    {reason}: {count}")
    for row in rows[:20]:
        print(
            f"    {row['instance_id']} | {row['arm']} | {row.get('status', '')} | "
            f"{row.get('elapsed_s', 0)}s | patch_len={row.get('patch_len', 0)} | {row['reason']}"
        )
    if len(rows) > 20:
        print(f"    ... {len(rows) - 20} more")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Clean suspicious benchmark uploads from online storage")
    parser.add_argument("--dataset", required=True, help="Dataset key stored online, e.g. pro")
    parser.add_argument("--run-id", default="", help="Only inspect a specific run_id")
    parser.add_argument("--arm", default="", help="Only inspect a specific arm")
    parser.add_argument("--status", default="", help="Only inspect rows with this task status (e.g. error, timeout)")
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
    parser.add_argument(
        "--eval-errors",
        action="store_true",
        help="Also find eval errors (eval_status=True on error/timeout results) "
        "and reset them so --evaluate-pending can retry.",
    )
    parser.add_argument("--apply", action="store_true", help="Delete/reset matching rows instead of dry-run")
    args = parser.parse_args(argv)
    dataset = dataset_key(args.dataset) or args.dataset

    # Suspicious upload rows (same filter as upload gate)
    rows = candidate_rows(dataset, run_id=args.run_id, arm=args.arm, status=args.status)
    if args.patch_len_eq is not None:
        rows = [row for row in rows if (row.get("patch_len") or 0) == args.patch_len_eq]
    if args.reason:
        wanted = set(args.reason)
        rows = [row for row in rows if row["reason"] in wanted]
    if args.max_elapsed is not None:
        rows = [row for row in rows if (row.get("elapsed_s") or 0) <= args.max_elapsed]

    # Eval error rows
    eval_errors: list[dict] = []
    if args.eval_errors:
        eval_errors = eval_error_rows(dataset, arm=args.arm)

    print(f"dataset={dataset} apply={args.apply}")

    if rows:
        _print_rows(rows, "suspicious uploads")
    if eval_errors:
        _print_rows(eval_errors, "eval errors (will reset eval_status)")
    if not rows and not eval_errors:
        print("  nothing to clean")
        return 0

    if not args.apply:
        return 0

    # Delete suspicious rows
    if rows:
        if all((row.get("patch_len") or 0) == 0 for row in rows):
            db.delete_task_results_batch(
                dataset,
                [(row["instance_id"], row["arm"]) for row in rows],
            )
            empty_docs = db.delete_empty_result_docs(dataset)
            print(f"deleted={len(rows)} empty_docs={empty_docs}")
        else:
            for row in rows:
                db.delete_task_result(
                    dataset,
                    row["instance_id"],
                    row["arm"],
                    delete_patch_blob=(row.get("patch_len") or 0) > 0,
                )
            print(f"deleted={len(rows)}")

    # Reset eval errors (clear eval_status so they can be retried)
    if eval_errors:
        db.clear_eval_status_batch(
            dataset,
            [(row["instance_id"], row["arm"]) for row in eval_errors],
        )
        print(f"reset eval_status={len(eval_errors)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
