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

from benchmark._util import docker_safe, fmt_duration, log, short_iid
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


def _fmt_size(chars: int) -> str:
    """Format character count: 1200 -> '1.2k', 0 -> 'empty'."""
    if chars == 0:
        return "empty"
    if chars < 1000:
        return f"{chars}"
    return f"{chars / 1000:.1f}k"


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
    seed: int = 0,
    assignments: list[dict] | None = None,
) -> None:
    """Run all tasks across all arms. Supports resumption.

    The ``seed`` parameter controls deduplication: tasks completed with a
    different seed are ignored, allowing the same tasks to be re-run for
    variance measurement.  Default seed=0 gives standard dedup behavior.

    When ``assignments`` is provided (distributed mode), only the specified
    (instance_id, arm) pairs are executed. Local crash-recovery dedup still
    applies but global dedup is skipped (the server already handled it).
    """
    run_dir = workspace / RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    _save_run_meta(run_dir, tasks, arms, timeout, dataset=dataset, seed=seed)

    if assignments is not None:
        # Distributed mode: only run server-assigned pairs.
        # Local completed set from THIS run for crash recovery only.
        assigned_set = {(a["instance_id"], a["arm"]) for a in assignments}
        completed = _load_completed(run_dir)
        # Mark everything not assigned as "completed" to skip it
        all_pairs = {(t.instance_id, arm) for t in tasks for arm in arms}
        completed |= all_pairs - assigned_set
    else:
        # Normal mode: skip globally completed + current run
        completed = _load_global_completed(
            workspace, exclude_run_dir=run_dir, seed=seed
        )
        completed |= _load_completed(run_dir)

    total = len(tasks) * len(arms)

    remaining = total - len(completed)
    log.info("─── Benchmark %s ───", run_id)
    log.info("  Tasks:   %d (%d remaining)", len(tasks), remaining)
    log.info("  Agents:  %s", ", ".join(arms))
    log.info(
        "  Timeout: %s per task (%s for kodo)",
        fmt_duration(timeout),
        fmt_duration(timeout_kodo),
    )
    if remaining < total:
        log.info("  Skipped: %d already completed", len(completed))

    _upload_run_online(run_id, tasks, arms, timeout, dataset)

    try:
        if parallel > 1:
            _run_parallel(
                tasks,
                arms,
                workspace,
                run_dir,
                timeout,
                timeout_kodo,
                parallel,
                completed,
                dataset,
                seed,
            )
        else:
            _run_sequential(
                tasks,
                arms,
                workspace,
                run_dir,
                timeout,
                timeout_kodo,
                completed,
                dataset,
                seed,
            )
    except BenchmarkInterrupted:
        raise  # let caller handle summary

    log.info("Run complete. Results in %s", run_dir)


class BenchmarkInterrupted(Exception):
    """Raised when the user cancels a benchmark run."""

    def __init__(self, completed_count: int = 0):
        self.completed_count = completed_count


def _run_sequential(
    tasks: list[SWETask],
    arms: list[str],
    workspace: Path,
    run_dir: Path,
    timeout: int,
    timeout_kodo: int,
    completed: set[tuple[str, str]],
    dataset: str = "",
    seed: int = 0,
) -> None:
    newly_completed = 0
    total_work = sum(
        1 for t in tasks for a in arms if (t.instance_id, a) not in completed
    )
    for task in tasks:
        for arm in arms:
            if (task.instance_id, arm) in completed:
                continue
            try:
                t = _timeout_for_arm(arm, timeout, timeout_kodo)
                log.info("  ▸ %s | %s", short_iid(task.instance_id), arm)
                result = _safe_run(task, arm, workspace, t, run_dir=run_dir)
                _append_result(run_dir, result, seed=seed)
                _append_prediction(run_dir, result)
                _upload_task_online(result, run_dir.name, dataset, workspace)
                completed.add((task.instance_id, arm))
                newly_completed += 1
                log.info(
                    "[%d/%d] %s | %s | %s (%s, %s patch)",
                    newly_completed,
                    total_work,
                    short_iid(task.instance_id),
                    arm,
                    result.status,
                    fmt_duration(int(result.elapsed_s)),
                    _fmt_size(len(result.patch)),
                )
            except KeyboardInterrupt:
                raise BenchmarkInterrupted(newly_completed)


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
    seed: int = 0,
) -> None:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    work = [
        (task, arm)
        for task in tasks
        for arm in arms
        if (task.instance_id, arm) not in completed
    ]

    newly_completed = 0
    total_work = len(work)
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futures = {}
        for task, arm in work:
            f = pool.submit(
                _safe_run,
                task,
                arm,
                workspace,
                _timeout_for_arm(arm, timeout, timeout_kodo),
                run_dir,
            )
            futures[f] = (task, arm)
            log.info("  ▸ %s | %s", short_iid(task.instance_id), arm)
        try:
            for future in as_completed(futures):
                task, arm = futures[future]
                result = future.result()
                _append_result(run_dir, result, seed=seed)
                _append_prediction(run_dir, result)
                _upload_task_online(result, run_dir.name, dataset, workspace)
                newly_completed += 1
                log.info(
                    "[%d/%d] %s | %s | %s (%s, %s patch)",
                    newly_completed,
                    total_work,
                    short_iid(task.instance_id),
                    arm,
                    result.status,
                    fmt_duration(int(result.elapsed_s)),
                    _fmt_size(len(result.patch)),
                )
        except KeyboardInterrupt:
            pool.shutdown(wait=False, cancel_futures=True)
            raise BenchmarkInterrupted(newly_completed)


def _safe_run(
    task: SWETask,
    arm: str,
    workspace: Path,
    timeout: int,
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
    task: SWETask,
    arm: str,
    workspace: Path,
    timeout: int,
    run_dir: Path | None = None,
) -> TaskResult:
    repo_dir = _prepare_repo(task, workspace, arm)
    t0 = time.monotonic()

    base, team = parse_arm(arm)
    if base == "kodo":
        agent_output, status, error, raw_stdout, raw_stderr = _run_kodo(
            task, repo_dir, timeout, team=team
        )
    elif base == "claude":
        agent_output, status, error, raw_stdout, raw_stderr = _run_claude(
            task, repo_dir, timeout, model=team
        )
    elif base == "cursor":
        agent_output, status, error, raw_stdout, raw_stderr = _run_cursor(
            task, repo_dir, timeout, model=team
        )
    elif base == "codex":
        agent_output, status, error, raw_stdout, raw_stderr = _run_codex(
            task, repo_dir, timeout, model=team
        )
    elif base == "gemini":
        agent_output, status, error, raw_stdout, raw_stderr = _run_gemini(
            task, repo_dir, timeout
        )
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
        "uv",
        "run",
        "kodo",
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
        cmd.extend(["--orchestrator", orch_model])
    return _run_subprocess(
        cmd, cwd=None, timeout=timeout, keep_api_key=bool(orch_model)
    )


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
        "stream-json",
        "--verbose",
        "--model",
        model or "opus",
        "--effort",
        "high",
    ]
    return _run_subprocess(cmd, cwd=repo_dir, timeout=timeout)


def _run_cursor(
    task: SWETask, repo_dir: Path, timeout: int, *, model: str | None = None
) -> tuple[dict, str, str, str, str]:
    """Run Cursor agent CLI in print mode."""
    from kodo.models import CURSOR_COMPOSER

    prompt = _build_prompt(task)
    cmd = [
        "cursor-agent",
        "--print",
        "--force",
        "--output-format",
        "json",
        "--model",
        model or CURSOR_COMPOSER,
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
        "-m",
        model or "gpt-5.4",
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
        "stream-json",
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
    cmd: list[str],
    cwd: Path | None,
    timeout: int,
    *,
    keep_api_key: bool = False,
) -> tuple[dict, str, str, str, str]:
    """Run a subprocess and return (parsed_output, status, error, raw_stdout, raw_stderr)."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
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
    """Best-effort: save raw stdout/stderr and kodo trace to log directory.

    Logs are gzip-compressed to save disk (stream-json output can be many MBs).
    """
    import gzip

    try:
        safe = docker_safe(arm)
        log_dir = run_dir / "logs" / instance_id / safe
        log_dir.mkdir(parents=True, exist_ok=True)

        if raw_stdout:
            (log_dir / "stdout.log.gz").write_bytes(gzip.compress(raw_stdout.encode()))
        if raw_stderr:
            (log_dir / "stderr.log.gz").write_bytes(gzip.compress(raw_stderr.encode()))

        # For kodo runs, copy the latest run trace
        base, _ = parse_arm(arm)
        if base == "kodo":
            kodo_runs = repo_dir / ".kodo" / "runs"
            if kodo_runs.is_dir():
                # Find the latest log.jsonl across all run subdirectories
                traces = sorted(
                    kodo_runs.glob("*/log.jsonl"), key=lambda p: p.stat().st_mtime
                )
                if traces:
                    data = traces[-1].read_bytes()
                    (log_dir / "kodo_trace.jsonl.gz").write_bytes(gzip.compress(data))
    except Exception:
        pass  # Best-effort: never fail the task over log capture


# ── Diff and Persistence ─────────────────────────────────────────────────


def _capture_diff(repo_dir: Path, base_commit: str) -> str:
    """Capture all changes (committed + staged + unstaged + untracked) as unified diff."""
    # Mixed reset to base_commit: collapses any worker commits back to working tree
    subprocess.run(
        ["git", "reset", base_commit],
        cwd=str(repo_dir),
        capture_output=True,
        timeout=30,
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


def _append_result(run_dir: Path, result: TaskResult, *, seed: int = 0) -> None:
    entry = {
        "instance_id": result.instance_id,
        "arm": result.arm,
        "status": result.status,
        "elapsed_s": round(result.elapsed_s, 1),
        "error": result.error,
        "patch_len": len(result.patch),
        "agent_output": result.agent_output,
        "seed": seed,
    }
    try:
        with open(run_dir / "results.jsonl", "a") as f:
            f.write(json.dumps(entry) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except OSError as exc:
        log.warning(
            "Failed to write result for %s/%s: %s", result.instance_id, result.arm, exc
        )


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
        log.warning(
            "Failed to write prediction for %s/%s: %s",
            result.instance_id,
            result.arm,
            exc,
        )


def _load_global_completed(
    workspace: Path,
    exclude_run_dir: Path | None = None,
    seed: int = 0,
) -> set[tuple[str, str]]:
    """Load completed (instance_id, arm) pairs from all prior runs.

    Only considers results with matching ``seed`` (default 0).  Results
    without a ``seed`` field are treated as seed=0 for backward compat.

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
                if entry.get("seed", 0) != seed:
                    continue
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
    run_dir: Path,
    tasks: list[SWETask],
    arms: list[str],
    timeout: int,
    *,
    dataset: str = "",
    seed: int = 0,
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
            "seed": seed,
        }
        meta_file.write_text(json.dumps(meta, indent=2))


# ── Online Upload ────────────────────────────────────────────────────────


def _upload_task_online(
    result: TaskResult,
    run_id: str,
    dataset: str,
    workspace: Path,
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
            agent_output=result.agent_output,
        )
        if ok:
            from benchmark.online.upload_tracker import mark_uploaded

            mark_uploaded(workspace, result.instance_id, result.arm, run_id)
    except Exception:
        pass


_upload_warned = False


def _upload_run_online(
    run_id: str,
    tasks: list[SWETask],
    arms: list[str],
    timeout: int,
    dataset: str,
) -> None:
    """Best-effort registration of a benchmark run.

    Logs a one-time warning if online uploads are not configured.
    """
    global _upload_warned
    try:
        from benchmark.online.client import is_configured, maybe_upload_run

        if not is_configured():
            if not _upload_warned:
                log.warning(
                    "Online uploads disabled (KODO_BENCH_URL / KODO_BENCH_TOKEN not set). "
                    "Use --upload-pending later to upload results."
                )
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
