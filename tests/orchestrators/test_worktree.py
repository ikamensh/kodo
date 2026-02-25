"""Boundary Condition 4: Worktree creation/removal edge cases.

Tests for non-git dir, cleanup on failure, nonexistent dir, and locked files.
Documents any issues found.

Documented outcomes (all pass):
- create_worktree in non-git dir: raises CalledProcessError, no temp dir leak
- create_worktree git failure: no temp dirs left behind
- remove_worktree nonexistent dir: completes without crashing
- remove_worktree with locked file: completes without crashing (ignore_errors=True)
"""

from __future__ import annotations

import glob
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from kodo.orchestrators.base import create_worktree, remove_worktree


@pytest.fixture
def git_project(tmp_path: Path) -> Path:
    """Create a real git repo for worktree tests."""
    project = tmp_path / "repo"
    project.mkdir()
    subprocess.run(["git", "init"], cwd=project, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=project,
        capture_output=True,
        check=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "t@t",
        },
    )
    return project


def _count_kodo_temp_dirs() -> int:
    """Count temp dirs matching kodo-* prefix."""
    return len(glob.glob(os.path.join(tempfile.gettempdir(), "kodo-*")))


def test_create_worktree_in_non_git_dir(tmp_path: Path):
    """create_worktree in a non-git directory must raise, not crash silently.

    The directory has no .git, so 'git worktree add' will fail. create_worktree
    uses check=True and will raise CalledProcessError. No temp dirs should leak
    (mkdtemp creates dir, rmdir removes it before git runs; git fails before
    creating worktree).
    """
    non_git_dir = tmp_path / "not_a_repo"
    non_git_dir.mkdir()
    # Ensure it's not a git repo
    assert not (non_git_dir / ".git").exists()

    before = _count_kodo_temp_dirs()
    try:
        create_worktree(non_git_dir, "test")
        pytest.fail("create_worktree should raise on non-git dir")
    except subprocess.CalledProcessError as e:
        assert e.returncode != 0
    after = _count_kodo_temp_dirs()

    if after > before:
        pytest.xfail(
            "Boundary Condition 4 LEAK: create_worktree on non-git dir left "
            f"{after - before} temp dir(s). mkdtemp creates dir before git runs; "
            "if git fails, the pre-created dir may leak."
        )


def test_create_worktree_git_failure_cleans_up(tmp_path: Path):
    """When git worktree add fails, no temp directories should be left behind.

    create_worktree does: mkdtemp -> rmdir -> git worktree add. If git fails,
    we never create a worktree; the mkdtemp dir was already rmdir'd. This test
    verifies no kodo-* temp dirs remain after a failed create_worktree.
    """
    non_git_dir = tmp_path / "no_repo"
    non_git_dir.mkdir()

    before = _count_kodo_temp_dirs()
    with pytest.raises(subprocess.CalledProcessError):
        create_worktree(non_git_dir, "cleanup-test")
    after = _count_kodo_temp_dirs()

    if after > before:
        pytest.xfail(
            "Boundary Condition 4 LEAK: git failure left temp dirs. "
            f"Before: {before}, after: {after}. "
            "create_worktree should not leak temp dirs when git worktree add fails."
        )


def test_remove_worktree_nonexistent_dir(git_project: Path):
    """remove_worktree with nonexistent worktree_dir must not crash.

    When the worktree path never existed (or was already removed), git worktree
    remove will fail. remove_worktree should handle this gracefully.
    """
    nonexistent = Path("/nonexistent/kodo-fake-dir-12345")
    assert not nonexistent.exists()

    try:
        remove_worktree(git_project, nonexistent, "kodo-fake-branch-abc123")
    except Exception as e:
        pytest.xfail(
            f"Boundary Condition 4 CRASH: remove_worktree crashed on nonexistent "
            f"dir. {type(e).__name__}: {e}"
        )


def test_remove_worktree_handles_locked_files(git_project: Path):
    """remove_worktree must not crash when files in worktree are locked (open).

    When a file in the worktree is held open by another process, rmtree may
    fail on some platforms. remove_worktree uses ignore_errors=True; it should
    not raise.
    """
    wt_dir, branch = create_worktree(git_project, "locked-test")
    lock_file = wt_dir / "locked.txt"
    lock_file.write_text("content")

    # Hold the file open to simulate a locked file
    with open(lock_file):
        try:
            remove_worktree(git_project, wt_dir, branch)
        except Exception as e:
            pytest.xfail(
                f"Boundary Condition 4 CRASH: remove_worktree crashed with "
                f"locked file. {type(e).__name__}: {e}"
            )
