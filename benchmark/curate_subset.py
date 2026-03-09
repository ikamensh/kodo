"""Generate a curated subset of SWE-bench Verified for fast iteration.

Picks tasks that are good candidates for showing orchestrator benefit:
- Multi-file changes (from gold patches)
- Medium difficulty (not trivial one-liners, not impossibly hard)
- Diverse repos

Usage:
    uv run python -m benchmark.curate_subset --count 20
    uv run python -m benchmark.curate_subset --count 20 --seed 42
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Curate a SWE-bench Verified subset")
    parser.add_argument("--count", type=int, default=20, help="Number of tasks")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument(
        "--include",
        type=Path,
        default=None,
        help="Path to existing subset JSON whose tasks must be included",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parent / "subsets" / "verified-20.json",
    )
    args = parser.parse_args()

    from datasets import load_dataset

    ds = load_dataset("princeton-nlp/SWE-bench_Verified", split="test")

    # Score tasks by "orchestration benefit" potential
    scored = []
    for row in ds:
        patch = row.get("patch", "")
        files_changed = patch.count("diff --git") if patch else 0
        patch_lines = len(patch.splitlines()) if patch else 0

        # Skip trivial (<=5 lines) and huge patches (>500 lines)
        if patch_lines <= 5 or patch_lines > 500:
            continue

        # Prefer multi-file changes (orchestrator can parallelize)
        # and medium-sized patches (not trivial, not impossible)
        score = 0
        if files_changed >= 2:
            score += 3
        if files_changed >= 3:
            score += 2
        if 20 <= patch_lines <= 200:
            score += 2
        if 10 <= patch_lines < 20:
            score += 1

        scored.append((score, row["instance_id"], row["repo"]))

    # Sort by score descending, then diversify by repo
    scored.sort(key=lambda x: -x[0])

    # Greedy pick: take highest-scored, but cap per-repo to ensure diversity
    selected = []
    repo_counts: dict[str, int] = {}
    max_per_repo = max(3, args.count // 5)

    # Pre-include tasks from existing subset
    if args.include:
        prior = json.loads(args.include.read_text())
        for iid in prior["instance_ids"]:
            selected.append(iid)
            repo = iid.rsplit("-", 1)[0].replace("__", "/")
            repo_counts[repo] = repo_counts.get(repo, 0) + 1
        print(f"Pre-included {len(selected)} tasks from {args.include}")

    rng = random.Random(args.seed)
    # Shuffle within same-score groups for randomness
    grouped: dict[int, list] = {}
    for score, iid, repo in scored:
        grouped.setdefault(score, []).append((iid, repo))
    for group in grouped.values():
        rng.shuffle(group)

    for score in sorted(grouped.keys(), reverse=True):
        for iid, repo in grouped[score]:
            if len(selected) >= args.count:
                break
            if repo_counts.get(repo, 0) >= max_per_repo:
                continue
            if iid in selected:
                continue
            selected.append(iid)
            repo_counts[repo] = repo_counts.get(repo, 0) + 1
        if len(selected) >= args.count:
            break

    subset = {
        "description": f"{len(selected)}-task curated subset of SWE-bench Verified. "
        f"Biased toward multi-file, medium-complexity tasks. Seed={args.seed}.",
        "dataset": "princeton-nlp/SWE-bench_Verified",
        "instance_ids": sorted(selected),
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(subset, indent=2) + "\n")
    print(f"Wrote {len(selected)} tasks to {args.output}")

    # Print repo distribution
    for repo, count in sorted(repo_counts.items(), key=lambda x: -x[1]):
        print(f"  {repo}: {count}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
