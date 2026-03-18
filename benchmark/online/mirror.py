"""Mirror public benchmark results into local JSON files and Python rows.

The online benchmark already exposes public dataset snapshots as JSON blobs.
This module keeps the local analysis path intentionally simple:

>>> len(flatten_index_rows({"results": {}, "meta": {"dataset": "verified"}}))
0
>>> rows = flatten_index_rows({
...     "results": {
...         "repo__issue-1": {
...             "claude": {"status": "ok", "resolved": True, "elapsed_s": 12.5}
...         }
...     },
...     "meta": {"dataset": "verified"},
... })
>>> rows[0]["dataset"], rows[0]["instance_id"], rows[0]["arm"], rows[0]["resolved"]
('verified', 'repo__issue-1', 'claude', True)
"""

from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_PUBLIC_URL = "https://kodo-bench-430011644943.europe-west1.run.app"


def public_base_url(base_url: str | None = None) -> str:
    """Return the benchmark public URL, preferring explicit input over env."""
    import os

    return (base_url or os.environ.get("KODO_BENCH_URL") or DEFAULT_PUBLIC_URL).rstrip(
        "/"
    )


def fetch_public_json(
    path: str, *, base_url: str | None = None, timeout: int = 60
) -> dict:
    """Fetch JSON from the public benchmark server."""
    url = f"{public_base_url(base_url)}{path}"
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read())


def fetch_dataset_index(dataset: str, *, base_url: str | None = None) -> dict:
    """Fetch public index.json for one dataset."""
    return fetch_public_json(f"/data/{dataset}/index.json", base_url=base_url)


def fetch_dataset_patches(
    dataset: str, *, base_url: str | None = None
) -> dict[str, str]:
    """Fetch public patches.json for one dataset."""
    data = fetch_public_json(
        f"/data/{dataset}/patches.json", base_url=base_url, timeout=300
    )
    if not isinstance(data, dict):
        raise TypeError(
            f"Expected patches dict for {dataset}, got {type(data).__name__}"
        )
    return {str(k): str(v) for k, v in data.items()}


def flatten_index_rows(index: dict) -> list[dict]:
    """Flatten index.json into one row per instance/arm for plotting.

    The output is stable under refactors of the nested JSON shape as long as
    the semantic fields stay the same.
    """
    rows: list[dict] = []
    dataset = str((index.get("meta") or {}).get("dataset", ""))
    results = index.get("results") or {}

    for instance_id, arms in results.items():
        if not isinstance(arms, dict):
            continue
        for arm, result in arms.items():
            if not isinstance(result, dict):
                continue
            row = {
                "dataset": dataset,
                "instance_id": str(instance_id),
                "arm": str(arm),
                "status": result.get("status"),
                "resolved": result.get("resolved"),
                "eval_status": result.get("eval_status"),
                "elapsed_s": result.get("elapsed_s"),
                "patch_len": result.get("patch_len"),
                "error": result.get("error"),
                "run_id": result.get("run_id"),
            }
            provenance = result.get("provenance") or {}
            if isinstance(provenance, dict):
                for key in (
                    "user",
                    "host",
                    "platform",
                    "country",
                    "city",
                    "region",
                    "timestamp",
                ):
                    if key in provenance:
                        row[f"provenance_{key}"] = provenance[key]
            rows.append(row)

    rows.sort(key=lambda row: (row["instance_id"], row["arm"]))
    return rows


def mirror_dataset(
    dataset: str,
    *,
    out_dir: Path,
    include_patches: bool = False,
    base_url: str | None = None,
) -> Path:
    """Mirror one dataset into a local directory and return that directory path."""
    out_dir = out_dir.expanduser()
    dataset_dir = out_dir / dataset
    dataset_dir.mkdir(parents=True, exist_ok=True)

    index = fetch_dataset_index(dataset, base_url=base_url)
    rows = flatten_index_rows(index)

    (dataset_dir / "index.json").write_text(json.dumps(index, indent=2) + "\n")
    (dataset_dir / "rows.json").write_text(json.dumps(rows, indent=2) + "\n")

    if include_patches:
        patches = fetch_dataset_patches(dataset, base_url=base_url)
        (dataset_dir / "patches.json").write_text(json.dumps(patches) + "\n")

    return dataset_dir


def load_rows(path: str | Path) -> list[dict]:
    """Load flattened rows from a mirrored dataset directory.

    Accepts either `rows.json` directly or the dataset directory containing it.
    """
    rows_path = Path(path).expanduser()
    if rows_path.is_dir():
        rows_path = rows_path / "rows.json"
    return json.loads(rows_path.read_text())


def fetch_patch(
    dataset: str,
    instance_id: str,
    arm: str,
    *,
    base_url: str | None = None,
    timeout: int = 60,
) -> str:
    """Fetch a single patch without downloading patches.json."""
    quoted_iid = urllib.parse.quote(instance_id, safe="")
    quoted_arm = urllib.parse.quote(arm, safe="")
    url = f"{public_base_url(base_url)}/api/patch/{dataset}/{quoted_iid}/{quoted_arm}"
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read().decode()


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for local mirroring."""
    parser = argparse.ArgumentParser(description="Mirror public benchmark data locally")
    parser.add_argument(
        "--dataset",
        action="append",
        required=True,
        help="Dataset to mirror. Repeatable, e.g. --dataset verified --dataset pro",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path.home() / ".kodo" / "benchmark" / "mirror",
        help="Output directory for mirrored JSON files",
    )
    parser.add_argument(
        "--patches",
        action="store_true",
        help="Also download patches.json for each dataset",
    )
    parser.add_argument(
        "--base-url", type=str, default=None, help="Override the public benchmark URL"
    )
    args = parser.parse_args(argv)

    for dataset in args.dataset:
        mirror_dataset(
            dataset,
            out_dir=args.out,
            include_patches=args.patches,
            base_url=args.base_url,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
