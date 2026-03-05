"""Run SWE-bench evaluation harness and parse results.

Uses Scale AI's SWE-bench Pro eval tooling for Pro datasets,
and the standard swebench harness for Lite/Verified.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# Location of the cloned scaleapi/SWE-bench_Pro-os repo
_PRO_EVAL_DIR = Path.home() / ".kodo" / "benchmark" / "SWE-bench_Pro-os"


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
        print(f"\nEvaluating {arm}...")

        if is_pro:
            _evaluate_pro(pred_file, arm, run_dir)
        else:
            _evaluate_standard(pred_file, arm, run_dir, run_id, dataset)

    _collect_eval_results(run_dir, is_pro=is_pro, run_id=run_id)


# ── SWE-bench Pro (Scale AI tooling) ────────────────────────────────────


def _evaluate_pro(pred_file: Path, arm: str, run_dir: Path) -> None:
    """Evaluate using Scale AI's SWE-bench Pro eval script."""
    if not _PRO_EVAL_DIR.exists():
        print(
            f"  WARNING: SWE-bench Pro eval repo not found at {_PRO_EVAL_DIR}\n"
            f"  Clone it: git clone https://github.com/scaleapi/SWE-bench_Pro-os.git {_PRO_EVAL_DIR}"
        )
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
        "--dockerhub_username", "jefzda",
        "--num_workers", "4",
        "--use_local_docker",
    ]

    try:
        subprocess.run(cmd, check=True, timeout=7200, cwd=str(_PRO_EVAL_DIR))
    except subprocess.TimeoutExpired:
        print(f"  WARNING: Evaluation timed out for {arm}")
    except subprocess.CalledProcessError as exc:
        print(f"  WARNING: Evaluation failed for {arm}: {exc}")


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
    pred_file: Path, arm: str, run_dir: Path, run_id: str, dataset: str
) -> None:
    """Evaluate using the standard swebench harness."""
    eval_dir = run_dir / "eval" / arm
    eval_dir.mkdir(parents=True, exist_ok=True)

    if not dataset:
        dataset = "princeton-nlp/SWE-bench_Lite"

    cmd = [
        sys.executable,
        "-m",
        "swebench.harness.run_evaluation",
        "--predictions_path", str(pred_file),
        "--dataset_name", dataset,
        "--run_id", f"{run_id}_{arm}",
        "--max_workers", "4",
    ]

    try:
        subprocess.run(cmd, check=True, timeout=7200)
    except subprocess.TimeoutExpired:
        print(f"  WARNING: Evaluation timed out for {arm}")
    except subprocess.CalledProcessError as exc:
        print(f"  WARNING: Evaluation failed for {arm}: {exc}")
    except FileNotFoundError:
        print(
            f"  WARNING: swebench not installed. "
            f"Install with: uv pip install 'swebench>=1.0'"
        )


# ── Result Collection ───────────────────────────────────────────────────


def _collect_eval_results(
    run_dir: Path, *, is_pro: bool = False, run_id: str = "",
) -> None:
    """Parse eval output into eval-summary.json."""
    eval_base = run_dir / "eval"
    if not eval_base.exists():
        eval_base.mkdir(parents=True)

    summary: dict[str, dict] = {}

    if is_pro:
        for arm_dir in sorted(eval_base.iterdir()):
            if not arm_dir.is_dir():
                continue
            summary[arm_dir.name] = _parse_pro_results(arm_dir)
    else:
        # swebench writes to logs/run_evaluation/{run_id}_{arm}/{model_name}/...
        swebench_log_base = Path("logs/run_evaluation")
        for log_dir in sorted(swebench_log_base.iterdir()) if swebench_log_base.exists() else []:
            if not log_dir.is_dir() or not log_dir.name.startswith(run_id + "_"):
                continue
            arm = log_dir.name[len(run_id) + 1:]
            # swebench nests by model_name (= arm), then instance_id
            for model_dir in log_dir.iterdir():
                if model_dir.is_dir():
                    summary[arm] = _parse_standard_results(model_dir)
                    break

    summary_file = run_dir / "eval-summary.json"
    summary_file.write_text(json.dumps(summary, indent=2))
    print(f"\nEval summary written to {summary_file}")


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
