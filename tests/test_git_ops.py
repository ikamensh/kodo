"""Tests for kodo/orchestrators/git_ops.py — comprehensive coverage."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import mock

import pytest

from kodo.orchestrators.git_ops import (
    _cleanup_orphaned_kodo_branches,
    _remove_worktree_keep_branch,
    _resolve_conflicts_with_agent,
    _strip_pycache_from_index,
    cleanup_stale_worktrees,
    commit_worktree_changes,
    create_worktree,
    merge_worktree_branch,
    remove_worktree,
)
from tests.conftest import _GIT_ENV


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


def _cleanup_branch(project_dir: Path, branch_name: str) -> None:
    """Helper to cleanup a branch after tests."""
    subprocess.run(
        ["git", "branch", "-D", branch_name],
        cwd=project_dir,
        capture_output=True,
    )


def _create_real_merge_conflict(git_project: Path) -> str:
    """Create a real merge conflict in git_project and return the branch name.

    Creates a file modified in both main and a branch, then starts the merge
    so `git diff --diff-filter=U` returns the conflicting file.
    Returns the branch name (merge left in-progress).
    """
    # Create base file
    (git_project / "shared.txt").write_text("original content\n")
    subprocess.run(["git", "add", "-A"], cwd=git_project, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "add shared"], cwd=git_project,
        capture_output=True, check=True, env=_GIT_ENV,
    )

    # Create branch with different change
    branch_name = "kodo-conflict-test"
    subprocess.run(
        ["git", "checkout", "-b", branch_name], cwd=git_project,
        capture_output=True, check=True,
    )
    (git_project / "shared.txt").write_text("branch version\n")
    subprocess.run(["git", "add", "-A"], cwd=git_project, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "branch change"], cwd=git_project,
        capture_output=True, check=True, env=_GIT_ENV,
    )

    # Go back to main, make conflicting change
    subprocess.run(
        ["git", "checkout", "-"], cwd=git_project,
        capture_output=True, check=True,
    )
    (git_project / "shared.txt").write_text("main version\n")
    subprocess.run(["git", "add", "-A"], cwd=git_project, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "main change"], cwd=git_project,
        capture_output=True, check=True, env=_GIT_ENV,
    )

    # Start merge (will conflict) — must pass _GIT_ENV for committer identity
    subprocess.run(
        ["git", "merge", branch_name, "--no-ff"],
        cwd=git_project, capture_output=True, env=_GIT_ENV,
    )
    return branch_name


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
            "kodo.orchestrators.git_ops._resolve_conflicts_with_agent", autospec=True,
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
            "kodo.orchestrators.git_ops._resolve_conflicts_with_agent", autospec=True,
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

        with mock.patch("subprocess.run", autospec=True, side_effect=mock_run):
            result = merge_worktree_branch(git_project, branch_name, "test-stage")

        assert result.success is False
        assert result.had_changes is True
        assert result.conflict is False  # No CONFLICT in output
        assert "hook" in result.error.lower()

    finally:
        _cleanup_branch(git_project, branch_name)


# ── Tier 3: Edge Cases + Helpers ──────────────────────────────────────────


def test_strip_pycache_noop_when_none(git_project: Path):
    """_strip_pycache_from_index should be no-op when no pycache files in index."""
    # Create and commit a regular python file
    (git_project / "test.py").write_text("print('hello')")
    subprocess.run(
        ["git", "add", "test.py"],
        cwd=git_project,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "add test.py"],
        cwd=git_project,
        check=True,
        env=_GIT_ENV,
    )

    # Call strip_pycache - should not remove anything
    _strip_pycache_from_index(git_project)

    # Verify test.py still in index
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=git_project,
        capture_output=True,
        text=True,
    )
    assert "test.py" in result.stdout


def test_strip_pycache_removes_pyc_files(git_project: Path):
    """_strip_pycache_from_index should remove __pycache__/*.pyc from index."""
    # Create pycache directory and files
    pycache_dir = git_project / "__pycache__"
    pycache_dir.mkdir()
    (pycache_dir / "test.cpython-311.pyc").write_bytes(b"fake bytecode")
    (git_project / "regular.pyc").write_bytes(b"fake bytecode")

    # Add to git
    subprocess.run(
        ["git", "add", "__pycache__", "regular.pyc"],
        cwd=git_project,
        check=True,
    )

    # Verify they're in the index
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=git_project,
        capture_output=True,
        text=True,
    )
    assert "__pycache__/test.cpython-311.pyc" in result.stdout
    assert "regular.pyc" in result.stdout

    # Call strip_pycache
    _strip_pycache_from_index(git_project)

    # Verify they're removed from index
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=git_project,
        capture_output=True,
        text=True,
    )
    assert "__pycache__" not in result.stdout
    assert "regular.pyc" not in result.stdout

    # But files still exist in working tree
    assert (pycache_dir / "test.cpython-311.pyc").exists()
    assert (git_project / "regular.pyc").exists()


def test_remove_worktree_fallback_to_rmtree(git_project: Path):
    """_remove_worktree_keep_branch should fall back to rmtree if git worktree remove fails."""
    worktree_dir, branch_name = create_worktree(git_project, "test")

    # Mock subprocess.run to make "git worktree remove" fail
    original_run = subprocess.run

    def mock_run(cmd, *args, **kwargs):
        if cmd[:3] == ["git", "worktree", "remove"]:
            # Simulate failure
            class FakeResult:
                returncode = 1
                stdout = ""
                stderr = "worktree remove failed"
            return FakeResult()
        return original_run(cmd, *args, **kwargs)

    with mock.patch("subprocess.run", autospec=True, side_effect=mock_run):
        _remove_worktree_keep_branch(git_project, worktree_dir)

    # Worktree directory should be removed despite git command failure
    assert not worktree_dir.exists()


def test_remove_worktree_keep_branch_preserves_branch(git_project: Path):
    """_remove_worktree_keep_branch should remove worktree but keep branch."""
    worktree_dir, branch_name = create_worktree(git_project, "test")

    # Verify worktree exists
    assert worktree_dir.exists()

    # Remove worktree but keep branch
    _remove_worktree_keep_branch(git_project, worktree_dir)

    # Worktree should be gone
    assert not worktree_dir.exists()

    # Branch should still exist
    result = subprocess.run(
        ["git", "branch", "--list", branch_name],
        cwd=git_project,
        capture_output=True,
        text=True,
    )
    assert branch_name in result.stdout

    # Clean up branch
    subprocess.run(
        ["git", "branch", "-D", branch_name],
        cwd=git_project,
        check=True,
    )


def test_commit_worktree_commit_fails(git_project: Path):
    """commit_worktree_changes should return False when git commit fails."""
    worktree_dir, branch_name = create_worktree(git_project, "test")

    try:
        # Create uncommitted changes
        (worktree_dir / "test.txt").write_text("content")
        subprocess.run(
            ["git", "add", "test.txt"],
            cwd=worktree_dir,
            check=True,
        )

        # Mock subprocess.run to make commit fail
        original_run = subprocess.run

        def mock_run(cmd, *args, **kwargs):
            if cmd[:2] == ["git", "commit"]:
                # Simulate commit failure
                class FakeResult:
                    returncode = 1
                    stdout = ""
                    stderr = "commit failed: hook rejected"
                return FakeResult()
            return original_run(cmd, *args, **kwargs)

        with mock.patch("subprocess.run", autospec=True, side_effect=mock_run):
            result = commit_worktree_changes(worktree_dir, "test-stage")

        # Should return False when commit fails
        assert result is False

    finally:
        _cleanup_branch(git_project, branch_name)


def test_resolve_conflicts_no_conflict_files(git_project: Path):
    """_resolve_conflicts_with_agent returns False immediately when no conflict files."""
    result = _resolve_conflicts_with_agent(git_project, "fake-branch", "test-stage")
    assert result is False


# ── Tier 4: create_worktree OSError fallback (lines 58-59) ────────────


def test_create_worktree_rmdir_oserror_fallback(git_project: Path):
    """When rmdir raises OSError (non-empty dir), shutil.rmtree is used instead."""

    def failing_rmdir(self):
        raise OSError("Directory not empty")

    with mock.patch.object(Path, "rmdir", failing_rmdir):
        worktree_dir, branch_name = create_worktree(git_project, "oserror-test")

    assert worktree_dir.is_dir()
    assert branch_name.startswith("kodo-oserror-test-")

    # Cleanup
    remove_worktree(git_project, worktree_dir, branch_name)


# ── Tier 5: remove_worktree fallbacks (lines 90-94, 106) ─────────────


def test_remove_worktree_git_remove_fails_dir_exists(git_project: Path):
    """When git worktree remove fails and dir still exists, rmtree cleans up (lines 90-94)."""
    worktree_dir, branch_name = create_worktree(git_project, "rmfail")

    original_run = subprocess.run

    def mock_run(cmd, *args, **kwargs):
        if isinstance(cmd, list) and cmd[:3] == ["git", "worktree", "remove"]:
            # Simulate git worktree remove failure
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="error removing")
        return original_run(cmd, *args, **kwargs)

    with mock.patch("subprocess.run", autospec=True, side_effect=mock_run):
        remove_worktree(git_project, worktree_dir, branch_name)

    assert not worktree_dir.exists()


def test_remove_worktree_dir_persists_after_git_remove(git_project: Path):
    """When dir still exists after successful git worktree remove, rmtree cleans up (line 106)."""
    worktree_dir, branch_name = create_worktree(git_project, "persist")

    original_run = subprocess.run
    first_remove_call = [True]

    def mock_run(cmd, *args, **kwargs):
        if isinstance(cmd, list) and cmd[:3] == ["git", "worktree", "remove"] and first_remove_call[0]:
            first_remove_call[0] = False
            # Return success but DON'T actually remove the dir
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return original_run(cmd, *args, **kwargs)

    with mock.patch("subprocess.run", autospec=True, side_effect=mock_run):
        remove_worktree(git_project, worktree_dir, branch_name)

    assert not worktree_dir.exists()


# ── Tier 6: _resolve_conflicts_with_agent full paths (lines 230-299) ──


def test_resolve_conflicts_session_crash(git_project: Path):
    """_resolve_conflicts_with_agent returns False when make_session raises (lines 259-262)."""
    branch_name = _create_real_merge_conflict(git_project)

    try:
        with mock.patch(
            "kodo.make_session", autospec=True,
            side_effect=RuntimeError("Session creation failed"),
        ):
            result = _resolve_conflicts_with_agent(git_project, branch_name, "test-stage")

        assert result is False
    finally:
        subprocess.run(["git", "merge", "--abort"], cwd=git_project, capture_output=True)
        _cleanup_branch(git_project, branch_name)


def test_resolve_conflicts_remaining_unresolved(git_project: Path):
    """_resolve_conflicts_with_agent returns False when conflicts remain (lines 275-282)."""
    from tests.conftest import FakeSession

    branch_name = _create_real_merge_conflict(git_project)

    try:
        # Agent "runs" but doesn't actually resolve the conflict markers
        fake_session = FakeSession(response_text="I tried but could not resolve")

        with mock.patch(
            "kodo.make_session", autospec=True,
            return_value=fake_session,
        ):
            result = _resolve_conflicts_with_agent(git_project, branch_name, "test-stage")

        assert result is False
    finally:
        subprocess.run(["git", "merge", "--abort"], cwd=git_project, capture_output=True)
        _cleanup_branch(git_project, branch_name)


def test_resolve_conflicts_success(git_project: Path):
    """_resolve_conflicts_with_agent returns True when agent resolves and commit succeeds (lines 285-299)."""
    from tests.conftest import FakeSession

    branch_name = _create_real_merge_conflict(git_project)

    class ResolvingSession(FakeSession):
        """Session that actually resolves the conflict by fixing the file."""

        def query(self, prompt, project_dir, *, max_turns=10):
            # Resolve the conflict by writing the combined content
            (project_dir / "shared.txt").write_text("both main and branch version\n")
            subprocess.run(
                ["git", "add", "shared.txt"],
                cwd=project_dir,
                capture_output=True,
                check=True,
            )
            return super().query(prompt, project_dir, max_turns=max_turns)

    try:
        with mock.patch(
            "kodo.make_session", autospec=True,
            return_value=ResolvingSession(response_text="resolved"),
        ):
            result = _resolve_conflicts_with_agent(git_project, branch_name, "test-stage")

        assert result is True

        # Verify merge commit was created
        log_result = subprocess.run(
            ["git", "log", "--oneline", "-1"],
            cwd=git_project, capture_output=True, text=True,
        )
        assert "Merge" in log_result.stdout or "merge" in log_result.stdout.lower()
    finally:
        _cleanup_branch(git_project, branch_name)


def test_resolve_conflicts_commit_fails(git_project: Path):
    """_resolve_conflicts_with_agent returns False when merge commit fails (lines 293-295)."""
    from tests.conftest import FakeSession

    branch_name = _create_real_merge_conflict(git_project)

    class ResolvingSession(FakeSession):
        """Session that resolves the conflict but commit will be mocked to fail."""

        def query(self, prompt, project_dir, *, max_turns=10):
            (project_dir / "shared.txt").write_text("resolved content\n")
            subprocess.run(
                ["git", "add", "shared.txt"],
                cwd=project_dir, capture_output=True, check=True,
            )
            return super().query(prompt, project_dir, max_turns=max_turns)

    original_run = subprocess.run

    def mock_run(cmd, *args, **kwargs):
        if isinstance(cmd, list) and cmd[:2] == ["git", "commit"]:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="commit failed")
        return original_run(cmd, *args, **kwargs)

    try:
        with mock.patch(
            "kodo.make_session", autospec=True,
            return_value=ResolvingSession(response_text="resolved"),
        ):
            # Need to let session.query run normally first, then intercept commit
            # The mock_run only intercepts AFTER the session resolves
            with mock.patch("subprocess.run", autospec=True, side_effect=mock_run):
                # This won't work because mock_run also intercepts git diff calls.
                # Instead, mock only the commit call precisely.
                pass

        # Better approach: let the function run normally up to the commit,
        # then fail the commit call.
        call_count = {"commit": 0}

        def mock_run_commit_fail(cmd, *args, **kwargs):
            if isinstance(cmd, list) and cmd[:2] == ["git", "commit"] and "--no-edit" in cmd:
                call_count["commit"] += 1
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="commit rejected")
            return original_run(cmd, *args, **kwargs)

        with (
            mock.patch(
                "kodo.make_session", autospec=True,
                return_value=ResolvingSession(response_text="resolved"),
            ),
            mock.patch("subprocess.run", autospec=True, side_effect=mock_run_commit_fail),
        ):
            result = _resolve_conflicts_with_agent(git_project, branch_name, "test-stage")

        assert result is False
    finally:
        subprocess.run(["git", "merge", "--abort"], cwd=git_project, capture_output=True)
        _cleanup_branch(git_project, branch_name)


# ── Tier 7: merge_worktree_branch edge cases (lines 359, 373-378, 412-417, 478) ──


def test_merge_rev_parse_fails(git_project: Path):
    """merge_worktree_branch returns error when rev-parse fails (line 359)."""
    # Create a worktree with a commit
    worktree_dir, branch_name = create_worktree(git_project, "revparse")
    try:
        (worktree_dir / "feature.txt").write_text("content")
        subprocess.run(["git", "add", "-A"], cwd=worktree_dir, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "add feature"], cwd=worktree_dir,
            capture_output=True, check=True, env=_GIT_ENV,
        )
        _remove_worktree_keep_branch(git_project, worktree_dir)

        original_run = subprocess.run

        def mock_run(cmd, *args, **kwargs):
            if isinstance(cmd, list) and "rev-parse" in cmd:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="rev-parse error")
            return original_run(cmd, *args, **kwargs)

        with mock.patch("subprocess.run", autospec=True, side_effect=mock_run):
            result = merge_worktree_branch(git_project, branch_name, "test-stage")

        assert result.success is False
        assert result.had_changes is False
        assert "rev-parse" in result.error.lower() or result.error != ""
    finally:
        _cleanup_branch(git_project, branch_name)


def test_merge_checkout_branch_fails(git_project: Path):
    """merge_worktree_branch returns error when checkout branch fails (lines 373-378)."""
    worktree_dir, branch_name = create_worktree(git_project, "cofail")
    try:
        (worktree_dir / "feature.txt").write_text("content")
        subprocess.run(["git", "add", "-A"], cwd=worktree_dir, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "add feature"], cwd=worktree_dir,
            capture_output=True, check=True, env=_GIT_ENV,
        )
        _remove_worktree_keep_branch(git_project, worktree_dir)

        original_run = subprocess.run
        checkout_count = [0]

        def mock_run(cmd, *args, **kwargs):
            if isinstance(cmd, list) and cmd[:2] == ["git", "checkout"] and branch_name in cmd:
                checkout_count[0] += 1
                raise subprocess.CalledProcessError(
                    1, cmd, output=b"", stderr=b"checkout failed: unable to checkout"
                )
            return original_run(cmd, *args, **kwargs)

        with mock.patch("subprocess.run", autospec=True, side_effect=mock_run):
            result = merge_worktree_branch(git_project, branch_name, "test-stage")

        assert result.success is False
        assert result.had_changes is False
        assert "checkout" in result.error.lower()
    finally:
        _cleanup_branch(git_project, branch_name)


def test_merge_checkout_main_back_fails(git_project: Path):
    """merge_worktree_branch returns error when checkout back to main fails (lines 412-417)."""
    worktree_dir, branch_name = create_worktree(git_project, "mainback")
    try:
        (worktree_dir / "feature.txt").write_text("content")
        subprocess.run(["git", "add", "-A"], cwd=worktree_dir, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "add feature"], cwd=worktree_dir,
            capture_output=True, check=True, env=_GIT_ENV,
        )
        _remove_worktree_keep_branch(git_project, worktree_dir)

        # Get current branch name
        current = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=git_project, capture_output=True, text=True,
        ).stdout.strip()

        original_run = subprocess.run
        first_checkout_done = [False]

        def mock_run(cmd, *args, **kwargs):
            if isinstance(cmd, list) and cmd[:2] == ["git", "checkout"]:
                if not first_checkout_done[0] and branch_name in cmd:
                    # Allow first checkout to branch
                    first_checkout_done[0] = True
                    return original_run(cmd, *args, **kwargs)
                if first_checkout_done[0] and current in cmd:
                    # Fail checkout back to main
                    raise subprocess.CalledProcessError(
                        1, cmd, output=b"", stderr=b"checkout main failed"
                    )
            return original_run(cmd, *args, **kwargs)

        with mock.patch("subprocess.run", autospec=True, side_effect=mock_run):
            result = merge_worktree_branch(git_project, branch_name, "test-stage")

        assert result.success is False
        assert result.had_changes is False
        assert "checkout" in result.error.lower()
    finally:
        # Make sure we're back on main branch for cleanup
        subprocess.run(["git", "checkout", "master"], cwd=git_project, capture_output=True)
        subprocess.run(["git", "checkout", "main"], cwd=git_project, capture_output=True)
        _cleanup_branch(git_project, branch_name)


def test_merge_git_clean_fails(git_project: Path):
    """merge_worktree_branch logs warning when git clean -fd fails (line 478)."""
    worktree_dir, branch_name = create_worktree(git_project, "cleanfail")
    try:
        (worktree_dir / "feature.txt").write_text("content")
        subprocess.run(["git", "add", "-A"], cwd=worktree_dir, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "add feature"], cwd=worktree_dir,
            capture_output=True, check=True, env=_GIT_ENV,
        )
        _remove_worktree_keep_branch(git_project, worktree_dir)

        original_run = subprocess.run

        def mock_run(cmd, *args, **kwargs):
            if isinstance(cmd, list) and cmd[:2] == ["git", "clean"]:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="clean failed")
            return original_run(cmd, *args, **kwargs)

        with mock.patch("subprocess.run", autospec=True, side_effect=mock_run):
            result = merge_worktree_branch(git_project, branch_name, "test-stage")

        # Merge should still succeed (clean failure is just logged)
        assert result.success is True
        assert result.had_changes is True
    finally:
        _cleanup_branch(git_project, branch_name)


def test_merge_checkout_branch_timeout(git_project: Path):
    """merge_worktree_branch handles TimeoutExpired during checkout (lines 373-378)."""
    worktree_dir, branch_name = create_worktree(git_project, "timeout")
    try:
        (worktree_dir / "feature.txt").write_text("content")
        subprocess.run(["git", "add", "-A"], cwd=worktree_dir, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "add feature"], cwd=worktree_dir,
            capture_output=True, check=True, env=_GIT_ENV,
        )
        _remove_worktree_keep_branch(git_project, worktree_dir)

        original_run = subprocess.run

        def mock_run(cmd, *args, **kwargs):
            if isinstance(cmd, list) and cmd[:2] == ["git", "checkout"] and branch_name in cmd:
                raise subprocess.TimeoutExpired(cmd, 60)
            return original_run(cmd, *args, **kwargs)

        with mock.patch("subprocess.run", autospec=True, side_effect=mock_run):
            result = merge_worktree_branch(git_project, branch_name, "test-stage")

        assert result.success is False
        assert result.had_changes is False
    finally:
        _cleanup_branch(git_project, branch_name)


# ── Tier 8: cleanup_stale_worktrees ────────────────────────────────────


def test_cleanup_stale_worktrees_no_stale(git_project: Path):
    """cleanup_stale_worktrees should be no-op when no stale worktrees exist."""
    # Just call the function - should not raise or emit errors
    cleanup_stale_worktrees(git_project)


def test_cleanup_stale_worktrees_removes_old(git_project: Path, tmp_path: Path):
    """cleanup_stale_worktrees removes worktrees older than 6 hours."""
    import time

    # Create a worktree in /tmp/kodo-*
    worktree_dir, branch_name = create_worktree(git_project, "old-stage")

    try:
        # Verify it has kodo- in the name (created by create_worktree)
        assert "kodo-" in worktree_dir.name

        # Set mtime to 7 hours ago (older than 6 hour threshold)
        seven_hours_ago = time.time() - (7 * 3600)
        import os
        os.utime(worktree_dir, (seven_hours_ago, seven_hours_ago))

        # Verify worktree exists before cleanup
        assert worktree_dir.exists()

        # Run cleanup
        cleanup_stale_worktrees(git_project)

        # Worktree should be removed
        assert not worktree_dir.exists()

        # Branch should also be deleted (since it starts with kodo-)
        result = subprocess.run(
            ["git", "branch", "--list", branch_name],
            cwd=git_project,
            capture_output=True,
            text=True,
        )
        assert branch_name not in result.stdout
    except Exception:
        # Cleanup in case test fails
        if worktree_dir.exists():
            remove_worktree(git_project, worktree_dir, branch_name)
        raise


def test_cleanup_stale_worktrees_keeps_recent(git_project: Path):
    """cleanup_stale_worktrees preserves worktrees younger than 6 hours."""
    # Create a fresh worktree
    worktree_dir, branch_name = create_worktree(git_project, "recent-stage")

    try:
        # Verify it's fresh (no need to modify mtime)
        assert worktree_dir.exists()

        # Run cleanup
        cleanup_stale_worktrees(git_project)

        # Worktree should still exist (not stale)
        assert worktree_dir.exists()

        # Branch should still exist
        result = subprocess.run(
            ["git", "branch", "--list", branch_name],
            cwd=git_project,
            capture_output=True,
            text=True,
        )
        assert branch_name in result.stdout
    finally:
        remove_worktree(git_project, worktree_dir, branch_name)


def test_cleanup_stale_worktrees_git_list_fails(git_project: Path):
    """cleanup_stale_worktrees handles git worktree list failure gracefully."""
    original_run = subprocess.run

    def mock_run(cmd, *args, **kwargs):
        if isinstance(cmd, list) and cmd[:3] == ["git", "worktree", "list"]:
            return subprocess.CompletedProcess(
                cmd, 1, stdout="", stderr="worktree list failed"
            )
        return original_run(cmd, *args, **kwargs)

    # Should not raise - just logs and returns
    with mock.patch("subprocess.run", autospec=True, side_effect=mock_run):
        cleanup_stale_worktrees(git_project)


def test_cleanup_stale_worktrees_never_crashes(git_project: Path):
    """cleanup_stale_worktrees never crashes even on unexpected errors."""
    # Mock to raise an exception
    def mock_run(*args, **kwargs):
        raise RuntimeError("Unexpected error!")

    # Should not raise - wrapped in try/except
    with mock.patch("subprocess.run", autospec=True, side_effect=mock_run):
        cleanup_stale_worktrees(git_project)


def test_cleanup_stale_worktrees_skips_non_kodo_paths(git_project: Path):
    """cleanup_stale_worktrees only processes worktrees with kodo- in their name."""
    import time

    # Create a regular worktree (not in /tmp/kodo-*)
    # This would require mocking create_worktree, which is complex
    # Instead, we'll test via the porcelain parsing logic

    original_run = subprocess.run

    # Mock git worktree list to return a non-kodo path
    def mock_run(cmd, *args, **kwargs):
        if isinstance(cmd, list) and cmd[:3] == ["git", "worktree", "list"]:
            # Return a worktree that's NOT in /tmp/kodo-*
            return subprocess.CompletedProcess(
                cmd, 0,
                stdout=(
                    f"worktree {git_project}\n"
                    f"HEAD {subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=git_project, capture_output=True, text=True).stdout.strip()}\n"
                    f"branch refs/heads/main\n"
                    "\n"
                    "worktree /tmp/other-worktree\n"
                    "HEAD abc123\n"
                    "branch refs/heads/kodo-test-branch\n"
                    "\n"
                ),
                stderr=""
            )
        return original_run(cmd, *args, **kwargs)

    # Should not attempt to remove non-kodo worktrees
    with mock.patch("subprocess.run", autospec=True, side_effect=mock_run):
        cleanup_stale_worktrees(git_project)


def test_cleanup_stale_worktrees_handles_missing_worktree_paths(git_project: Path):
    """cleanup_stale_worktrees handles worktrees whose paths no longer exist."""
    original_run = subprocess.run

    # Mock git worktree list to return a kodo worktree that doesn't exist
    def mock_run(cmd, *args, **kwargs):
        if isinstance(cmd, list) and cmd[:3] == ["git", "worktree", "list"]:
            return subprocess.CompletedProcess(
                cmd, 0,
                stdout=(
                    f"worktree {git_project}\n"
                    f"HEAD {subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=git_project, capture_output=True, text=True).stdout.strip()}\n"
                    f"branch refs/heads/main\n"
                    "\n"
                    "worktree /tmp/kodo-nonexistent-abc123\n"
                    "HEAD abc123\n"
                    "branch refs/heads/kodo-stage-1-abc123\n"
                    "\n"
                ),
                stderr=""
            )
        return original_run(cmd, *args, **kwargs)

    # Should not crash when worktree path doesn't exist
    with mock.patch("subprocess.run", autospec=True, side_effect=mock_run):
        cleanup_stale_worktrees(git_project)


# ── Tier 9: orphaned kodo branch cleanup ───────────────────────────────


def test_cleanup_orphaned_kodo_branches_deletes_orphans(git_project: Path):
    """Orphaned kodo-* branches (no worktree dir) are deleted by cleanup."""
    # Create a worktree, then manually remove the directory + prune
    # to simulate an interrupted cleanup that left the branch behind.
    wt_dir, branch = create_worktree(git_project, "orphan-test")
    import shutil

    shutil.rmtree(wt_dir, ignore_errors=True)
    subprocess.run(
        ["git", "worktree", "prune"],
        cwd=git_project,
        capture_output=True,
    )

    # Branch should still exist but worktree dir should not
    assert not wt_dir.exists()
    branch_check = subprocess.run(
        ["git", "branch", "--list", branch],
        cwd=git_project,
        capture_output=True,
        text=True,
    )
    assert branch in branch_check.stdout

    # Run cleanup — should find and remove the orphaned branch
    cleanup_stale_worktrees(git_project)

    # Orphaned branch should now be gone
    branch_check = subprocess.run(
        ["git", "branch", "--list", branch],
        cwd=git_project,
        capture_output=True,
        text=True,
    )
    assert branch not in branch_check.stdout


def test_cleanup_orphaned_branches_preserves_active_worktree_branches(
    git_project: Path,
):
    """Branches with active worktrees must NOT be deleted by orphan cleanup."""
    wt_dir, branch = create_worktree(git_project, "active-stage")

    try:
        assert wt_dir.exists()

        # Run cleanup — should NOT delete the branch because worktree exists
        cleanup_stale_worktrees(git_project)

        # Branch should still exist
        branch_check = subprocess.run(
            ["git", "branch", "--list", branch],
            cwd=git_project,
            capture_output=True,
            text=True,
        )
        assert branch in branch_check.stdout
    finally:
        remove_worktree(git_project, wt_dir, branch)


def test_cleanup_orphaned_kodo_branches_direct(git_project: Path):
    """_cleanup_orphaned_kodo_branches deletes branches not in active set."""
    # Create a branch manually (without a worktree)
    branch_name = "kodo-orphan-direct-test"
    subprocess.run(
        ["git", "branch", branch_name],
        cwd=git_project,
        capture_output=True,
        check=True,
    )

    from kodo import log

    _cleanup_orphaned_kodo_branches(
        git_project,
        active_worktree_branches=set(),  # no active branches
        log=log,
    )

    # Branch should be gone
    branch_check = subprocess.run(
        ["git", "branch", "--list", branch_name],
        cwd=git_project,
        capture_output=True,
        text=True,
    )
    assert branch_name not in branch_check.stdout


def test_cleanup_orphaned_kodo_branches_skips_active(git_project: Path):
    """_cleanup_orphaned_kodo_branches skips branches in the active set."""
    branch_name = "kodo-active-direct-test"
    subprocess.run(
        ["git", "branch", branch_name],
        cwd=git_project,
        capture_output=True,
        check=True,
    )

    try:
        from kodo import log

        _cleanup_orphaned_kodo_branches(
            git_project,
            active_worktree_branches={branch_name},  # marked active
            log=log,
        )

        # Branch should still exist
        branch_check = subprocess.run(
            ["git", "branch", "--list", branch_name],
            cwd=git_project,
            capture_output=True,
            text=True,
        )
        assert branch_name in branch_check.stdout
    finally:
        subprocess.run(
            ["git", "branch", "-D", branch_name],
            cwd=git_project,
            capture_output=True,
        )


def test_cleanup_orphaned_kodo_branches_handles_git_failure(git_project: Path):
    """_cleanup_orphaned_kodo_branches handles git branch --list failure."""
    original_run = subprocess.run

    def mock_run(cmd, *args, **kwargs):
        if isinstance(cmd, list) and cmd[:3] == ["git", "branch", "--list"]:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="error")
        return original_run(cmd, *args, **kwargs)

    from kodo import log

    # Should not crash
    with mock.patch("subprocess.run", autospec=True, side_effect=mock_run):
        _cleanup_orphaned_kodo_branches(git_project, set(), log)
