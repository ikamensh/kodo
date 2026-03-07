"""Git operations for orchestrator worktree management and merging."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

_GIT_TIMEOUT = 60  # seconds; prevents indefinite block on git commands

_KODO_GIT_ENV = {
    "GIT_AUTHOR_NAME": "kodo",
    "GIT_AUTHOR_EMAIL": "noreply@github.com",
    "GIT_COMMITTER_NAME": "kodo",
    "GIT_COMMITTER_EMAIL": "noreply@github.com",
}


_GIT = "git"


@dataclass
class MergeResult:
    """Result of merging a worktree branch back into the main branch."""

    success: bool
    had_changes: bool
    conflict: bool = False
    error: str = ""


def create_worktree(project_dir: Path, label: str) -> tuple[Path, str]:
    """Create a git worktree for isolated parallel execution.

    Returns ``(worktree_dir, branch_name)``.  The worktree is placed in a
    temp directory (outside the repo) and uses a unique branch name to avoid
    collisions with leftover branches from crashed runs.

    The label is sanitized by replacing special characters (/, @, :, etc.)
    with underscores to ensure git branch name validity.
    """
    # Sanitize label: replace special characters with underscores
    # Git branch names cannot contain /, @, :, ^, ~, ?, *, [, \, space, etc.
    sanitized_label = re.sub(r"[/@:^~?\*\[\\\s]+", "_", label)

    suffix = uuid.uuid4().hex[:8]
    branch_name = f"kodo-{sanitized_label}-{suffix}"
    worktree_dir = Path(tempfile.mkdtemp(prefix=f"kodo-{sanitized_label}-"))
    # mkdtemp already created the dir; git worktree add wants a non-existing
    # target, so remove the empty dir first.
    try:
        worktree_dir.rmdir()
    except OSError:
        shutil.rmtree(worktree_dir, ignore_errors=True)
    subprocess.run(
        [_GIT, "worktree", "add", str(worktree_dir), "-b", branch_name, "HEAD"],
        cwd=project_dir,
        capture_output=True,
        check=True,
        timeout=_GIT_TIMEOUT,
    )
    return worktree_dir, branch_name


def remove_worktree(project_dir: Path, worktree_dir: Path, branch_name: str) -> None:
    """Remove a git worktree and its branch. Robust against partial failures."""
    from kodo import log

    if not project_dir.is_dir():
        raise RuntimeError(
            f"remove_worktree called with non-existent project_dir: {project_dir}"
        )
    if not branch_name:
        raise RuntimeError("remove_worktree called with empty branch_name")

    # 1. Remove worktree from git's index (--force allows dirty state)
    result = subprocess.run(
        [_GIT, "worktree", "remove", str(worktree_dir), "--force"],
        cwd=project_dir,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT,
    )
    if result.returncode != 0 and worktree_dir.exists():
        log.tprint(
            f"[worktree] git worktree remove failed ({result.stderr or result.stdout}), "
            "removing directory directly",
        )
        shutil.rmtree(worktree_dir, ignore_errors=True)

    # 2. Delete the branch (may already be gone if worktree remove succeeded)
    subprocess.run(
        [_GIT, "branch", "-D", branch_name],
        cwd=project_dir,
        capture_output=True,
        timeout=_GIT_TIMEOUT,
    )

    # 3. Ensure directory is gone
    if worktree_dir.exists():
        shutil.rmtree(worktree_dir, ignore_errors=True)

    # 4. Prune stale worktree metadata (rmtree leaves .git/worktrees/ entries)
    subprocess.run(
        [_GIT, "worktree", "prune"],
        cwd=project_dir,
        capture_output=True,
        timeout=_GIT_TIMEOUT,
    )


def _strip_pycache_from_index(repo_dir: Path) -> None:
    """Remove __pycache__ files from the git index (but not working tree).

    Agents frequently commit __pycache__/.pyc which causes merge conflicts
    when multiple parallel branches each commit different bytecode.
    """
    cached = subprocess.run(
        [_GIT, "ls-files", "--cached", "-z",
         "*/__pycache__/*", "*.pyc"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT,
    )
    pycache_files = [f for f in cached.stdout.split("\0") if f]
    if pycache_files:
        subprocess.run(
            [_GIT, "rm", "-r", "--cached", "--quiet", "--"] + pycache_files,
            cwd=repo_dir,
            capture_output=True,
            timeout=_GIT_TIMEOUT,
        )


def commit_worktree_changes(worktree_dir: Path, stage_name: str) -> bool:
    """Commit any uncommitted changes in a worktree.

    Returns True if a commit was made.  Used as a safety net before merging —
    catches changes the agent didn't commit during its run.
    """
    from kodo import log

    if not worktree_dir.is_dir():
        raise RuntimeError(
            f"commit_worktree_changes called with non-existent worktree: {worktree_dir}"
        )
    if not stage_name:
        raise RuntimeError("commit_worktree_changes called with empty stage_name")

    status = subprocess.run(
        [_GIT, "status", "--porcelain"],
        cwd=worktree_dir,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT,
    )
    if not status.stdout.strip():
        log.tprint(f"[persist] Stage '{stage_name}': no uncommitted changes")
        return False

    subprocess.run(
        [_GIT, "add", "-A"],
        cwd=worktree_dir,
        capture_output=True,
        check=True,
        timeout=_GIT_TIMEOUT,
    )
    _strip_pycache_from_index(worktree_dir)
    result = subprocess.run(
        [_GIT, "commit", "-m", f"kodo: parallel stage '{stage_name}' changes"],
        cwd=worktree_dir,
        capture_output=True,
        text=True,
        env={**os.environ, **_KODO_GIT_ENV},
        timeout=_GIT_TIMEOUT,
    )
    if result.returncode != 0:
        log.tprint(f"[persist] Stage '{stage_name}': commit failed: {result.stderr}")
        return False

    log.tprint(f"[persist] Stage '{stage_name}': committed worktree changes")
    return True


def _remove_worktree_keep_branch(project_dir: Path, worktree_dir: Path) -> None:
    """Remove a worktree directory without deleting its branch."""
    result = subprocess.run(
        [_GIT, "worktree", "remove", str(worktree_dir), "--force"],
        cwd=project_dir,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT,
    )
    if result.returncode != 0 and worktree_dir.exists():
        shutil.rmtree(worktree_dir, ignore_errors=True)
    subprocess.run(
        [_GIT, "worktree", "prune"],
        cwd=project_dir,
        capture_output=True,
        timeout=_GIT_TIMEOUT,
    )


def _resolve_conflicts_with_agent(
    project_dir: Path, branch_name: str, stage_name: str,
) -> bool:
    """Spin up a Claude Code agent to resolve merge conflicts.

    Returns True if conflicts were resolved and committed.
    """
    from kodo import log, make_session

    conflict_files = subprocess.run(
        [_GIT, "diff", "--name-only", "--diff-filter=U"],
        cwd=project_dir,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT,
    )
    files = conflict_files.stdout.strip()
    if not files:
        return False

    log.tprint(f"[persist] Resolving conflicts in: {files}")
    log.emit("persist_conflict_resolve_start", stage_name=stage_name, files=files)

    from kodo.models import CLAUDE_SONNET

    session = None
    try:
        session = make_session(
            backend="claude-code",
            model=CLAUDE_SONNET,
            system_prompt=(
                "You are resolving git merge conflicts. The merge is in progress. "
                "Conflicting files have <<<<<<< / ======= / >>>>>>> markers. "
                "Resolve each conflict by keeping BOTH sides' changes integrated "
                "correctly. Both branches implemented independent features that "
                "should coexist. After resolving, run `git add` on each file."
            ),
        )
        session.query(
            f"Resolve the merge conflicts in this project. The conflicting files are:\n"
            f"{files}\n\n"
            f"The branch being merged is '{branch_name}' (stage: {stage_name}). "
            f"Both the current branch and the incoming branch have valid changes "
            f"that should be combined. Read each conflicting file, resolve the "
            f"conflict markers, and `git add` the resolved files. "
            f"Do NOT commit — just resolve and stage.",
            project_dir=project_dir,
            max_turns=30,
        )
    except Exception as exc:
        log.emit("persist_conflict_resolve_crash", error=str(exc))
        log.tprint(f"[persist] Conflict resolver crashed: {exc}")
        return False
    finally:
        if session is not None:
            session.close()

    # Check if all conflicts are resolved
    remaining = subprocess.run(
        [_GIT, "diff", "--name-only", "--diff-filter=U"],
        cwd=project_dir,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT,
    )
    if remaining.stdout.strip():
        log.tprint("[persist] Agent failed to resolve all conflicts")
        log.emit(
            "persist_conflict_resolve_failed",
            stage_name=stage_name,
            remaining=remaining.stdout.strip(),
        )
        return False

    # Commit the merge
    commit = subprocess.run(
        [_GIT, "commit", "--no-edit"],
        cwd=project_dir,
        capture_output=True,
        text=True,
        env={**os.environ, **_KODO_GIT_ENV},
        timeout=_GIT_TIMEOUT,
    )
    if commit.returncode != 0:
        log.tprint(f"[persist] Merge commit failed: {commit.stderr}")
        return False

    log.tprint(f"[persist] Agent resolved conflicts for '{stage_name}'")
    log.emit("persist_conflict_resolve_ok", stage_name=stage_name)
    return True


def merge_worktree_branch(
    project_dir: Path, branch_name: str, stage_name: str,
) -> MergeResult:
    """Merge a worktree branch into the current branch at *project_dir*.

    Uses ``--no-ff`` to preserve branch history.  On conflict, spins up
    a Claude Code agent to resolve the conflicts.  Falls back to abort
    if the agent cannot resolve them.
    """
    from kodo import log

    # Pre-flight: abort early if the main repo has uncommitted changes.
    # Running destructive commands (checkout, clean) on a dirty worktree
    # would silently discard user work.
    preflight = subprocess.run(
        [_GIT, "status", "--porcelain"],
        cwd=project_dir,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT,
    )
    if preflight.stdout.strip():
        dirty_msg = (
            f"Stage '{stage_name}': refusing to merge — main repo has uncommitted "
            f"changes. Commit or stash them first.\n"
            f"Dirty files:\n{preflight.stdout.strip()[:500]}"
        )
        log.tprint(f"[persist] {dirty_msg}")
        return MergeResult(success=False, had_changes=False, error=dirty_msg)

    # Check if branch has any commits ahead of HEAD
    diff_check = subprocess.run(
        [_GIT, "log", f"HEAD..{branch_name}", "--oneline"],
        cwd=project_dir,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT,
    )
    if diff_check.returncode != 0:
        log.tprint(f"[persist] Stage '{stage_name}': branch '{branch_name}' not found")
        return MergeResult(
            success=False, had_changes=False, error=diff_check.stderr or "",
        )
    if not diff_check.stdout.strip():
        log.tprint(f"[persist] Stage '{stage_name}': no commits to merge")
        return MergeResult(success=True, had_changes=False)

    # Strip __pycache__ from the branch (agents commit bytecode that
    # causes binary conflicts across parallel branches).
    rev_parse = subprocess.run(
        [_GIT, "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=project_dir,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT,
    )
    if rev_parse.returncode != 0:
        return MergeResult(
            success=False, had_changes=False, error=rev_parse.stderr or "",
        )
    current_branch = rev_parse.stdout.strip()

    try:
        subprocess.run(
            [_GIT, "checkout", branch_name],
            cwd=project_dir,
            capture_output=True,
            text=True,
            check=True,
            timeout=_GIT_TIMEOUT,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        err = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or str(e))
        log.tprint(
            f"[persist] Stage '{stage_name}': checkout {branch_name} failed: {err}",
        )
        return MergeResult(success=False, had_changes=False, error=err)

    _strip_pycache_from_index(project_dir)
    status_before = subprocess.run(
        [_GIT, "status", "--porcelain"],
        cwd=project_dir,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT,
    )
    if status_before.stdout.strip():
        subprocess.run(
            [
                _GIT,
                "commit",
                "-m",
                "kodo: strip __pycache__ before merge",
            ],
            cwd=project_dir,
            capture_output=True,
            check=True,
            env={**os.environ, **_KODO_GIT_ENV},
            timeout=_GIT_TIMEOUT,
        )

    try:
        subprocess.run(
            [_GIT, "checkout", current_branch],
            cwd=project_dir,
            capture_output=True,
            text=True,
            check=True,
            timeout=_GIT_TIMEOUT,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        err = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or str(e))
        log.tprint(
            f"[persist] Stage '{stage_name}': checkout {current_branch} failed: {err}",
        )
        return MergeResult(success=False, had_changes=False, error=err)

    # Strip __pycache__ from current branch too (prior merge may have
    # brought in bytecode that would conflict with the next branch).
    _strip_pycache_from_index(project_dir)
    status_main = subprocess.run(
        [_GIT, "status", "--porcelain"],
        cwd=project_dir,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT,
    )
    if status_main.stdout.strip():
        subprocess.run(
            [
                _GIT,
                "commit",
                "-m",
                "kodo: strip __pycache__ from main",
            ],
            cwd=project_dir,
            capture_output=True,
            check=True,
            env={**os.environ, **_KODO_GIT_ENV},
            timeout=_GIT_TIMEOUT,
        )

    # Clean untracked files and dirty state that would block merge.
    # Skip if user has local changes — don't wipe their data.
    status_clean = subprocess.run(
        [_GIT, "status", "--porcelain"],
        cwd=project_dir,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT,
    )
    if status_clean.stdout.strip():
        log.tprint(
            f"[persist] Stage '{stage_name}': skipping checkout/clean — "
            "untracked or modified files would be lost",
        )
    else:
        co = subprocess.run(
            [_GIT, "checkout", "--", "."],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
        if co.returncode != 0:
            log.tprint(
                f"[persist] Stage '{stage_name}': checkout -- . failed: {co.stderr}",
            )
        cl = subprocess.run(
            [_GIT, "clean", "-fd"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
        if cl.returncode != 0:
            log.tprint(f"[persist] Stage '{stage_name}': clean -fd failed: {cl.stderr}")

    result = subprocess.run(
        [
            _GIT,
            "merge",
            branch_name,
            "--no-ff",
            "-m",
            f"Merge kodo parallel stage: {stage_name}",
        ],
        cwd=project_dir,
        capture_output=True,
        text=True,
        env={**os.environ, **_KODO_GIT_ENV},
        timeout=_GIT_TIMEOUT,
    )

    if result.returncode != 0:
        is_conflict = "CONFLICT" in (result.stdout + result.stderr)

        if is_conflict:
            log.tprint(
                f"[persist] Stage '{stage_name}': merge conflict, attempting agent resolution",
            )
            resolved = _resolve_conflicts_with_agent(
                project_dir, branch_name, stage_name,
            )
            if resolved:
                log.tprint(
                    f"[persist] Stage '{stage_name}': conflicts resolved by agent",
                )
                log.emit("persist_merge_ok", stage_name=stage_name, branch=branch_name)
                return MergeResult(success=True, had_changes=True)
            # Agent failed — abort the merge
            log.tprint(
                f"[persist] Stage '{stage_name}': agent could not resolve conflicts",
            )

        abort = subprocess.run(
            [_GIT, "merge", "--abort"],
            cwd=project_dir,
            capture_output=True,
            timeout=_GIT_TIMEOUT,
        )
        if abort.returncode != 0:
            log.tprint(
                f"[persist] Stage '{stage_name}': merge --abort failed: {abort.stderr}",
            )
        merge_output = result.stdout + result.stderr
        log.tprint(
            f"[persist] Stage '{stage_name}': "
            f"merge {'conflict' if is_conflict else 'failed'}",
        )
        log.emit(
            "persist_merge_failed",
            stage_name=stage_name,
            branch=branch_name,
            conflict=is_conflict,
            error=merge_output[:1000],
        )
        return MergeResult(
            success=False,
            had_changes=True,
            conflict=is_conflict,
            error=merge_output,
        )

    log.tprint(f"[persist] Stage '{stage_name}': merged successfully")
    log.emit("persist_merge_ok", stage_name=stage_name, branch=branch_name)
    return MergeResult(success=True, had_changes=True)


def _auto_commit(
    team: dict,
    project_dir: Path,
    summary: str,
) -> None:
    """Dispatch a worker to commit completed work after verification passes.

    Non-fatal: logs warnings on failure but never raises.
    """
    from kodo import log

    # Find a worker: prefer worker_fast, fall back to worker_smart, then any
    worker = (
        team.get("worker_fast")
        or team.get("worker_smart")
        or next((a for a in team.values()), None)
    )
    if worker is None:
        log.tprint("📝 [auto-commit] no worker available, skipping")
        log.emit("auto_commit_skip", reason="no_worker")
        return

    worker_name = next((n for n, a in team.items() if a is worker), "worker")

    directive = (
        "Review `git diff` and `git status`. Stage the relevant changed files "
        "and commit with a clear, concise message describing what was accomplished. "
        "Add Co-Authored-By: kodo <noreply@github.com>\n"
        "Do NOT push. Do NOT commit unrelated or generated files.\n\n"
        f"Summary of completed work:\n{summary}"
    )

    log.tprint(f"📝 [auto-commit] dispatching {worker_name} to commit...")
    log.emit("auto_commit_start", worker=worker_name)

    try:
        result = worker.run(
            directive,
            project_dir,
            new_conversation=True,
            agent_name=f"{worker_name}_auto_commit",
        )
        report = (result.text or "")[:2000]
        log.emit("auto_commit_done", worker=worker_name, report=report)
        log.tprint(f"📝 [auto-commit] {worker_name} finished")
    except Exception as exc:
        log.emit("auto_commit_error", worker=worker_name, error=str(exc))
        log.tprint(f"📝 [auto-commit] {worker_name} failed: {exc}")
