"""Clone repos, run agents, capture diffs, write predictions JSONL."""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from scripts.benchmark.tasks import SWETask

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
    completed = _load_completed(run_dir)
    # Seed from prior runs: copy results for matching (instance_id, arm) pairs
    needed = {(t.instance_id, a) for t in tasks for a in arms} - completed
    seeded = _seed_from_prior_runs(workspace, run_dir, needed)
    completed |= seeded
    total = len(tasks) * len(arms)

    print(f"Benchmark run {run_id}: {len(tasks)} tasks x {len(arms)} arm(s)")
    print(f"  Timeout: {timeout}s (non-kodo), {timeout_kodo}s (kodo)")
    print(f"  Already completed: {len(completed)}/{total} ({len(seeded)} from prior runs)")
    print(f"  Workspace: {workspace}")

    if parallel > 1:
        _run_parallel(tasks, arms, workspace, run_dir, timeout, timeout_kodo, parallel, completed)
    else:
        _run_sequential(tasks, arms, workspace, run_dir, timeout, timeout_kodo, completed)

    print(f"\nRun complete. Results in {run_dir}")


def _run_sequential(
    tasks: list[SWETask],
    arms: list[str],
    workspace: Path,
    run_dir: Path,
    timeout: int,
    timeout_kodo: int,
    completed: set[tuple[str, str]],
) -> None:
    for i, task in enumerate(tasks):
        for arm in arms:
            if (task.instance_id, arm) in completed:
                continue

            t = _timeout_for_arm(arm, timeout, timeout_kodo)
            print(f"\n[{i + 1}/{len(tasks)}] {task.instance_id} ({arm}) [timeout {t}s]")
            result = _safe_run(task, arm, workspace, t)
            _append_result(run_dir, result)
            _append_prediction(run_dir, result)
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
            pool.submit(_safe_run, task, arm, workspace, _timeout_for_arm(arm, timeout, timeout_kodo)): (task, arm)
            for task, arm in work
        }
        for future in as_completed(futures):
            task, arm = futures[future]
            result = future.result()
            _append_result(run_dir, result)
            _append_prediction(run_dir, result)
            print(
                f"  {task.instance_id} ({arm}): "
                f"{result.status} ({result.elapsed_s:.0f}s, "
                f"{len(result.patch)} chars patch)"
            )


def _safe_run(
    task: SWETask, arm: str, workspace: Path, timeout: int
) -> TaskResult:
    """Run a single task, catching all exceptions."""
    try:
        return _run_single_task(task, arm, workspace, timeout)
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
    task: SWETask, arm: str, workspace: Path, timeout: int
) -> TaskResult:
    repo_dir = _prepare_repo(task, workspace, arm)
    t0 = time.monotonic()

    base, team = parse_arm(arm)
    if base == "kodo":
        agent_output, status, error = _run_kodo(task, repo_dir, timeout, team=team)
    elif base == "claude":
        agent_output, status, error = _run_claude(task, repo_dir, timeout, model=team)
    elif base == "cursor":
        agent_output, status, error = _run_cursor(task, repo_dir, timeout)
    elif base == "codex":
        agent_output, status, error = _run_codex(task, repo_dir, timeout, model=team)
    elif base == "gemini":
        agent_output, status, error = _run_gemini(task, repo_dir, timeout)
    else:
        raise ValueError(f"Unknown arm: {arm}")

    elapsed = time.monotonic() - t0
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
            print(f"  Cloning {task.repo} (bare)...")
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
) -> tuple[dict, str, str]:
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
) -> tuple[dict, str, str]:
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
) -> tuple[dict, str, str]:
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
) -> tuple[dict, str, str]:
    """Run OpenAI Codex CLI in non-interactive mode."""
    prompt = _build_prompt(task)
    cmd = [
        "codex",
        "exec",
        "--full-auto",
        "--json",
    ]
    if model:
        cmd.extend(["-m", model])
    cmd.append(prompt)
    return _run_subprocess(cmd, cwd=repo_dir, timeout=timeout)


def _run_gemini(
    task: SWETask, repo_dir: Path, timeout: int
) -> tuple[dict, str, str]:
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
    import os

    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    if not keep_api_key:
        env.pop("ANTHROPIC_API_KEY", None)
    return env


def _run_subprocess(
    cmd: list[str], cwd: Path | None, timeout: int,
    *, keep_api_key: bool = False,
) -> tuple[dict, str, str]:
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
        return output, status, error
    except subprocess.TimeoutExpired:
        return {}, "timeout", f"Timed out after {timeout}s"


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
    with open(run_dir / "results.jsonl", "a") as f:
        f.write(json.dumps(entry) + "\n")


def _append_prediction(run_dir: Path, result: TaskResult) -> None:
    # Use arm as model name; sanitize for filenames and Docker container names
    import re
    safe_arm = re.sub(r"[^a-zA-Z0-9_.-]", "_", result.arm)
    entry = {
        "instance_id": result.instance_id,
        "model_name_or_path": safe_arm,
        "model_patch": result.patch,
    }
    with open(run_dir / f"predictions-{safe_arm}.jsonl", "a") as f:
        f.write(json.dumps(entry) + "\n")


def _seed_from_prior_runs(
    workspace: Path, run_dir: Path, needed: set[tuple[str, str]],
) -> set[tuple[str, str]]:
    """Copy results from prior runs for matching (instance_id, arm) pairs."""
    seeded: set[tuple[str, str]] = set()
    if not needed:
        return seeded
    runs_dir = workspace / RUNS_DIR
    for other_dir in sorted(runs_dir.iterdir()):
        if other_dir == run_dir or not other_dir.is_dir():
            continue
        results_file = other_dir / "results.jsonl"
        if not results_file.exists():
            continue
        for line in results_file.read_text().splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                key = (entry["instance_id"], entry["arm"])
            except (json.JSONDecodeError, KeyError):
                continue
            if key not in needed or key in seeded:
                continue
            # Skip error/timeout results so they get retried
            # "partial" (kodo exit 2) still has a valid patch — keep it
            if entry.get("status") in ("error", "timeout"):
                continue
            # Copy result and prediction into current run
            _append_result_raw(run_dir, line)
            # Find matching prediction
            safe_arm = entry["arm"].replace(":", "_")
            pred_file = other_dir / f"predictions-{safe_arm}.jsonl"
            if pred_file.exists():
                for pred_line in pred_file.read_text().splitlines():
                    if not pred_line.strip():
                        continue
                    try:
                        pred = json.loads(pred_line)
                        if pred["instance_id"] == entry["instance_id"]:
                            with open(run_dir / f"predictions-{safe_arm}.jsonl", "a") as f:
                                f.write(pred_line + "\n")
                            break
                    except (json.JSONDecodeError, KeyError):
                        continue
            seeded.add(key)
    return seeded


def _append_result_raw(run_dir: Path, line: str) -> None:
    """Append a raw JSON line to results.jsonl."""
    with open(run_dir / "results.jsonl", "a") as f:
        f.write(line.strip() + "\n")


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
        meta = {
            "task_count": len(tasks),
            "arms": arms,
            "timeout": timeout,
            "dataset": dataset,
            "instance_ids": [t.instance_id for t in tasks],
        }
        meta_file.write_text(json.dumps(meta, indent=2))
