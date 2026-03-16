"""Run SWE-bench evaluation harness and parse results.

Uses Scale AI's SWE-bench Pro eval tooling for Pro datasets,
and the standard swebench harness for Lite/Verified.
"""

from __future__ import annotations

import contextlib
import datetime as dt
from dataclasses import dataclass, field
import json
import os
import signal
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from benchmark._util import docker_safe as _docker_safe, log

_EMPTY_RESULTS: dict = {"resolved": [], "failed": [], "error": [], "resolve_rate": 0.0}

# Default parallelism for swebench evaluation; override via SWEBENCH_EVAL_WORKERS.
_DEFAULT_EVAL_WORKERS = "4"
_DEFAULT_STALL_SECONDS = "900"
_DEFAULT_STALL_REPEAT_SECONDS = "900"
_DEFAULT_STALL_CHECK_SECONDS = "60"


def _eval_workers() -> str:
    return os.environ.get("SWEBENCH_EVAL_WORKERS", _DEFAULT_EVAL_WORKERS)


def _stall_seconds() -> int:
    return int(os.environ.get("SWEBENCH_EVAL_STALL_SECONDS", _DEFAULT_STALL_SECONDS))


def _stall_repeat_seconds() -> int:
    return int(
        os.environ.get("SWEBENCH_EVAL_STALL_REPEAT_SECONDS", _DEFAULT_STALL_REPEAT_SECONDS)
    )


def _stall_check_seconds() -> int:
    return int(
        os.environ.get("SWEBENCH_EVAL_STALL_CHECK_SECONDS", _DEFAULT_STALL_CHECK_SECONDS)
    )

# Location of the cloned scaleapi/SWE-bench_Pro-os repo
_PRO_EVAL_DIR = Path(os.environ.get(
    "SWEBENCH_PRO_EVAL_DIR",
    str(Path.home() / ".kodo" / "benchmark" / "SWE-bench_Pro-os"),
))


def _kill_subprocess_group(proc: subprocess.Popen[Any]) -> None:
    """Best-effort termination that also reaps children on POSIX."""
    if proc.poll() is not None:
        return

    if os.name != "nt":
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        except OSError:
            pass
        else:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
            return

    try:
        proc.kill()
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def _timestamp_prefix() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class _EvalHeartbeat:
    """Shared state for evaluator progress and stall diagnostics."""

    started_at: float = field(default_factory=time.monotonic)
    last_output_at: float = field(default_factory=time.monotonic)
    last_progress_at: float = field(default_factory=time.monotonic)
    last_filesystem_at: float = field(default_factory=time.monotonic)
    latest_tree_mtime: float = 0.0
    last_completed: int = 0
    in_progress: tuple[str, ...] = ()
    last_diagnostic_at: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def note_output(self) -> None:
        with self.lock:
            self.last_output_at = time.monotonic()

    def note_progress(
        self,
        *,
        completed: int,
        in_progress: list[str],
        tree_mtime: float | None = None,
    ) -> None:
        now = time.monotonic()
        with self.lock:
            if completed != self.last_completed or tuple(in_progress) != self.in_progress:
                self.last_progress_at = now
                self.last_completed = completed
                self.in_progress = tuple(in_progress)
            if tree_mtime is not None and tree_mtime > self.latest_tree_mtime:
                self.latest_tree_mtime = tree_mtime
                self.last_filesystem_at = now

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "started_at": self.started_at,
                "last_output_at": self.last_output_at,
                "last_progress_at": self.last_progress_at,
                "last_filesystem_at": self.last_filesystem_at,
                "latest_tree_mtime": self.latest_tree_mtime,
                "last_completed": self.last_completed,
                "in_progress": list(self.in_progress),
                "last_diagnostic_at": self.last_diagnostic_at,
            }

    def note_diagnostic(self) -> None:
        with self.lock:
            self.last_diagnostic_at = time.monotonic()


def _stream_with_timestamps(
    proc: subprocess.Popen[Any],
    timeout: int,
    *,
    heartbeat: _EvalHeartbeat | None = None,
) -> int:
    """Read stdout line by line (stderr merged via STDOUT), prefix with timestamp."""
    deadline = time.monotonic() + timeout
    if proc.stdout:
        for line in proc.stdout:
            if time.monotonic() > deadline:
                raise subprocess.TimeoutExpired(proc.args, timeout)
            text = line.rstrip("\n\r")
            if heartbeat:
                heartbeat.note_output()
            print(f"[{_timestamp_prefix()}] {text}", flush=True)

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise subprocess.TimeoutExpired(proc.args, timeout)
    return proc.wait(timeout=remaining)


def _run_eval_subprocess(
    cmd: list[str],
    *,
    timeout: int,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    heartbeat: _EvalHeartbeat | None = None,
    context: str = "evaluation",
    diagnostic_dir: Path | None = None,
) -> None:
    """Run an evaluator command and kill its whole process group on timeout."""
    popen_kwargs: dict[str, Any] = {
        "cwd": cwd,
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
    }
    if os.name != "nt":
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(cmd, **popen_kwargs)
    stop_monitoring = threading.Event()
    stall_thread = None
    if heartbeat:
        stall_fn = _make_stall_monitor(
            stop_monitoring,
            proc,
            heartbeat,
            context=context,
            diagnostic_dir=diagnostic_dir,
        )
        stall_thread = threading.Thread(target=stall_fn, daemon=True)
        stall_thread.start()
    try:
        returncode = _stream_with_timestamps(proc, timeout, heartbeat=heartbeat)
    except subprocess.TimeoutExpired:
        if heartbeat:
            _emit_eval_diagnostics(
                proc,
                heartbeat,
                context=f"{context} timeout",
                diagnostic_dir=diagnostic_dir,
            )
        _kill_subprocess_group(proc)
        raise
    except subprocess.CalledProcessError:
        if heartbeat:
            _emit_eval_diagnostics(
                proc,
                heartbeat,
                context=f"{context} failed",
                diagnostic_dir=diagnostic_dir,
            )
        _kill_subprocess_group(proc)
        raise
    except BaseException:
        _kill_subprocess_group(proc)
        raise
    finally:
        stop_monitoring.set()
        if stall_thread:
            stall_thread.join(timeout=_stall_check_seconds() + 5)

    if returncode != 0:
        if heartbeat:
            _emit_eval_diagnostics(
                proc,
                heartbeat,
                context=f"{context} nonzero-exit",
                diagnostic_dir=diagnostic_dir,
            )
        raise subprocess.CalledProcessError(returncode, cmd)


def _latest_tree_mtime(root: Path) -> float | None:
    """Return the newest mtime in a directory tree, if any."""
    try:
        mtimes = [root.stat().st_mtime]
    except OSError:
        return None
    try:
        for path in root.rglob("*"):
            with contextlib.suppress(OSError):
                mtimes.append(path.stat().st_mtime)
    except OSError:
        pass
    return max(mtimes) if mtimes else None


def _capture_command(cmd: list[str], *, timeout: int = 10) -> dict[str, Any]:
    """Best-effort command capture for diagnostics."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except Exception as exc:
        return {"command": cmd, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "command": cmd,
        "returncode": result.returncode,
        "stdout": result.stdout.splitlines()[:40],
        "stderr": result.stderr.splitlines()[:20],
    }


def _recent_files(paths: list[Path], *, limit: int = 8) -> list[dict[str, Any]]:
    """Return recent files under the provided paths for diagnostics."""
    entries: list[dict[str, Any]] = []
    for root in paths:
        if not root.exists():
            continue
        for path in [root, *root.rglob("*")]:
            with contextlib.suppress(OSError):
                stat = path.stat()
                entries.append({
                    "path": str(path),
                    "mtime": dt.datetime.fromtimestamp(
                        stat.st_mtime, tz=dt.timezone.utc,
                    ).isoformat(),
                    "size": stat.st_size,
                    "is_dir": path.is_dir(),
                })
    entries.sort(key=lambda item: item["mtime"], reverse=True)
    return entries[:limit]


def _emit_eval_diagnostics(
    proc: subprocess.Popen[Any],
    heartbeat: _EvalHeartbeat,
    *,
    context: str,
    diagnostic_dir: Path | None,
) -> Path | None:
    """Persist and log a best-effort diagnostic snapshot for stalled evals."""
    now = time.monotonic()
    state = heartbeat.snapshot()
    snapshot = {
        "context": context,
        "pid": proc.pid,
        "returncode": proc.poll(),
        "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "elapsed_seconds": round(now - state["started_at"], 1),
        "last_output_age_seconds": round(now - state["last_output_at"], 1),
        "last_progress_age_seconds": round(now - state["last_progress_at"], 1),
        "last_filesystem_age_seconds": round(now - state["last_filesystem_at"], 1),
        "completed": state["last_completed"],
        "in_progress": state["in_progress"],
        "process": _capture_command(
            ["ps", "-o", "pid,ppid,etime,stat,pcpu,pmem,command", "-p", str(proc.pid)],
        ),
        "children": _capture_command(["pgrep", "-P", str(proc.pid)]),
    }
    if diagnostic_dir:
        snapshot["recent_files"] = _recent_files([diagnostic_dir])
    snapshot["docker_ps"] = _capture_command(
        ["docker", "ps", "--format", "{{.ID}}\t{{.Status}}\t{{.Image}}\t{{.Names}}"],
        timeout=15,
    )

    diagnostic_path = None
    if diagnostic_dir:
        try:
            diagnostic_dir.mkdir(parents=True, exist_ok=True)
            diagnostic_path = diagnostic_dir / "stall-diagnostics.json"
            diagnostic_path.write_text(json.dumps(snapshot, indent=2, sort_keys=True))
        except OSError as exc:
            log.debug("Could not persist diagnostics for %s: %s", context, exc)
            diagnostic_path = None

    log.warning(
        "%s appears stalled: %d complete, %d in progress, last output %.1fs ago%s",
        context,
        state["last_completed"],
        len(state["in_progress"]),
        now - state["last_output_at"],
        f" (diagnostics: {diagnostic_path})" if diagnostic_path else "",
    )
    heartbeat.note_diagnostic()
    return diagnostic_path


def _make_stall_monitor(
    stop_event: threading.Event,
    proc: subprocess.Popen[Any],
    heartbeat: _EvalHeartbeat,
    *,
    context: str,
    diagnostic_dir: Path | None,
) -> Callable[[], None]:
    """Create a watchdog that logs diagnostics when an eval stops making progress."""
    stall_after = _stall_seconds()
    repeat_after = _stall_repeat_seconds()
    check_every = _stall_check_seconds()

    def _monitor() -> None:
        while not stop_event.wait(timeout=check_every):
            if proc.poll() is not None:
                return
            state = heartbeat.snapshot()
            last_activity = max(
                state["last_output_at"],
                state["last_progress_at"],
                state["last_filesystem_at"],
            )
            now = time.monotonic()
            if now - last_activity < stall_after:
                continue
            if now - state["last_diagnostic_at"] < repeat_after:
                continue
            _emit_eval_diagnostics(
                proc,
                heartbeat,
                context=context,
                diagnostic_dir=diagnostic_dir,
            )

    return _monitor


def _format_progress(completed: int, total: int, in_progress: list[str]) -> str:
    """Format a progress message for evaluation status logging."""
    msg = f"Still evaluating: {completed}/{total} complete"
    if in_progress and len(in_progress) <= 3:
        msg += f" (in progress: {', '.join(in_progress[:3])})"
    elif in_progress:
        msg += f" ({len(in_progress)} in progress)"
    return msg


def _make_progress_reporter(
    stop_event: threading.Event,
    watch_dir: Path,
    total: int,
    *,
    count_fn: Callable[[Path], tuple[int, list[str]]] | None = None,
    heartbeat: _EvalHeartbeat | None = None,
) -> Callable[[], None]:
    """Create a progress reporter function for use in a daemon thread.

    Args:
        count_fn: Custom counting function(watch_dir) -> (completed, in_progress_labels).
            If None, uses standard swebench directory layout (model_dir/instance_dir/report.json).
    """
    def _default_count(wd: Path) -> tuple[int, list[str]]:
        completed = 0
        in_progress: list[str] = []
        for model_dir in wd.iterdir():
            if not model_dir.is_dir():
                continue
            for instance_dir in model_dir.iterdir():
                if not instance_dir.is_dir():
                    continue
                if (instance_dir / "report.json").exists():
                    completed += 1
                elif len(in_progress) < 4:  # only collect a few for display
                    in_progress.append(f"{model_dir.name}/{instance_dir.name}")
        return completed, in_progress

    counter = count_fn or _default_count

    def _reporter() -> None:
        while not stop_event.wait(timeout=60):
            if not watch_dir.exists():
                continue
            try:
                completed, in_progress = counter(watch_dir)
                if heartbeat:
                    heartbeat.note_progress(
                        completed=completed,
                        in_progress=in_progress,
                        tree_mtime=_latest_tree_mtime(watch_dir),
                    )
                if completed > 0 or in_progress:
                    log.info(_format_progress(completed, total, in_progress))
            except OSError:
                pass

    return _reporter


def _make_report_watcher(
    stop_event: threading.Event,
    watch_dir: Path,
    callback: Callable[[str, str, bool], None],
    safe_to_arm: dict[str, str] | None = None,
) -> Callable[[], None]:
    """Create a watcher that polls for report.json files and invokes callback.

    Args:
        callback: Called as callback(instance_id, arm, resolved) for each new report.
        safe_to_arm: If provided, maps model_dir.name to original arm name.
            If None, arm is always model_dir.name.
    """
    def _watcher() -> None:
        seen: set[tuple[str, str]] = set()
        while not stop_event.is_set():
            if watch_dir.exists():
                for model_dir in watch_dir.iterdir():
                    if not model_dir.is_dir():
                        continue
                    arm = (safe_to_arm.get(model_dir.name, model_dir.name)
                           if safe_to_arm else model_dir.name)
                    for instance_dir in model_dir.iterdir():
                        if not instance_dir.is_dir():
                            continue
                        iid = instance_dir.name
                        if (arm, iid) in seen:
                            continue
                        report = instance_dir / "report.json"
                        if report.exists():
                            seen.add((arm, iid))
                            try:
                                data = json.loads(report.read_text())
                                instance_data = data.get(iid, data)
                                resolved = instance_data.get("resolved", False)
                                callback(iid, arm, resolved)
                            except Exception as exc:
                                log.debug("Watcher error for %s/%s: %s", arm, iid, exc)
            stop_event.wait(timeout=5)
    return _watcher


def _count_pro_progress(eval_dir: Path) -> tuple[int, list[str]]:
    """Return completed instance count and the remaining instance ids."""
    completed = 0
    in_progress: list[str] = []
    for instance_dir in sorted(eval_dir.glob("instance_*")):
        if not instance_dir.is_dir():
            continue
        if any(instance_dir.glob("*_output.json")):
            completed += 1
        else:
            in_progress.append(instance_dir.name)
    return completed, in_progress


def evaluate_predictions(workspace: Path, run_id: str) -> None:
    """Run swebench evaluation for each arm's predictions file."""
    from benchmark._util import ensure_docker_running

    if not ensure_docker_running():
        log.error("Docker is required for evaluation but could not be started")
        return

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


def evaluate_arms_combined(
    run_dir: Path,
    arm_names: list[str],
    run_id: str,
    dataset: str,
    on_instance: Callable[[str, str, bool], None] | None = None,
) -> dict[str, dict]:
    """Evaluate multiple arms in a single swebench invocation.

    Merges all arms' predictions into one file so swebench can optimally
    reuse Docker images — build an instance image once, test all arms' patches
    against it before moving on.

    Args:
        arm_names: Arms to evaluate. Prediction files must already exist.
        on_instance: Optional callback(instance_id, arm, resolved) called as each
            instance completes.

    Returns {arm: {"resolved": [...], "failed": [...], ...}} per arm.
    """
    is_pro = "SWE-bench_Pro" in dataset
    if is_pro or len(arm_names) < 2:
        # Pro eval uses a different format; single arm gains nothing from merging.
        results = {}
        for arm in arm_names:
            cb = (lambda iid, ok, a=arm: on_instance(iid, a, ok)) if on_instance else None
            results[arm] = evaluate_arm(run_dir, arm, run_id, dataset, on_instance=cb)
        return results

    # Merge all arms' predictions into one file
    combined_file = run_dir / "predictions-_combined_.jsonl"
    total_entries = 0
    with open(combined_file, "w") as out:
        for arm in arm_names:
            safe_arm = _docker_safe(arm)
            pred_file = run_dir / f"predictions-{safe_arm}.jsonl"
            if not pred_file.exists():
                log.warning("No predictions file for arm '%s', skipping", arm)
                continue
            with open(pred_file) as inp:
                for line in inp:
                    if line.strip():
                        out.write(line if line.endswith("\n") else line + "\n")
                        total_entries += 1

    if total_entries == 0:
        return {arm: _EMPTY_RESULTS.copy() for arm in arm_names}

    log.info("Combined %d predictions from %d arms into single evaluation",
             total_entries, len(arm_names))

    safe_key = _docker_safe(run_id)

    if not dataset:
        dataset = "princeton-nlp/SWE-bench_Lite"

    cmd = [
        sys.executable,
        "-m",
        "swebench.harness.run_evaluation",
        "--predictions_path", str(combined_file),
        "--dataset_name", dataset,
        "--run_id", safe_key,
        "--max_workers", _eval_workers(),
    ]
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")

    stop_watching = threading.Event()
    watch_dir = Path.cwd() / "logs" / "run_evaluation" / safe_key

    # Build reverse map: safe_arm -> original arm name
    safe_to_arm = {_docker_safe(arm): arm for arm in arm_names}

    watcher_thread = None
    if on_instance:
        watcher_fn = _make_report_watcher(
            stop_watching, watch_dir, on_instance, safe_to_arm=safe_to_arm,
        )
        watcher_thread = threading.Thread(target=watcher_fn, daemon=True)
        watcher_thread.start()

    progress_fn = _make_progress_reporter(stop_watching, watch_dir, total_entries)
    progress_thread = threading.Thread(target=progress_fn, daemon=True)
    progress_thread.start()

    try:
        _run_eval_subprocess(cmd, timeout=7200 * len(arm_names), env=env)
    except subprocess.TimeoutExpired:
        log.warning("Combined evaluation timed out")
    except subprocess.CalledProcessError as exc:
        log.warning("Combined evaluation failed: %s", exc)
    except FileNotFoundError:
        log.warning("swebench not installed. Install with: uv pip install 'swebench>=1.0'")
    finally:
        stop_watching.set()
        progress_thread.join(timeout=65)
        if watcher_thread:
            watcher_thread.join(timeout=10)

    # Copy swebench logs into per-arm eval dirs
    if watch_dir.exists():
        for model_dir in watch_dir.iterdir():
            if not model_dir.is_dir():
                continue
            safe_arm = model_dir.name
            dest = run_dir / "eval" / safe_arm / "swebench_logs" / safe_arm
            dest.mkdir(parents=True, exist_ok=True)
            for item in model_dir.iterdir():
                if item.is_dir():
                    shutil.copytree(item, dest / item.name, dirs_exist_ok=True)
        log.info("Copied swebench logs to %s/eval/*/", run_dir)

    # Collect results per arm
    results: dict[str, dict] = {}
    for arm in arm_names:
        safe_arm = _docker_safe(arm)
        results[arm] = _collect_arm_result(run_dir, safe_arm, run_id, is_pro=False)

    combined_file.unlink(missing_ok=True)
    return results


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
    total_instances = len(patches)
    patch_file = eval_dir / "patches.json"
    patch_file.write_text(json.dumps(patches, indent=2))

    # Generate raw sample JSONL from HuggingFace dataset (filtered to our instances)
    sample_file = eval_dir / "raw_samples.jsonl"
    _write_pro_samples(sample_file, [p["instance_id"] for p in patches])

    log.info(
        "Evaluation typically takes 10-30 min per instance. First run may pull Docker images (5-15 min)."
    )

    cmd = [
        sys.executable,
        str(_PRO_EVAL_DIR / "swe_bench_pro_eval.py"),
        "--raw_sample_path", str(sample_file),
        "--patch_path", str(patch_file),
        "--output_dir", str(eval_dir),
        "--scripts_dir", str(_PRO_EVAL_DIR / "run_scripts"),
        "--dockerhub_username", os.environ.get("DOCKERHUB_USERNAME", "jefzda"),
        "--num_workers", _eval_workers(),
        "--use_local_docker",
    ]

    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    stop_reporting = threading.Event()
    heartbeat = _EvalHeartbeat()

    progress_fn = _make_progress_reporter(
        stop_reporting, eval_dir, total_instances,
        count_fn=lambda _wd: _count_pro_progress(eval_dir),
        heartbeat=heartbeat,
    )
    progress_thread = threading.Thread(target=progress_fn, daemon=True)
    progress_thread.start()
    try:
        _run_eval_subprocess(
            cmd,
            timeout=7200,
            cwd=str(_PRO_EVAL_DIR),
            env=env,
            heartbeat=heartbeat,
            context=f"pro eval for {arm}",
            diagnostic_dir=eval_dir,
        )
    except subprocess.TimeoutExpired:
        log.warning("Evaluation timed out for %s", arm)
    except subprocess.CalledProcessError as exc:
        log.warning("Evaluation failed for %s: %s", arm, exc)
    finally:
        stop_reporting.set()
        progress_thread.join(timeout=65)


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
    eval_dir = run_dir / "eval" / arm
    eval_dir.mkdir(parents=True, exist_ok=True)

    if not dataset:
        dataset = "princeton-nlp/SWE-bench_Lite"

    safe_key = _docker_safe(f"{run_id}_{arm}")

    # Count instances for progress reporting
    with open(pred_file) as f:
        total_instances = sum(1 for line in f if line.strip())
    log.info(
        "Evaluation typically takes 10-30 min per instance. First run may pull Docker images (5-15 min)."
    )

    cmd = [
        sys.executable,
        "-m",
        "swebench.harness.run_evaluation",
        "--predictions_path", str(pred_file),
        "--dataset_name", dataset,
        "--run_id", safe_key,
        "--max_workers", _eval_workers(),
    ]
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    heartbeat = _EvalHeartbeat()

    # Watch for report.json files as swebench writes them
    stop_watching = threading.Event()
    watch_dir = Path.cwd() / "logs" / "run_evaluation" / safe_key

    watcher_thread = None
    if on_instance:
        watcher_fn = _make_report_watcher(
            stop_watching, watch_dir,
            lambda iid, _arm, resolved: on_instance(iid, resolved),
        )
        watcher_thread = threading.Thread(target=watcher_fn, daemon=True)
        watcher_thread.start()

    progress_fn = _make_progress_reporter(
        stop_watching, watch_dir, total_instances, heartbeat=heartbeat,
    )
    progress_thread = threading.Thread(target=progress_fn, daemon=True)
    progress_thread.start()

    try:
        _run_eval_subprocess(
            cmd,
            timeout=7200,
            env=env,
            heartbeat=heartbeat,
            context=f"standard eval for {arm}",
            diagnostic_dir=eval_dir,
        )
    except subprocess.TimeoutExpired:
        log.warning("Evaluation timed out for %s", arm)
    except subprocess.CalledProcessError as exc:
        log.warning("Evaluation failed for %s: %s", arm, exc)
    except FileNotFoundError:
        log.warning("swebench not installed. Install with: uv pip install 'swebench>=1.0'")
    finally:
        stop_watching.set()
        progress_thread.join(timeout=65)
        if watcher_thread:
            watcher_thread.join(timeout=10)

    # Copy swebench logs into the run's eval dir so results persist
    if watch_dir.exists():
        dest = eval_dir / "swebench_logs"
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(watch_dir, dest)
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
    copied_logs = eval_dir / "swebench_logs"
    if copied_logs.exists():
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
        # Read only the first line to get the original arm name
        with open(pred_file) as f:
            first_line = f.readline().strip()
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
