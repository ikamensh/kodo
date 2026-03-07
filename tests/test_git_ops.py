"""Tests for kodo/orchestrators/git_ops.py — Tier 1 & 2."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from kodo.orchestrators.git_ops import (
    MergeResult,
    _remove_worktree_keep_branch,
    commit_worktree_changes,
    create_worktree,
    merge_worktree_branch,
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


# ── Tier 2: Merge Operations ──────────────────────────────────────────


_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "t@t",
}


def _cleanup_branch(project_dir: Path, branch_name: str) -> None:
    """Helper to cleanup a branch after tests."""
    subprocess.run(
        ["git", "branch", "-D", branch_name],
        cwd=project_dir,
        capture_output=True,
    )


def test_merge_dirty_main_repo_refused(git_project: Path):
    """merge_worktree_branch refuses to merge if main repo has uncommitted changes."""
    # Create a worktree with a commit
    worktree_dir, branch_name = create_worktree(git_project, "test")
    try:
        # Make a change in the worktree and commit
        (worktree_dir / "wt.txt").write_text("worktree change")
        subprocess.run(
            ["git", "add", "-A"],
            cwd=worktree_dir,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "worktree commit"],
            cwd=worktree_dir,
            capture_output=True,
            check=True,
            env=_GIT_ENV,
        )

        # Make the main repo dirty
        (git_project / "dirty.txt").write_text("uncommitted change")

        # Attempt merge - should fail due to dirty repo
        result = merge_worktree_branch(git_project, branch_name, "test-stage")

        assert result.success is False
        assert result.had_changes is False
        assert "uncommitted changes" in result.error.lower()
        assert "dirty.txt" in result.error

    finally:
        # Cleanup dirty file
        (git_project / "dirty.txt").unlink(missing_ok=True)
        remove_worktree(git_project, worktree_dir, branch_name)


def test_merge_branch_not_found(git_project: Path):
    """merge_worktree_branch returns error when branch doesn't exist."""
    result = merge_worktree_branch(git_project, "nonexistent-branch", "test-stage")

    assert result.success is False
    assert result.had_changes is False
    assert result.error != ""


def test_merge_no_commits_to_merge(git_project: Path):
    """merge_worktree_branch succeeds with had_changes=False when branch has no new commits."""
    # Create a worktree but don't add any commits
    worktree_dir, branch_name = create_worktree(git_project, "test")
    try:
        # Merge without any changes
        result = merge_worktree_branch(git_project, branch_name, "test-stage")

        assert result.success is True
        assert result.had_changes is False
        assert result.conflict is False

    finally:
        remove_worktree(git_project, worktree_dir, branch_name)


def test_merge_happy_path_no_conflict(git_project: Path):
    """merge_worktree_branch successfully merges a divergent branch without conflicts."""
    # Create a worktree and add a commit
    worktree_dir, branch_name = create_worktree(git_project, "test")
    try:
        # Add a file in the worktree
        (worktree_dir / "feature.txt").write_text("new feature")
        subprocess.run(
            ["git", "add", "-A"],
            cwd=worktree_dir,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "add feature"],
            cwd=worktree_dir,
            capture_output=True,
            check=True,
            env=_GIT_ENV,
        )

        # Remove worktree before merge (branch must not be checked out)
        # Use _remove_worktree_keep_branch to keep the branch for merging
        _remove_worktree_keep_branch(git_project, worktree_dir)

        # Merge the branch
        result = merge_worktree_branch(git_project, branch_name, "test-stage")

        assert result.success is True
        assert result.had_changes is True
        assert result.conflict is False
        assert result.error == ""

        # Verify the file exists in main repo
        assert (git_project / "feature.txt").exists()
        assert (git_project / "feature.txt").read_text() == "new feature"

        # Verify merge commit was created
        log_result = subprocess.run(
            ["git", "log", "--oneline", "-1"],
            cwd=git_project,
            capture_output=True,
            text=True,
        )
        assert "Merge kodo parallel stage: test-stage" in log_result.stdout

    finally:
        _cleanup_branch(git_project, branch_name)


def test_merge_strips_pycache_from_both_branches(git_project: Path):
    """merge_worktree_branch removes __pycache__ files from both branches before merging."""
    # Create a worktree and add __pycache__ files
    worktree_dir, branch_name = create_worktree(git_project, "test")
    try:
        # Add __pycache__ in worktree
        pycache_dir = worktree_dir / "module" / "__pycache__"
        pycache_dir.mkdir(parents=True)
        (pycache_dir / "test.cpython-313.pyc").write_bytes(b"\x00\x01\x02")
        (worktree_dir / "feature.txt").write_text("feature")

        subprocess.run(
            ["git", "add", "-A"],
            cwd=worktree_dir,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "add feature with pycache"],
            cwd=worktree_dir,
            capture_output=True,
            check=True,
            env=_GIT_ENV,
        )

        # Add __pycache__ in main repo
        main_pycache = git_project / "main_module" / "__pycache__"
        main_pycache.mkdir(parents=True)
        (main_pycache / "main.cpython-313.pyc").write_bytes(b"\x00\x01\x02")
        subprocess.run(
            ["git", "add", "-A"],
            cwd=git_project,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "add main pycache"],
            cwd=git_project,
            capture_output=True,
            check=True,
            env=_GIT_ENV,
        )

        # Remove worktree before merge (branch must not be checked out)
        _remove_worktree_keep_branch(git_project, worktree_dir)

        # Merge - should strip pycache from both branches
        result = merge_worktree_branch(git_project, branch_name, "test-stage")

        assert result.success is True
        assert result.had_changes is True

        # Verify __pycache__ files are not in the git index
        ls_files = subprocess.run(
            ["git", "ls-files", "*/__pycache__/*"],
            cwd=git_project,
            capture_output=True,
            text=True,
        )
        assert ls_files.stdout.strip() == ""

        # Verify feature.txt was merged
        assert (git_project / "feature.txt").exists()

    finally:
        _cleanup_branch(git_project, branch_name)


def test_merge_conflict_agent_resolves(git_project: Path):
    """merge_worktree_branch uses agent to resolve conflicts successfully."""
    # Create a base file
    (git_project / "shared.txt").write_text("original content\n")
    subprocess.run(
        ["git", "add", "-A"],
        cwd=git_project,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "add shared file"],
        cwd=git_project,
        capture_output=True,
        check=True,
        env=_GIT_ENV,
    )

    # Create worktree and modify the same file
    worktree_dir, branch_name = create_worktree(git_project, "test")
    try:
        (worktree_dir / "shared.txt").write_text("worktree version\n")
        subprocess.run(
            ["git", "add", "-A"],
            cwd=worktree_dir,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "modify shared in worktree"],
            cwd=worktree_dir,
            capture_output=True,
            check=True,
            env=_GIT_ENV,
        )

        # Modify the same file in main
        (git_project / "shared.txt").write_text("main version\n")
        subprocess.run(
            ["git", "add", "-A"],
            cwd=git_project,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "modify shared in main"],
            cwd=git_project,
            capture_output=True,
            check=True,
            env=_GIT_ENV,
        )

        # Remove worktree before merge (branch must not be checked out)
        _remove_worktree_keep_branch(git_project, worktree_dir)

        # Mock _resolve_conflicts_with_agent to return True (resolved)
        with mock.patch(
            "kodo.orchestrators.git_ops._resolve_conflicts_with_agent",
            return_value=True,
        ):
            result = merge_worktree_branch(git_project, branch_name, "test-stage")

        assert result.success is True
        assert result.had_changes is True
        assert result.conflict is False  # Successfully resolved
        assert result.error == ""

    finally:
        _cleanup_branch(git_project, branch_name)


def test_merge_conflict_agent_fails(git_project: Path):
    """merge_worktree_branch aborts merge when agent fails to resolve conflicts."""
    # Create a base file
    (git_project / "shared.txt").write_text("original content\n")
    subprocess.run(
        ["git", "add", "-A"],
        cwd=git_project,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "add shared file"],
        cwd=git_project,
        capture_output=True,
        check=True,
        env=_GIT_ENV,
    )

    # Create worktree and modify the same file
    worktree_dir, branch_name = create_worktree(git_project, "test")
    try:
        (worktree_dir / "shared.txt").write_text("worktree version\n")
        subprocess.run(
            ["git", "add", "-A"],
            cwd=worktree_dir,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "modify shared in worktree"],
            cwd=worktree_dir,
            capture_output=True,
            check=True,
            env=_GIT_ENV,
        )

        # Modify the same file in main
        (git_project / "shared.txt").write_text("main version\n")
        subprocess.run(
            ["git", "add", "-A"],
            cwd=git_project,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "modify shared in main"],
            cwd=git_project,
            capture_output=True,
            check=True,
            env=_GIT_ENV,
        )

        # Remove worktree before merge (branch must not be checked out)
        _remove_worktree_keep_branch(git_project, worktree_dir)

        # Mock _resolve_conflicts_with_agent to return False (failed)
        with mock.patch(
            "kodo.orchestrators.git_ops._resolve_conflicts_with_agent",
            return_value=False,
        ):
            result = merge_worktree_branch(git_project, branch_name, "test-stage")

        assert result.success is False
        assert result.had_changes is True
        assert result.conflict is True
        assert "CONFLICT" in result.error

        # Verify merge was aborted - no merge commit
        log_result = subprocess.run(
            ["git", "log", "--oneline", "-1"],
            cwd=git_project,
            capture_output=True,
            text=True,
        )
        assert "Merge kodo parallel stage" not in log_result.stdout
        assert "modify shared in main" in log_result.stdout

    finally:
        _cleanup_branch(git_project, branch_name)


def test_merge_non_conflict_failure(git_project: Path):
    """merge_worktree_branch handles non-conflict merge failures."""
    # Create a worktree with a commit
    worktree_dir, branch_name = create_worktree(git_project, "test")
    try:
        (worktree_dir / "feature.txt").write_text("feature")
        subprocess.run(
            ["git", "add", "-A"],
            cwd=worktree_dir,
            capture_output=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "add feature"],
            cwd=worktree_dir,
            capture_output=True,
            check=True,
            env=_GIT_ENV,
        )

        # Remove worktree before merge (branch must not be checked out)
        _remove_worktree_keep_branch(git_project, worktree_dir)

        # Mock subprocess.run to simulate merge failure without conflict
        original_run = subprocess.run

        def mock_run(cmd, *args, **kwargs):
            # Let most git commands through normally
            if cmd[:2] == ["git", "merge"]:
                # Simulate a non-conflict failure (e.g., hook rejection)
                class FakeResult:
                    returncode = 1
                    stdout = "merge failed: hook rejected\n"
                    stderr = "error: merge hook failed\n"
                return FakeResult()
            return original_run(cmd, *args, **kwargs)

        with mock.patch("subprocess.run", side_effect=mock_run):
            result = merge_worktree_branch(git_project, branch_name, "test-stage")

        assert result.success is False
        assert result.had_changes is True
        assert result.conflict is False  # No CONFLICT in output
        assert "hook" in result.error.lower()

    finally:
        _cleanup_branch(git_project, branch_name)
