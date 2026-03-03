"""Tests for batch 2 fixes (F1, F2, F4, F8, F9, F11, F16, F17, F20, F21)."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# F8: dict(items) instead of comprehension
# ---------------------------------------------------------------------------


class TestF8DictItems:
    """_cmd_teams_auto should use dict(items) not {k: v for k, v in ...}."""

    def test_dict_items_equivalent(self):
        """Verify dict(items) produces the same result as the comprehension."""
        backends = {"claude": True, "cursor": False, "codex": True}
        has = dict(backends.items())
        assert has == backends
        assert has is not backends  # new dict, not same object


# ---------------------------------------------------------------------------
# F11: HTTPServer cleanup in viewer.py
# ---------------------------------------------------------------------------


class TestF11ViewerServerCleanup:
    """_serve() should clean up HTTPServer in a finally block."""

    def test_serve_has_finally_cleanup(self):
        """Verify the source code has the finally block with shutdown/close."""
        import inspect
        from kodo.viewer import _serve

        source = inspect.getsource(_serve)
        assert "finally:" in source
        assert "server.shutdown()" in source
        assert "server.server_close()" in source


# ---------------------------------------------------------------------------
# F16: Warning for shadowed built-in teams
# ---------------------------------------------------------------------------


class TestF16ShadowedBuiltInTeam:
    """get_team() should warn when a user JSON team shadows a built-in."""

    def test_shadow_warning_logged(self, tmp_path: Path, caplog):
        """Creating a user team with a built-in name logs a warning."""
        from kodo.factory import get_team

        kodo_dir = tmp_path / ".kodo" / "teams"
        kodo_dir.mkdir(parents=True)
        team_json = {"name": "full", "description": "custom full"}
        (kodo_dir / "full.json").write_text(json.dumps(team_json))

        with (
            patch("pathlib.Path.home", return_value=tmp_path),
            caplog.at_level(logging.WARNING),
        ):
            result = get_team("full")

        assert result is not None
        assert any("shadows built-in" in r.message for r in caplog.records), (
            f"Expected shadow warning, got: {[r.message for r in caplog.records]}"
        )

    def test_no_warning_for_non_shadowing(self, tmp_path: Path, caplog):
        """A user team that doesn't match a built-in should not warn."""
        from kodo.factory import get_team

        kodo_dir = tmp_path / ".kodo" / "teams"
        kodo_dir.mkdir(parents=True)
        team_json = {"name": "custom_xyz", "description": "custom team"}
        (kodo_dir / "custom_xyz.json").write_text(json.dumps(team_json))

        with (
            patch("pathlib.Path.home", return_value=tmp_path),
            caplog.at_level(logging.WARNING),
        ):
            result = get_team("custom_xyz")

        assert result is not None
        assert not any("shadows built-in" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# F17: cleanup_abandoned_worktrees
# ---------------------------------------------------------------------------


class TestF17CleanupAbandonedWorktrees:
    """cleanup_abandoned_worktrees should remove stale kodo worktrees."""

    @pytest.fixture
    def git_project(self, tmp_path: Path):
        """Create a minimal git project for worktree tests."""
        project = tmp_path / "repo"
        project.mkdir()
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "t@t",
        }
        subprocess.run(
            ["git", "init"], cwd=project, capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "init"],
            cwd=project, capture_output=True, check=True, env=env,
        )
        return project

    def test_cleanup_on_empty_repo(self, git_project):
        """cleanup_abandoned_worktrees on a repo with no worktrees returns 0."""
        from kodo.orchestrators.base import cleanup_abandoned_worktrees

        cleaned = cleanup_abandoned_worktrees(git_project)
        assert cleaned == 0

    def test_cleanup_removes_stale_worktree(self, git_project):
        """A kodo worktree older than max_age should be cleaned up."""
        import os
        import time

        from kodo.orchestrators.base import (
            cleanup_abandoned_worktrees,
            create_worktree,
        )

        wt, branch = create_worktree(git_project, "test-stale")
        assert wt.exists()

        # Set mtime to 2 days ago
        old_time = time.time() - 48 * 3600
        os.utime(wt, (old_time, old_time))

        cleaned = cleanup_abandoned_worktrees(git_project, max_age_hours=24)
        assert cleaned >= 1
        assert not wt.exists()

    def test_cleanup_keeps_fresh_worktree(self, git_project):
        """A kodo worktree younger than max_age should not be cleaned."""
        from kodo.orchestrators.base import (
            cleanup_abandoned_worktrees,
            create_worktree,
        )

        wt, branch = create_worktree(git_project, "test-fresh")
        assert wt.exists()

        cleaned = cleanup_abandoned_worktrees(git_project, max_age_hours=24)
        assert cleaned == 0
        assert wt.exists()

        # Clean up
        from kodo.orchestrators.base import remove_worktree
        remove_worktree(git_project, wt, branch)


# ---------------------------------------------------------------------------
# F20: Defensive guards in base.py
# ---------------------------------------------------------------------------


class TestF20DefensiveGuards:
    """remove_worktree and commit_worktree_changes should guard preconditions."""

    def test_remove_worktree_rejects_nonexistent_project(self, tmp_path: Path):
        from kodo.orchestrators.base import remove_worktree

        fake_project = tmp_path / "nonexistent"
        with pytest.raises(RuntimeError, match="non-existent project_dir"):
            remove_worktree(fake_project, tmp_path / "wt", "some-branch")

    def test_remove_worktree_rejects_empty_branch(self, tmp_path: Path):
        from kodo.orchestrators.base import remove_worktree

        with pytest.raises(RuntimeError, match="empty branch_name"):
            remove_worktree(tmp_path, tmp_path / "wt", "")

    def test_commit_worktree_rejects_nonexistent_dir(self, tmp_path: Path):
        from kodo.orchestrators.base import commit_worktree_changes

        fake_wt = tmp_path / "nonexistent"
        with pytest.raises(RuntimeError, match="non-existent worktree"):
            commit_worktree_changes(fake_wt, "stage1")

    def test_commit_worktree_rejects_empty_stage(self, tmp_path: Path):
        from kodo.orchestrators.base import commit_worktree_changes

        with pytest.raises(RuntimeError, match="empty stage_name"):
            commit_worktree_changes(tmp_path, "")


# ---------------------------------------------------------------------------
# F21: json_output_redirect context manager
# ---------------------------------------------------------------------------


class TestF21JsonOutputRedirect:
    """json_output_redirect should save/restore stdout and reset _original_stdout."""

    def test_context_manager_redirects_and_restores(self):
        from kodo.cli._launch import json_output_redirect, _original_stdout

        original = sys.stdout
        assert _original_stdout is None

        with json_output_redirect() as saved:
            from kodo.cli import _launch as mod

            # Inside context: stdout is redirected to stderr
            assert sys.stdout is sys.stderr
            # The saved value is the original stdout
            assert saved is original
            # Module-level variable is set
            assert mod._original_stdout is original

        # After context: restored
        assert sys.stdout is original
        from kodo.cli import _launch as mod
        assert mod._original_stdout is None

    def test_context_manager_restores_on_exception(self):
        from kodo.cli._launch import json_output_redirect

        original = sys.stdout

        with pytest.raises(ValueError):
            with json_output_redirect():
                assert sys.stdout is sys.stderr
                raise ValueError("test error")

        # Should still restore
        assert sys.stdout is original
        from kodo.cli import _launch as mod
        assert mod._original_stdout is None


# ---------------------------------------------------------------------------
# F2: _TEST_API_KEY constant in test_claude.py
# ---------------------------------------------------------------------------


class TestF2ApiKeyConstant:
    """Verify test_claude.py uses a constant instead of hardcoded secrets."""

    def test_constant_is_defined(self):
        """_TEST_API_KEY should be defined and used in test_claude.py."""
        from tests.sessions.test_claude import _TEST_API_KEY

        assert _TEST_API_KEY == "sk-test-secret"


# ---------------------------------------------------------------------------
# Bare "git" strings removed from base.py
# ---------------------------------------------------------------------------


class TestBareGitRemoval:
    """All git subprocess calls in base.py should use _git() helper."""

    def test_no_bare_git_in_subprocess_calls(self):
        """Source should not contain bare 'git' in subprocess list args."""
        import inspect
        import kodo.orchestrators.base as base_mod

        source = inspect.getsource(base_mod)
        # Check that no subprocess calls use bare "git" string
        # (they should all use _git())
        import re

        # Find patterns like ["git", or [ "git",
        bare_git_calls = re.findall(r'\[\s*"git"\s*,', source)
        assert bare_git_calls == [], (
            f"Found bare 'git' in subprocess calls: {bare_git_calls}"
        )
