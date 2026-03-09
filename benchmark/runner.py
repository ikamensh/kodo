"""Clone repos, run agents, capture diffs, write predictions JSONL."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from benchmark._util import docker_safe, log
from benchmark.tasks import SWETask

REPO_CACHE_DIR = "repos"
RUNS_DIR = "runs"
WORK_DIR = "work"


@dataclass
class TaskResult:
    instance_id: str
    arm: str  # "claude", "kodo", "kodo:quick", "kodo:full", etc.
    patch: str
    elapsed_s: float
    status: str  # "ok", "timeout", "error"
    error: str = ""
    agent_output: dict = field(default_factory=dict)


def parse_arm(arm: str) -> tuple[str, str | None]:
    """Parse arm string into (base, team). E.g. 'kodo:quick' -> ('kodo', 'quick')."""
    if ":" in arm:
        base, team = arm.split(":", 1)
        return base, team
    return arm, None


def _timeout_for_arm(arm: str, timeout: int, timeout_kodo: int) -> int:
    """Return the appropriate timeout for an arm."""
    base, _ = parse_arm(arm)
    return timeout_kodo if base == "kodo" else timeout


def run_benchmark(
    *,
    tasks: list[SWETask],
    arms: list[str],
    workspace: Path,
    run_id: str,
    timeout: int,
    timeout_kodo: int = 43200,
    parallel: int = 1,
    dataset: str = "",
) -> None:
    """Run all tasks across all arms. Supports resumption."""
    run_dir = workspace / RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    _save_run_meta(run_dir, tasks, arms, timeout, dataset=dataset)
    # Load completed tasks: prior runs (skip errors for retry) + current run (all statuses)
    completed = _load_global_completed(workspace, exclude_run_dir=run_dir)
    completed |= _load_completed(run_dir)
    total = len(tasks) * len(arms)

    log.info("Benchmark run %s: %d tasks x %d arm(s)", run_id, len(tasks), len(arms))
    log.info("  Timeout: %ds (non-kodo), %ds (kodo)", timeout, timeout_kodo)
    log.info("  Already completed: %d/%d", len(completed), total)
    log.info("  Workspace: %s", workspace)

    _upload_run_online(run_id, tasks, arms, timeout, dataset)

    if parallel > 1:
        _run_parallel(tasks, arms, workspace, run_dir, timeout, timeout_kodo, parallel, completed, dataset)
    else:
        _run_sequential(tasks, arms, workspace, run_dir, timeout, timeout_kodo, completed, dataset)

    log.info("Run complete. Results in %s", run_dir)


def _run_sequential(
    tasks: list[SWETask],
    arms: list[str],
    workspace: Path,
    run_dir: Path,
    timeout: int,
    timeout_kodo: int,
    completed: set[tuple[str, str]],
    dataset: str = "",
) -> None:
    for i, task in enumerate(tasks):
        for arm in arms:
            if (task.instance_id, arm) in completed:
                continue

            t = _timeout_for_arm(arm, timeout, timeout_kodo)
            log.info("[%d/%d] %s (%s) [timeout %ds]", i + 1, len(tasks), task.instance_id, arm, t)
            result = _safe_run(task, arm, workspace, t, run_dir=run_dir)
            _append_result(run_dir, result)
            _append_prediction(run_dir, result)
            _upload_task_online(result, run_dir.name, dataset, workspace)
            completed.add((task.instance_id, arm))


def _run_parallel(
    tasks: list[SWETask],
    arms: list[str],
    workspace: Path,
    run_dir: Path,
    timeout: int,
    timeout_kodo: int,
    parallel: int,
    completed: set[tuple[str, str]],
    dataset: str = "",
) -> None:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    work = [
        (task, arm)
        for task in tasks
        for arm in arms
        if (task.instance_id, arm) not in completed
    ]

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futures = {
            pool.submit(_safe_run, task, arm, workspace, _timeout_for_arm(arm, timeout, timeout_kodo), run_dir): (task, arm)
            for task, arm in work
        }
        for future in as_completed(futures):
            task, arm = futures[future]
            result = future.result()
            _append_result(run_dir, result)
            _append_prediction(run_dir, result)
            _upload_task_online(result, run_dir.name, dataset, workspace)
            log.info("  %s (%s): %s (%.0fs, %d chars patch)",
                     task.instance_id, arm, result.status, result.elapsed_s, len(result.patch))


def _safe_run(
    task: SWETask, arm: str, workspace: Path, timeout: int,
    run_dir: Path | None = None,
) -> TaskResult:
    """Run a single task, catching all exceptions."""
    try:
        return _run_single_task(task, arm, workspace, timeout, run_dir=run_dir)
    except Exception as exc:
        return TaskResult(
            instance_id=task.instance_id,
            arm=arm,
            patch="",
            elapsed_s=0.0,
            status="error",
            error=str(exc),
        )


def _run_single_task(
    task: SWETask, arm: str, workspace: Path, timeout: int,
    run_dir: Path | None = None,
) -> TaskResult:
    repo_dir = _prepare_repo(task, workspace, arm)
    t0 = time.monotonic()

    base, team = parse_arm(arm)
    if base == "kodo":
        agent_output, status, error, raw_stdout, raw_stderr = _run_kodo(task, repo_dir, timeout, team=team)
    elif base == "claude":
        agent_output, status, error, raw_stdout, raw_stderr = _run_claude(task, repo_dir, timeout, model=team)
    elif base == "cursor":
        agent_output, status, error, raw_stdout, raw_stderr = _run_cursor(task, repo_dir, timeout)
    elif base == "codex":
        agent_output, status, error, raw_stdout, raw_stderr = _run_codex(task, repo_dir, timeout, model=team)
    elif base == "gemini":
        agent_output, status, error, raw_stdout, raw_stderr = _run_gemini(task, repo_dir, timeout)
    else:
        raise ValueError(f"Unknown arm: {arm}")

    elapsed = time.monotonic() - t0

    # Best-effort: save raw logs and kodo trace
    if run_dir is not None:
        _save_logs(run_dir, task.instance_id, arm, repo_dir, raw_stdout, raw_stderr)

    patch = _capture_diff(repo_dir, task.base_commit)

    return TaskResult(
        instance_id=task.instance_id,
        arm=arm,
        patch=patch,
        elapsed_s=elapsed,
        status=status,
        error=error,
        agent_output=agent_output,
    )


# ── Repo Management ──────────────────────────────────────────────────────

_clone_locks: dict[str, threading.Lock] = {}
_clone_locks_lock = threading.Lock()


def _prepare_repo(task: SWETask, workspace: Path, arm: str) -> Path:
    """Bare-clone cache + shared clone per task/arm."""
    cache_dir = workspace / REPO_CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)

    repo_slug = task.repo.replace("/", "__")
    bare_path = cache_dir / f"{repo_slug}.git"

    # Serialize bare clones per repo to avoid parallel race conditions
    with _clone_locks_lock:
        if repo_slug not in _clone_locks:
            _clone_locks[repo_slug] = threading.Lock()
        repo_lock = _clone_locks[repo_slug]

    with repo_lock:
        if not bare_path.exists():
            log.info("  Cloning %s (bare)...", task.repo)
            subprocess.run(
                [
                    "git",
                    "clone",
                    "--bare",
                    f"https://github.com/{task.repo}.git",
                    str(bare_path),
                ],
                check=True,
                capture_output=True,
                timeout=600,
            )

    # Clean up the lock now that bare_path exists (subsequent calls skip via .exists())
    if bare_path.exists():
        with _clone_locks_lock:
            _clone_locks.pop(repo_slug, None)

    work_dir = workspace / WORK_DIR / task.instance_id / arm
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True)

    # Shared clone from bare cache (hardlinks objects, fast)
    subprocess.run(
        ["git", "clone", "--shared", str(bare_path), str(work_dir)],
        check=True,
        capture_output=True,
        timeout=300,
    )
    subprocess.run(
        ["git", "checkout", task.base_commit],
        cwd=str(work_dir),
        check=True,
        capture_output=True,
        timeout=60,
    )

    return work_dir


# ── Agent Invocations ─────────────────────────────────────────────────────


def _build_prompt(task: SWETask) -> str:
    return (
        f"Fix the following GitHub issue in this repository.\n\n"
        f"Issue: {task.instance_id}\n\n"
        f"{task.problem_statement}\n\n"
        f"Make the minimal code changes needed to fix this issue. "
        f"Do not add or modify tests."
    )


def _run_kodo(
    task: SWETask, repo_dir: Path, timeout: int, *, team: str | None = None
) -> tuple[dict, str, str, str, str]:
    prompt = _build_prompt(task)
    # Parse team: "solo" or "solo+opus" (team+orchestrator_model)
    orch_model = None
    if team and "+" in team:
        team, orch_model = team.rsplit("+", 1)
    cmd = [
        "uv", "run", "kodo",
        "--goal",
        prompt,
        "--skip-intake",
        "--yes",
        "--json",
        "--no-auto-commit",
        "--project",
        str(repo_dir),
    ]
    if team:
        cmd.extend(["--team", team])
    if orch_model:
        cmd.extend(["--orchestrator", "api", "--orchestrator-model", orch_model])
    return _run_subprocess(cmd, cwd=None, timeout=timeout, keep_api_key=bool(orch_model))


def _run_claude(
    task: SWETask, repo_dir: Path, timeout: int, *, model: str | None = None
) -> tuple[dict, str, str, str, str]:
    prompt = _build_prompt(task)
    cmd = [
        "claude",
        "--print",
        "--dangerously-skip-permissions",
        "-p",
        prompt,
        "--output-format",
        "json",
        "--model",
        model or "opus",
        "--effort",
        "high",
    ]
    return _run_subprocess(cmd, cwd=repo_dir, timeout=timeout)


def _run_cursor(
    task: SWETask, repo_dir: Path, timeout: int
) -> tuple[dict, str, str, str, str]:
    """Run Cursor agent CLI in print mode."""
    prompt = _build_prompt(task)
    cmd = [
        "cursor-agent",
        "--print",
        "--force",
        "--output-format",
        "json",
        "--model",
        "composer-1.5",
        prompt,
    ]
    return _run_subprocess(cmd, cwd=repo_dir, timeout=timeout)


def _run_codex(
    task: SWETask, repo_dir: Path, timeout: int, *, model: str | None = None
) -> tuple[dict, str, str, str, str]:
    """Run OpenAI Codex CLI in non-interactive mode."""
    prompt = _build_prompt(task)
    cmd = [
        "codex",
        "exec",
        "--full-auto",
        "--json",
        "-m", model or "gpt-5.4",
    ]
    cmd.append(prompt)
    return _run_subprocess(cmd, cwd=repo_dir, timeout=timeout)


def _run_gemini(
    task: SWETask, repo_dir: Path, timeout: int
) -> tuple[dict, str, str, str, str]:
    """Run Google Gemini CLI in headless mode."""
    prompt = _build_prompt(task)
    cmd = [
        "gemini",
        "-p",
        prompt,
        "--yolo",
        "--output-format",
        "json",
    ]
    return _run_subprocess(cmd, cwd=repo_dir, timeout=timeout)


def _clean_env(*, keep_api_key: bool = False) -> dict[str, str]:
    """Return a copy of os.environ without vars that block nested sessions
    or that force API billing instead of subscription."""
    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    if not keep_api_key:
        env.pop("ANTHROPIC_API_KEY", None)
    return env


def _run_subprocess(
    cmd: list[str], cwd: Path | None, timeout: int,
    *, keep_api_key: bool = False,
) -> tuple[dict, str, str, str, str]:
    """Run a subprocess and return (parsed_output, status, error, raw_stdout, raw_stderr)."""
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd,
            env=_clean_env(keep_api_key=keep_api_key),
        )
        output = _parse_json_output(proc.stdout)
        # exit 0 = success, exit 2 = partial (kodo verification unsatisfied but patch exists)
        if proc.returncode == 0:
            status = "ok"
        elif proc.returncode == 2:
            status = "partial"
        else:
            status = "error"
        error = proc.stderr[-500:] if proc.returncode != 0 else ""
        return output, status, error, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return {}, "timeout", f"Timed out after {timeout}s", "", ""


# ── Log Capture ──────────────────────────────────────────────────────────


def _save_logs(
    run_dir: Path,
    instance_id: str,
    arm: str,
    repo_dir: Path,
    raw_stdout: str,
    raw_stderr: str,
) -> None:
    """Best-effort: save raw stdout/stderr and kodo trace to log directory."""
    try:
        safe = docker_safe(arm)
        log_dir = run_dir / "logs" / instance_id / safe
        log_dir.mkdir(parents=True, exist_ok=True)

        if raw_stdout:
            (log_dir / "stdout.log").write_text(raw_stdout)
        if raw_stderr:
            (log_dir / "stderr.log").write_text(raw_stderr)

        # For kodo runs, copy the latest run trace
        base, _ = parse_arm(arm)
        if base == "kodo":
            kodo_runs = repo_dir / ".kodo" / "runs"
            if kodo_runs.is_dir():
                # Find the latest run.jsonl across all run subdirectories
                traces = sorted(kodo_runs.glob("*/run.jsonl"), key=lambda p: p.stat().st_mtime)
                if traces:
                    shutil.copy2(traces[-1], log_dir / "kodo_trace.jsonl")
    except Exception:
        pass  # Best-effort: never fail the task over log capture


# ── Diff and Persistence ─────────────────────────────────────────────────


def _capture_diff(repo_dir: Path, base_commit: str) -> str:
    """Capture all changes (committed + staged + unstaged + untracked) as unified diff."""
    # Mixed reset to base_commit: collapses any worker commits back to working tree
    subprocess.run(
        ["git", "reset", base_commit],
        cwd=str(repo_dir), capture_output=True, timeout=30,
    )
    # Stage everything so we catch new files too
    subprocess.run(
        ["git", "add", "-A"], cwd=str(repo_dir), capture_output=True, timeout=30
    )
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--", ".", ":(exclude).kodo"],
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.stdout
    except Exception:
        return ""


def _parse_json_output(stdout: str) -> dict:
    try:
        return json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        pass
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
    return {}


def _append_result(run_dir: Path, result: TaskResult) -> None:
    entry = {
        "instance_id": result.instance_id,
        "arm": result.arm,
        "status": result.status,
        "elapsed_s": round(result.elapsed_s, 1),
        "error": result.error,
        "patch_len": len(result.patch),
        "agent_output": result.agent_output,
    }
    try:
        with open(run_dir / "results.jsonl", "a") as f:
            f.write(json.dumps(entry) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except OSError as exc:
        log.warning("Failed to write result for %s/%s: %s",
                    result.instance_id, result.arm, exc)


def _append_prediction(run_dir: Path, result: TaskResult) -> None:
    # Use arm as model name; sanitize for filenames and Docker container names
    safe = docker_safe(result.arm)
    entry = {
        "instance_id": result.instance_id,
        "model_name_or_path": safe,
        "arm": result.arm,  # original unsanitized arm for lossless round-trips
        "model_patch": result.patch,
    }
    try:
        with open(run_dir / f"predictions-{safe}.jsonl", "a") as f:
            f.write(json.dumps(entry) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except OSError as exc:
        log.warning("Failed to write prediction for %s/%s: %s",
                    result.instance_id, result.arm, exc)


def _load_global_completed(
    workspace: Path, exclude_run_dir: Path | None = None,
) -> set[tuple[str, str]]:
    """Load completed (instance_id, arm) pairs from all prior runs.

    Skips error/timeout results so they get retried in new runs.
    Skips ``exclude_run_dir`` (typically the current run) to avoid
    double-counting with ``_load_completed``.
    """
    completed: set[tuple[str, str]] = set()
    runs_dir = workspace / RUNS_DIR
    if not runs_dir.is_dir():
        return completed
    for run_dir in runs_dir.iterdir():
        if not run_dir.is_dir():
            continue
        if exclude_run_dir and run_dir == exclude_run_dir:
            continue
        results_file = run_dir / "results.jsonl"
        if not results_file.exists():
            continue
        for line in results_file.read_text().splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                status = entry.get("status", "")
                if status not in ("error", "timeout"):
                    completed.add((entry["instance_id"], entry["arm"]))
            except (json.JSONDecodeError, KeyError):
                continue
    return completed


def _load_completed(run_dir: Path) -> set[tuple[str, str]]:
    completed: set[tuple[str, str]] = set()
    results_file = run_dir / "results.jsonl"
    if results_file.exists():
        for line in results_file.read_text().splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                completed.add((entry["instance_id"], entry["arm"]))
            except (json.JSONDecodeError, KeyError):
                continue
    return completed


def _save_run_meta(
    run_dir: Path, tasks: list[SWETask], arms: list[str], timeout: int,
    *, dataset: str = "",
) -> None:
    meta_file = run_dir / "meta.json"
    if not meta_file.exists():
        from kodo import __version__ as kodo_version
        meta = {
            "kodo_version": kodo_version,
            "task_count": len(tasks),
            "arms": arms,
            "timeout": timeout,
            "dataset": dataset,
            "instance_ids": [t.instance_id for t in tasks],
        }
        meta_file.write_text(json.dumps(meta, indent=2))


# ── Online Upload ────────────────────────────────────────────────────────


def _upload_task_online(
    result: TaskResult, run_id: str, dataset: str, workspace: Path,
) -> None:
    """Best-effort upload of a single task result to the online store.

    On success, records the upload in the workspace tracker so it won't
    be re-uploaded by ``--upload-pending``.
    """
    try:
        from benchmark.online.client import maybe_upload_task_result

        ok = maybe_upload_task_result(
            instance_id=result.instance_id,
            arm=result.arm,
            status=result.status,
            elapsed_s=result.elapsed_s,
            patch=result.patch,
            error=result.error,
            run_id=run_id,
            dataset=dataset,
        )
        if ok:
            from benchmark.online.upload_tracker import mark_uploaded

            mark_uploaded(workspace, result.instance_id, result.arm, run_id)
    except Exception:
        pass


_upload_warned = False


def _upload_run_online(
    run_id: str, tasks: list[SWETask], arms: list[str], timeout: int, dataset: str,
) -> None:
    """Best-effort registration of a benchmark run.

    Logs a one-time warning if online uploads are not configured.
    """
    global _upload_warned
    try:
        from benchmark.online.client import is_configured, maybe_upload_run

        if not is_configured():
            if not _upload_warned:
                log.warning("Online uploads disabled (KODO_BENCH_URL / KODO_BENCH_TOKEN not set). "
                            "Use --upload-pending later to upload results.")
                _upload_warned = True
            return

        from kodo import __version__ as kodo_version

        maybe_upload_run(
            run_id,
            kodo_version=kodo_version,
            task_count=len(tasks),
            arms=arms,
            timeout=timeout,
            dataset=dataset,
            instance_ids=[t.instance_id for t in tasks],
        )
    except Exception:
        pass
