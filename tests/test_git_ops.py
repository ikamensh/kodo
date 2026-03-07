"""Tests for kodo/orchestrators/git_ops.py — Tier 1: Core functionality."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from kodo.orchestrators.git_ops import (
    commit_worktree_changes,
    create_worktree,
    remove_worktree,
)


# ── Tier 1: Core Functionality ────────────────────────────────────────


def test_create_worktree_sanitizes_labels(git_project: Path):
    """Special characters in label are replaced with underscores."""
    label = "feat/my@feature:test*branch"
    worktree_dir, branch_name = create_worktree(git_project, label)

    # Branch name should sanitize special chars: /, @, :, *, etc.
    # Expected: "kodo-feat_my_feature_test_branch-<8-char-suffix>"
    assert "feat_my_feature_test_branch" in branch_name
    assert "/" not in branch_name
    assert "@" not in branch_name
    assert ":" not in branch_name
    assert "*" not in branch_name

    # Verify worktree was created
    assert worktree_dir.exists()
    assert (worktree_dir / ".git").exists()

    # Verify branch exists
    result = subprocess.run(
        ["git", "branch", "--list", branch_name],
        cwd=git_project,
        capture_output=True,
        text=True,
    )
    assert branch_name in result.stdout

    # Cleanup
    remove_worktree(git_project, worktree_dir, branch_name)


def test_create_worktree_happy_path(git_project: Path):
    """create_worktree creates a worktree and branch successfully."""
    label = "teststage"
    worktree_dir, branch_name = create_worktree(git_project, label)

    # Verify return values
    assert worktree_dir.is_dir()
    assert branch_name.startswith("kodo-teststage-")
    assert len(branch_name.split("-")) == 3  # kodo-teststage-<uuid>

    # Verify worktree directory structure
    assert (worktree_dir / ".git").exists()

    # Verify branch exists in git
    result = subprocess.run(
        ["git", "branch", "--list", branch_name],
        cwd=git_project,
        capture_output=True,
        text=True,
    )
    assert branch_name in result.stdout

    # Verify worktree is registered
    wt_list = subprocess.run(
        ["git", "worktree", "list"],
        cwd=git_project,
        capture_output=True,
        text=True,
    )
    assert str(worktree_dir) in wt_list.stdout

    # Cleanup
    remove_worktree(git_project, worktree_dir, branch_name)


def test_remove_worktree_missing_project_dir(tmp_path: Path):
    """remove_worktree raises RuntimeError if project_dir doesn't exist."""
    non_existent = tmp_path / "no-such-dir"
    fake_worktree = tmp_path / "fake-worktree"
    fake_worktree.mkdir()

    with pytest.raises(RuntimeError, match="non-existent project_dir"):
        remove_worktree(non_existent, fake_worktree, "kodo-test-branch")


def test_remove_worktree_empty_branch_name(git_project: Path, tmp_path: Path):
    """remove_worktree raises RuntimeError if branch_name is empty."""
    fake_worktree = tmp_path / "fake-worktree"
    fake_worktree.mkdir()

    with pytest.raises(RuntimeError, match="empty branch_name"):
        remove_worktree(git_project, fake_worktree, "")


def test_commit_worktree_missing_dir(tmp_path: Path):
    """commit_worktree_changes raises RuntimeError if worktree doesn't exist."""
    non_existent = tmp_path / "no-such-worktree"

    with pytest.raises(RuntimeError, match="non-existent worktree"):
        commit_worktree_changes(non_existent, "test-stage")


def test_commit_worktree_empty_stage_name(git_project: Path):
    """commit_worktree_changes raises RuntimeError if stage_name is empty."""
    label = "test-stage"
    worktree_dir, branch_name = create_worktree(git_project, label)

    try:
        with pytest.raises(RuntimeError, match="empty stage_name"):
            commit_worktree_changes(worktree_dir, "")
    finally:
        remove_worktree(git_project, worktree_dir, branch_name)


def test_commit_worktree_no_changes(git_project: Path):
    """commit_worktree_changes returns False when no changes exist."""
    label = "test-stage"
    worktree_dir, branch_name = create_worktree(git_project, label)

    try:
        # No changes made to the worktree
        result = commit_worktree_changes(worktree_dir, "test-stage")
        assert result is False
    finally:
        remove_worktree(git_project, worktree_dir, branch_name)


def test_commit_worktree_with_changes(git_project: Path):
    """commit_worktree_changes returns True and creates a commit when changes exist."""
    label = "test-stage"
    worktree_dir, branch_name = create_worktree(git_project, label)

    try:
        # Make changes in the worktree
        test_file = worktree_dir / "test.txt"
        test_file.write_text("Test content")

        # Commit the changes
        result = commit_worktree_changes(worktree_dir, "test-stage")
        assert result is True

        # Verify commit was created
        log_result = subprocess.run(
            ["git", "log", "--oneline", "-1"],
            cwd=worktree_dir,
            capture_output=True,
            text=True,
        )
        assert "kodo: parallel stage 'test-stage' changes" in log_result.stdout

        # Verify the file is committed (no longer in git status)
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=worktree_dir,
            capture_output=True,
            text=True,
        )
        assert status.stdout.strip() == ""

        # Verify commit author is kodo
        show_result = subprocess.run(
            ["git", "show", "--format=%an <%ae>", "-s"],
            cwd=worktree_dir,
            capture_output=True,
            text=True,
        )
        assert "kodo <noreply@github.com>" in show_result.stdout

    finally:
        remove_worktree(git_project, worktree_dir, branch_name)
