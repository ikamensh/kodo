"""Publish benchmark results to GitHub Pages.

Builds static files and pushes to the gh-pages branch:
  index.html                — viewer SPA
  data/{dataset}/index.json — metadata, results, provenance
  data/{dataset}/patches.json — all patches keyed by "instance_id/arm"

Incremental: reads existing files from gh-pages, merges new results, pushes.

Usage:
    uv run python -m benchmark --publish                      # publish all runs
    uv run python -m benchmark --publish --run-id <id>        # publish one run
    uv run python -m benchmark --extract-patch <instance_id> <arm>
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from benchmark._util import docker_safe, load_json, load_jsonl, log
from benchmark.online.config import collect_provenance, dataset_key as _cfg_dataset_key


VIEWER_DIR = Path(__file__).parent / "static"


# ── Publish ──────────────────────────────────────────────────────────────


def publish_results(workspace: Path, run_id: str | None = None) -> int:
    """Build data from local runs and push to gh-pages. Returns 0 on success."""
    runs_dir = workspace / "runs"
    if not runs_dir.is_dir():
        log.warning("No runs found.")
        return 1

    if run_id:
        run_dirs = [runs_dir / run_id]
        if not run_dirs[0].is_dir():
            log.warning("Run %s not found.", run_id)
            return 1
    else:
        run_dirs = sorted(d for d in runs_dir.iterdir() if d.is_dir())

    provenance = collect_provenance()
    log.info(
        "Publisher: %s@%s (%s)",
        provenance.get("user"),
        provenance.get("host"),
        provenance.get("country", "?"),
    )

    # Collect everything locally, grouped by dataset
    datasets: dict[str, _DatasetBuild] = {}

    for rd in run_dirs:
        meta = load_json(rd / "meta.json")
        if not meta:
            continue

        ds_key = _dataset_key(meta.get("dataset", ""))
        if not ds_key:
            continue

        if ds_key not in datasets:
            datasets[ds_key] = _DatasetBuild()

        ds = datasets[ds_key]
        arms = meta.get("arms", [])
        ds.arms.update(arms)

        for iid in meta.get("instance_ids", []):
            ds.tasks[iid] = {"instance_id": iid}

        # Results
        for r in load_jsonl(rd / "results.jsonl"):
            iid, arm = r.get("instance_id", ""), r.get("arm", "")
            if not iid or not arm:
                continue
            ds.results.setdefault(iid, {})[arm] = {
                "status": r.get("status", ""),
                "elapsed_s": r.get("elapsed_s", 0),
                "patch_len": r.get("patch_len", 0),
                "error": r.get("error", ""),
                "run_id": rd.name,
                "provenance": provenance,
            }

        # Eval summary
        eval_summary = load_json(rd / "eval-summary.json")
        for arm in arms:
            e = eval_summary.get(docker_safe(arm), {})
            for iid in e.get("resolved", []):
                ds.results.setdefault(iid, {}).setdefault(arm, {})
                ds.results[iid][arm].update(resolved=True, eval_status=True)
            for iid in e.get("failed", []):
                ds.results.setdefault(iid, {}).setdefault(arm, {})
                ds.results[iid][arm].update(resolved=False, eval_status=True)
            for iid in e.get("error", []):
                ds.results.setdefault(iid, {}).setdefault(arm, {})
                ds.results[iid][arm].update(resolved=False, eval_status=True)

        # Patches
        for pred_file in rd.glob("predictions-*.jsonl"):
            for line in pred_file.read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    pred = json.loads(line)
                    iid = pred["instance_id"]
                    safe_arm = pred.get("model_name_or_path", "")
                    patch = pred.get("model_patch", "")
                    if ".." in iid or ".." in safe_arm:
                        log.warning(
                            "Skipping suspicious instance_id/arm: %s/%s", iid, safe_arm
                        )
                        continue
                    if patch and iid and safe_arm:
                        ds.patches[f"{iid}/{safe_arm}"] = patch
                        # Use original arm name if available (lossless),
                        # fall back to heuristic reverse for old data.
                        orig_arm = pred.get("arm") or safe_arm.replace("_", ":")
                        if orig_arm != safe_arm:
                            ds.patches[f"{iid}/{orig_arm}"] = patch
                except (json.JSONDecodeError, KeyError):
                    continue

    # Build site in a temp dir and push to gh-pages
    with tempfile.TemporaryDirectory() as tmp:
        site_dir = Path(tmp)
        _build_site(site_dir, datasets)
        _push_gh_pages(site_dir)

    return 0


class _DatasetBuild:
    def __init__(self) -> None:
        self.tasks: dict[str, dict] = {}
        self.arms: set[str] = set()
        self.results: dict[str, dict[str, dict]] = {}
        self.patches: dict[str, str] = {}


def _build_site(site_dir: Path, datasets: dict[str, _DatasetBuild]) -> None:
    """Write static site files into site_dir."""
    # Copy viewer HTML
    for f in VIEWER_DIR.iterdir():
        if f.is_file():
            shutil.copy2(f, site_dir / f.name)

    # Merge with existing gh-pages data
    existing = _read_gh_pages_data()

    for ds_key, local in datasets.items():
        data_dir = site_dir / "data" / ds_key
        data_dir.mkdir(parents=True, exist_ok=True)

        ex_index = existing.get(ds_key, {}).get("index", {})
        ex_patches = existing.get(ds_key, {}).get("patches", {})

        merged_tasks = {}
        for t in ex_index.get("tasks", []):
            merged_tasks[t["instance_id"]] = t
        merged_tasks.update(local.tasks)

        merged_arms = set(ex_index.get("arms", []))
        merged_arms.update(local.arms)

        merged_results = ex_index.get("results", {})
        for iid, arms_data in local.results.items():
            merged_results.setdefault(iid, {})
            for arm, data in arms_data.items():
                if arm in merged_results[iid]:
                    merged_results[iid][arm].update(data)
                else:
                    merged_results[iid][arm] = data

        merged_patches = ex_patches if isinstance(ex_patches, dict) else {}
        merged_patches.update(local.patches)

        total_evaluated = sum(
            1
            for iid in merged_tasks
            if any(
                merged_results.get(iid, {}).get(a, {}).get("eval_status")
                for a in merged_arms
            )
        )

        index_data = {
            "tasks": sorted(merged_tasks.values(), key=lambda t: t["instance_id"]),
            "arms": sorted(merged_arms),
            "results": merged_results,
            "meta": {
                "dataset": ds_key,
                "total_tasks": len(merged_tasks),
                "total_evaluated": total_evaluated,
                "last_updated": datetime.now(timezone.utc).isoformat(),
            },
        }

        index_json = json.dumps(index_data, indent=2)
        patches_json = json.dumps(merged_patches)

        (data_dir / "index.json").write_text(index_json)
        (data_dir / "patches.json").write_text(patches_json)

        idx_kb = len(index_json) / 1024
        patches_kb = len(patches_json) / 1024
        log.info(
            "Built data/%s/ — %d tasks, %d arms, %d patches, %d evaluated "
            "(%dKB index + %dKB patches)",
            ds_key,
            len(merged_tasks),
            len(merged_arms),
            len(merged_patches),
            total_evaluated,
            idx_kb,
            patches_kb,
        )


def _read_gh_pages_data() -> dict[str, dict]:
    """Read existing data files from gh-pages branch."""
    result: dict[str, dict] = {}
    try:
        for ds_key in ("verified", "pro", "lite"):
            ds_data: dict[str, dict] = {}
            for fname in ("index.json", "patches.json"):
                proc = subprocess.run(
                    ["git", "show", f"gh-pages:data/{ds_key}/{fname}"],
                    capture_output=True,
                    timeout=10,
                )
                if proc.returncode == 0:
                    ds_data[fname.replace(".json", "")] = json.loads(proc.stdout)
            if ds_data:
                result[ds_key] = ds_data
    except Exception:
        pass
    return result


def _push_gh_pages(site_dir: Path) -> None:
    """Push site_dir contents to gh-pages branch using a worktree."""
    repo_root = Path(__file__).resolve().parents[1]

    with tempfile.TemporaryDirectory() as wt:
        wt_path = Path(wt) / "gh-pages"

        # Create orphan gh-pages branch if needed
        check = subprocess.run(
            ["git", "rev-parse", "--verify", "gh-pages"],
            capture_output=True,
            cwd=repo_root,
        )
        if check.returncode != 0:
            log.info("Creating gh-pages branch...")
            # Use git plumbing to create orphan branch (Apple Git lacks --orphan worktree)
            empty_tree = (
                subprocess.run(
                    ["git", "hash-object", "-t", "tree", "/dev/null"],
                    capture_output=True,
                    cwd=repo_root,
                    check=True,
                )
                .stdout.decode()
                .strip()
            )
            init_commit = (
                subprocess.run(
                    ["git", "commit-tree", "-m", "Initial gh-pages", empty_tree],
                    capture_output=True,
                    cwd=repo_root,
                    check=True,
                )
                .stdout.decode()
                .strip()
            )
            subprocess.run(
                ["git", "branch", "gh-pages", init_commit],
                capture_output=True,
                cwd=repo_root,
                check=True,
            )

        subprocess.run(
            ["git", "worktree", "add", str(wt_path), "gh-pages"],
            capture_output=True,
            cwd=repo_root,
            check=True,
        )

        try:
            # Clear old files (except .git)
            for item in wt_path.iterdir():
                if item.name == ".git":
                    continue
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()

            # Copy new site files
            for item in site_dir.iterdir():
                dest = wt_path / item.name
                if item.is_dir():
                    shutil.copytree(item, dest)
                else:
                    shutil.copy2(item, dest)

            # .nojekyll so GitHub serves files as-is
            (wt_path / ".nojekyll").touch()

            subprocess.run(
                ["git", "add", "-A"],
                cwd=wt_path,
                capture_output=True,
                check=True,
            )

            diff = subprocess.run(
                ["git", "diff", "--cached", "--quiet"],
                cwd=wt_path,
                capture_output=True,
            )
            if diff.returncode == 0:
                log.info("No changes to publish.")
                return

            ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
            subprocess.run(
                [
                    "git",
                    "commit",
                    "--no-verify",
                    "-m",
                    f"Update benchmark results ({ts})",
                ],
                cwd=wt_path,
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "push", "origin", "gh-pages"],
                cwd=wt_path,
                capture_output=True,
                check=True,
            )
            log.info("Pushed to gh-pages → https://ikamensh.github.io/kodo/")
        finally:
            subprocess.run(
                ["git", "worktree", "remove", str(wt_path), "--force"],
                capture_output=True,
                cwd=repo_root,
            )


# ── Extract ──────────────────────────────────────────────────────────────


def extract_patch(instance_id: str, arm: str) -> int:
    """Print a patch from gh-pages data."""
    for ds_key in ("verified", "pro", "lite"):
        try:
            proc = subprocess.run(
                ["git", "show", f"gh-pages:data/{ds_key}/patches.json"],
                capture_output=True,
                timeout=10,
            )
            if proc.returncode != 0:
                continue
            patches = json.loads(proc.stdout)
        except Exception:
            continue

        key = f"{instance_id}/{arm}"
        patch = patches.get(key)
        if not patch:
            patch = patches.get(f"{instance_id}/{docker_safe(arm)}")
        if patch:
            print(patch)
            return 0

    print(f"Patch not found: {instance_id} / {arm}")
    return 1


# ── Utilities ────────────────────────────────────────────────────────────


def _dataset_key(dataset: str) -> str:
    return _cfg_dataset_key(dataset)
