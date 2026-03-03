"""Run SWE-bench evaluation harness and parse results."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


def evaluate_predictions(workspace: Path, run_id: str) -> None:
    """Run swebench evaluation for each arm's predictions file."""
    run_dir = workspace / "runs" / run_id

    for pred_file in sorted(run_dir.glob("predictions-*.jsonl")):
        arm = pred_file.stem.replace("predictions-", "")
        print(f"\nEvaluating {arm}...")

        eval_dir = run_dir / "eval" / arm
        eval_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            "python",
            "-m",
            "swebench.harness.run_evaluation",
            "--predictions_path",
            str(pred_file),
            "--dataset_name",
            "princeton-nlp/SWE-bench_Lite",
            "--run_id",
            f"{run_id}_{arm}",
            "--max_workers",
            "4",
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
            return

    _collect_eval_results(run_dir)


def _collect_eval_results(run_dir: Path) -> None:
    """Parse swebench eval output into eval-summary.json."""
    eval_base = run_dir / "eval"
    if not eval_base.exists():
        return

    summary: dict[str, dict] = {}
    for arm_dir in sorted(eval_base.iterdir()):
        if not arm_dir.is_dir():
            continue
        summary[arm_dir.name] = _parse_eval_logs(arm_dir)

    summary_file = run_dir / "eval-summary.json"
    summary_file.write_text(json.dumps(summary, indent=2))
    print(f"\nEval summary written to {summary_file}")


def _parse_eval_logs(eval_dir: Path) -> dict:
    """Parse swebench evaluation logs into per-instance results."""
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
                if report.get("resolved", False):
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
