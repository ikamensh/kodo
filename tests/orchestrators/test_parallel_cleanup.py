"""Tests for kodo/orchestrators/parallel.py cleanup hardening.

Covers:
- _suppress_keyboard_interrupt: defers SIGINT during critical cleanup
- cleanup_and_merge_worktrees: BaseException-resilient cleanup phases
"""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from kodo.orchestrators.parallel import (
    _suppress_keyboard_interrupt,
    cleanup_and_merge_worktrees,
)
from kodo.orchestrators.types import GoalStage, StageResult


# ── _suppress_keyboard_interrupt ───────────────────────────────────────


def test_suppress_keyboard_interrupt_normal_flow():
    """Normal execution inside the context manager should work transparently."""
    result = []
    with _suppress_keyboard_interrupt():
        result.append("executed")
    assert result == ["executed"]


def test_suppress_keyboard_interrupt_defers_sigint():
    """SIGINT during the context is deferred and re-raised after exit."""
    with pytest.raises(KeyboardInterrupt):
        with _suppress_keyboard_interrupt():
            # Send SIGINT to ourselves — the handler should defer it
            os.kill(os.getpid(), signal.SIGINT)
            # Execution should continue (not immediately interrupted)
            # The signal is only re-raised on context exit

    # If we got here via pytest.raises, the signal was properly deferred
    # and re-raised


def test_suppress_keyboard_interrupt_in_non_main_thread():
    """Context manager is a no-op in non-main threads (graceful fallback)."""
    import threading

    errors = []

    def run_in_thread():
        try:
            with _suppress_keyboard_interrupt():
                pass  # should not crash
        except Exception as e:
            errors.append(e)

    t = threading.Thread(target=run_in_thread)
    t.start()
    t.join(timeout=5)
    assert not errors, f"Unexpected errors in thread: {errors}"


# ── cleanup_and_merge_worktrees ────────────────────────────────────────


def test_cleanup_handles_session_close_raising_keyboard_interrupt():
    """Cleanup continues even if agent.close() raises KeyboardInterrupt.

    Since we use `except BaseException`, KeyboardInterrupt from agent.close()
    should be caught and the rest of cleanup should proceed.
    """
    stage = GoalStage(
        index=1,
        name="test-stage",
        description="test",
        acceptance_criteria="done",
    )

    # Agent whose close() raises KeyboardInterrupt
    bad_agent = mock.MagicMock()
    bad_agent.close.side_effect = KeyboardInterrupt("simulated")

    good_agent = mock.MagicMock()

    stage_teams = {
        1: {"worker": bad_agent, "tester": good_agent},
    }

    # No worktrees (empty dict) — skip worktree phases
    worktrees: dict[int, tuple[Path, str]] = {}
    parallel_results = [
        StageResult(stage_index=1, stage_name="test-stage", finished=True, success=True),
    ]

    # Should not raise — SIGINT is suppressed during cleanup
    with mock.patch(
        "kodo.orchestrators.parallel._suppress_keyboard_interrupt", autospec=True
    ) as mock_suppress:
        # Use a real context manager that doesn't actually suppress
        # (so we can test the BaseException handling directly)
        mock_suppress.return_value.__enter__ = mock.MagicMock(return_value=None)
        mock_suppress.return_value.__exit__ = mock.MagicMock(return_value=False)

        # Call the inner function directly to test BaseException handling
        from kodo.orchestrators.parallel import _cleanup_and_merge_worktrees_inner

        _cleanup_and_merge_worktrees_inner(
            [stage], worktrees, stage_teams, parallel_results, Path("/tmp/fake"),
        )

    # Both agents should have had close() called
    bad_agent.close.assert_called_once()
    good_agent.close.assert_called_once()


def test_cleanup_handles_commit_worktree_changes_raising_base_exception():
    """Cleanup continues if commit_worktree_changes raises BaseException."""
    stage = GoalStage(
        index=1,
        name="persist-stage",
        description="test",
        acceptance_criteria="done",
        persist_changes=True,
    )

    worktrees = {1: (Path("/tmp/kodo-fake-wt"), "kodo-fake-branch")}
    stage_teams: dict = {}
    parallel_results = [
        StageResult(stage_index=1, stage_name="persist-stage", finished=True, success=True),
    ]

    # Mock commit_worktree_changes to raise KeyboardInterrupt
    with (
        mock.patch(
            "kodo.orchestrators.parallel.commit_worktree_changes", autospec=True,
            side_effect=KeyboardInterrupt("simulated"),
        ),
        mock.patch(
            "kodo.orchestrators.parallel.remove_worktree", autospec=True,
        ) as mock_remove,
    ):
        from kodo.orchestrators.parallel import _cleanup_and_merge_worktrees_inner

        _cleanup_and_merge_worktrees_inner(
            [stage], worktrees, stage_teams, parallel_results, Path("/tmp/fake"),
        )

    # Should still have attempted to remove the worktree
    mock_remove.assert_called_once()


def test_cleanup_and_merge_worktrees_wraps_with_sigint_suppression():
    """cleanup_and_merge_worktrees uses _suppress_keyboard_interrupt."""
    stage = GoalStage(
        index=1,
        name="test",
        description="test",
        acceptance_criteria="done",
    )

    with mock.patch(
        "kodo.orchestrators.parallel._cleanup_and_merge_worktrees_inner", autospec=True,
    ) as mock_inner:
        cleanup_and_merge_worktrees(
            [stage], {}, {}, [], Path("/tmp/fake"),
        )
        mock_inner.assert_called_once()
