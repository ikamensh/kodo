"""Run SWE-bench evaluation harness and parse results.

Uses Scale AI's SWE-bench Pro eval tooling for Pro datasets,
and the standard swebench harness for Lite/Verified.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from benchmark._util import docker_safe as _docker_safe, log

_EMPTY_RESULTS: dict = {"resolved": [], "failed": [], "error": [], "resolve_rate": 0.0}

# Location of the cloned scaleapi/SWE-bench_Pro-os repo
_PRO_EVAL_DIR = Path(os.environ.get(
    "SWEBENCH_PRO_EVAL_DIR",
    str(Path.home() / ".kodo" / "benchmark" / "SWE-bench_Pro-os"),
))


def evaluate_predictions(workspace: Path, run_id: str) -> None:
    """Run swebench evaluation for each arm's predictions file."""
    run_dir = workspace / "runs" / run_id

    meta_file = run_dir / "meta.json"
    dataset = ""
    if meta_file.exists():
        meta = json.loads(meta_file.read_text())
        dataset = meta.get("dataset", "")

    is_pro = "SWE-bench_Pro" in dataset

    for pred_file in sorted(run_dir.glob("predictions-*.jsonl")):
        arm = pred_file.stem.replace("predictions-", "")
        log.info("Evaluating %s...", arm)

        if is_pro:
            _evaluate_pro(pred_file, arm, run_dir)
        else:
            _evaluate_standard(pred_file, arm, run_dir, run_id, dataset)

    _collect_eval_results(run_dir, is_pro=is_pro, run_id=run_id)


def evaluate_arm(
    run_dir: Path,
    arm: str,
    run_id: str,
    dataset: str,
    on_instance: Callable[[str, bool], None] | None = None,
) -> dict:
    """Evaluate a single arm and return its results.

    Args:
        on_instance: Optional callback(instance_id, resolved) called as each
            instance completes evaluation. Enables streaming uploads.

    Returns {"resolved": [...], "failed": [...], "error": [...], "resolve_rate": float}.
    """
    safe_arm = _docker_safe(arm)
    pred_file = run_dir / f"predictions-{safe_arm}.jsonl"
    if not pred_file.exists():
        log.warning("No predictions file for arm '%s'", arm)
        return _EMPTY_RESULTS.copy()

    is_pro = "SWE-bench_Pro" in dataset
    log.info("Evaluating %s...", arm)

    if is_pro:
        _evaluate_pro(pred_file, safe_arm, run_dir)
    else:
        _evaluate_standard(pred_file, safe_arm, run_dir, run_id, dataset, on_instance)

    return _collect_arm_result(run_dir, safe_arm, run_id, is_pro)


# ── SWE-bench Pro (Scale AI tooling) ────────────────────────────────────


def _evaluate_pro(pred_file: Path, arm: str, run_dir: Path) -> None:
    """Evaluate using Scale AI's SWE-bench Pro eval script."""
    if not _PRO_EVAL_DIR.exists():
        log.warning("SWE-bench Pro eval repo not found at %s\n"
                     "  Clone it: git clone https://github.com/scaleapi/SWE-bench_Pro-os.git %s",
                     _PRO_EVAL_DIR, _PRO_EVAL_DIR)
        return

    # Convert our predictions JSONL to the patch JSON format Scale expects
    patches = []
    for line in pred_file.read_text().splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        patches.append({
            "instance_id": entry["instance_id"],
            "patch": entry.get("model_patch", ""),
            "prefix": arm,
        })

    eval_dir = run_dir / "eval" / arm
    eval_dir.mkdir(parents=True, exist_ok=True)
    patch_file = eval_dir / "patches.json"
    patch_file.write_text(json.dumps(patches, indent=2))

    # Generate raw sample JSONL from HuggingFace dataset (filtered to our instances)
    sample_file = eval_dir / "raw_samples.jsonl"
    _write_pro_samples(sample_file, [p["instance_id"] for p in patches])

    cmd = [
        sys.executable,
        str(_PRO_EVAL_DIR / "swe_bench_pro_eval.py"),
        "--raw_sample_path", str(sample_file),
        "--patch_path", str(patch_file),
        "--output_dir", str(eval_dir),
        "--scripts_dir", str(_PRO_EVAL_DIR / "run_scripts"),
        "--dockerhub_username", os.environ.get("DOCKERHUB_USERNAME", "jefzda"),
        "--num_workers", os.environ.get("SWEBENCH_EVAL_WORKERS", "4"),
        "--use_local_docker",
    ]

    try:
        subprocess.run(cmd, check=True, timeout=7200, cwd=str(_PRO_EVAL_DIR))
    except subprocess.TimeoutExpired:
        log.warning("Evaluation timed out for %s", arm)
    except subprocess.CalledProcessError as exc:
        log.warning("Evaluation failed for %s: %s", arm, exc)


def _write_pro_samples(sample_file: Path, instance_ids: list[str]) -> None:
    """Write the raw sample JSONL needed by swe_bench_pro_eval.py."""
    from datasets import load_dataset

    ds = load_dataset("ScaleAI/SWE-bench_Pro", split="test")
    id_set = set(instance_ids)

    with open(sample_file, "w") as f:
        for row in ds:
            if row["instance_id"] in id_set:
                f.write(json.dumps(dict(row)) + "\n")


# ── Standard SWE-bench (Lite / Verified) ────────────────────────────────


def _evaluate_standard(
    pred_file: Path,
    arm: str,
    run_dir: Path,
    run_id: str,
    dataset: str,
    on_instance: Callable[[str, bool], None] | None = None,
) -> None:
    """Evaluate using swebench, with optional per-instance callback via file watcher."""
    import threading

    eval_dir = run_dir / "eval" / arm
    eval_dir.mkdir(parents=True, exist_ok=True)

    if not dataset:
        dataset = "princeton-nlp/SWE-bench_Lite"

    safe_key = _docker_safe(f"{run_id}_{arm}")

    cmd = [
        sys.executable,
        "-m",
        "swebench.harness.run_evaluation",
        "--predictions_path", str(pred_file),
        "--dataset_name", dataset,
        "--run_id", safe_key,
        "--max_workers", "4",
    ]

    # Watch for report.json files as swebench writes them
    stop_watching = threading.Event()
    watch_dir = Path.cwd() / "logs" / "run_evaluation" / safe_key

    def _watcher():
        seen: set[str] = set()
        while not stop_watching.is_set():
            if watch_dir.exists():
                for model_dir in watch_dir.iterdir():
                    if not model_dir.is_dir():
                        continue
                    for instance_dir in model_dir.iterdir():
                        if not instance_dir.is_dir():
                            continue
                        iid = instance_dir.name
                        if iid in seen:
                            continue
                        report = instance_dir / "report.json"
                        if report.exists():
                            seen.add(iid)
                            try:
                                data = json.loads(report.read_text())
                                instance_data = data.get(iid, data)
                                resolved = instance_data.get("resolved", False)
                                on_instance(iid, resolved)
                            except Exception as exc:
                                log.debug("Watcher error for %s: %s", iid, exc)
            stop_watching.wait(timeout=5)

    watcher_thread = None
    if on_instance:
        watcher_thread = threading.Thread(target=_watcher, daemon=True)
        watcher_thread.start()

    try:
        subprocess.run(cmd, check=True, timeout=7200)
    except subprocess.TimeoutExpired:
        log.warning("Evaluation timed out for %s", arm)
    except subprocess.CalledProcessError as exc:
        log.warning("Evaluation failed for %s: %s", arm, exc)
    except FileNotFoundError:
        log.warning("swebench not installed. Install with: uv pip install 'swebench>=1.0'")
    finally:
        stop_watching.set()
        if watcher_thread:
            watcher_thread.join(timeout=10)

    # Copy swebench logs into the run's eval dir so results persist
    swebench_log_dir = watch_dir
    if swebench_log_dir.exists():
        dest = eval_dir / "swebench_logs"
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(swebench_log_dir, dest)
        log.info("Copied swebench logs to %s", dest)


# ── Result Collection ───────────────────────────────────────────────────


def _collect_arm_result(
    run_dir: Path, safe_arm: str, run_id: str, is_pro: bool,
) -> dict:
    """Collect evaluation results for a single arm."""
    eval_dir = run_dir / "eval" / safe_arm
    if is_pro:
        return _parse_pro_results(eval_dir) if eval_dir.exists() else _EMPTY_RESULTS.copy()

    # Check copied logs first (persistent within run dir)
    copied_logs = eval_dir / "swebench_logs" if eval_dir.exists() else None
    if copied_logs and copied_logs.exists():
        for model_dir in copied_logs.iterdir():
            if model_dir.is_dir():
                return _parse_standard_results(model_dir)

    # Fall back to swebench's cwd logs
    safe_key = _docker_safe(f"{run_id}_{safe_arm}")
    log_dir = Path.cwd() / "logs" / "run_evaluation" / safe_key
    if log_dir.exists():
        for model_dir in log_dir.iterdir():
            if model_dir.is_dir():
                return _parse_standard_results(model_dir)

    return _EMPTY_RESULTS.copy()


def _collect_eval_results(
    run_dir: Path, *, is_pro: bool = False, run_id: str = "",
) -> None:
    """Parse eval output into eval-summary.json and upload results."""
    eval_base = run_dir / "eval"
    if not eval_base.exists():
        eval_base.mkdir(parents=True)

    summary: dict[str, dict[str, Any]] = {}
    for arm_dir in sorted(eval_base.iterdir()):
        if not arm_dir.is_dir():
            continue
        summary[arm_dir.name] = _collect_arm_result(run_dir, arm_dir.name, run_id, is_pro)

    summary_file = run_dir / "eval-summary.json"
    summary_file.write_text(json.dumps(summary, indent=2))
    log.info("Eval summary written to %s", summary_file)

    # Upload eval results to server (best-effort)
    _upload_eval_summary(run_dir, summary)


def _upload_eval_summary(run_dir: Path, summary: dict[str, dict]) -> None:
    """Best-effort upload of eval results to the online server."""
    from benchmark.online.client import is_configured, upload_eval_results

    if not is_configured():
        return

    meta_file = run_dir / "meta.json"
    if not meta_file.exists():
        return
    dataset = json.loads(meta_file.read_text()).get("dataset", "")
    if not dataset:
        return

    # Build docker-safe -> original arm name map from predictions files
    arm_map = _build_arm_name_map(run_dir)

    for safe_arm, results in summary.items():
        original_arm = arm_map.get(safe_arm, safe_arm)
        resolved = results.get("resolved", [])
        failed = results.get("failed", [])
        error = results.get("error", [])
        if not resolved and not failed and not error:
            continue
        try:
            upload_eval_results(
                dataset, original_arm, resolved=resolved, failed=failed, error=error,
            )
            log.info("Uploaded eval for %s: %d resolved, %d failed",
                     original_arm, len(resolved), len(failed))
        except Exception as exc:
            log.debug("Eval upload failed for %s: %s", original_arm, exc)


def _build_arm_name_map(run_dir: Path) -> dict[str, str]:
    """Map docker-safe arm names back to originals from predictions files."""
    arm_map: dict[str, str] = {}
    for pred_file in run_dir.glob("predictions-*.jsonl"):
        safe_name = pred_file.stem.replace("predictions-", "")
        # Read the first line to get the original arm name
        first_line = pred_file.read_text().split("\n", 1)[0].strip()
        if first_line:
            try:
                entry = json.loads(first_line)
                original = entry.get("arm", safe_name)
                arm_map[safe_name] = original
            except json.JSONDecodeError:
                pass
    return arm_map


def _parse_pro_results(eval_dir: Path) -> dict:
    """Parse Scale's eval_results.json into our standard format."""
    results_file = eval_dir / "eval_results.json"
    if not results_file.exists():
        return {"resolved": [], "failed": [], "error": [], "resolve_rate": 0.0}

    results = json.loads(results_file.read_text())
    resolved = [iid for iid, passed in results.items() if passed]
    failed = [iid for iid, passed in results.items() if not passed]
    total = len(resolved) + len(failed)
    return {
        "resolved": sorted(resolved),
        "failed": sorted(failed),
        "error": [],
        "resolve_rate": len(resolved) / max(total, 1),
    }


def _parse_standard_results(eval_dir: Path) -> dict:
    """Parse standard swebench evaluation logs.

    swebench writes report.json as {instance_id: {resolved: bool, ...}}.
    """
    resolved = []
    failed = []
    errored = []

    for instance_dir in eval_dir.iterdir():
        if not instance_dir.is_dir():
            continue
        instance_id = instance_dir.name
        report_file = instance_dir / "report.json"
        if report_file.exists():
            try:
                report = json.loads(report_file.read_text())
                # swebench nests results under the instance_id key
                instance_data = report.get(instance_id, report)
                if instance_data.get("resolved", False):
                    resolved.append(instance_id)
                else:
                    failed.append(instance_id)
            except (json.JSONDecodeError, KeyError):
                errored.append(instance_id)
        else:
            errored.append(instance_id)

    total = len(resolved) + len(failed) + len(errored)
    return {
        "resolved": sorted(resolved),
        "failed": sorted(failed),
        "error": sorted(errored),
        "resolve_rate": len(resolved) / max(total, 1),
    }
