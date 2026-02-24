"""CLI edge cases: unusual inputs that should not crash or produce tracebacks."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from kodo.cli._main import _main_inner
from kodo.orchestrators.base import CycleResult, RunResult


def _fake_run_result():
    return RunResult(
        cycles=[
            CycleResult(exchanges=1, finished=True, summary="Done."),
        ],
    )


def test_very_long_goal_preserved(tmp_path: Path):
    """A 10k-char goal passes through to launch_run without truncation."""
    long_goal = "x" * 10000

    with (
        patch("kodo.cli._params.has_claude", return_value=True),
        patch("kodo.cli._params.check_api_key", return_value=None),
        patch("kodo.cli._main.launch_run") as mock_launch,
    ):
        mock_launch.return_value = _fake_run_result()
        sys.argv = [
            "kodo",
            "--goal",
            long_goal,
            "--skip-intake",
            "--yes",
            str(tmp_path),
        ]
        _main_inner()

    goal_passed = mock_launch.call_args[0][1]
    assert len(goal_passed) == 10000
    assert goal_passed == long_goal


def test_unicode_and_special_chars_in_goal(tmp_path: Path):
    """Unicode, newlines, and quotes in --goal pass through unmangled."""
    special_goal = (
        "Build a «café» app\nWith \"quotes\" and 'apostrophes'\nAnd emoji: 🚀"
    )

    with (
        patch("kodo.cli._params.has_claude", return_value=True),
        patch("kodo.cli._params.check_api_key", return_value=None),
        patch("kodo.cli._main.launch_run") as mock_launch,
    ):
        mock_launch.return_value = _fake_run_result()
        sys.argv = [
            "kodo",
            "--goal",
            special_goal,
            "--skip-intake",
            "--yes",
            str(tmp_path),
        ]
        _main_inner()

    goal_passed = mock_launch.call_args[0][1]
    assert "café" in goal_passed
    assert "🚀" in goal_passed
    assert "\n" in goal_passed


@pytest.mark.skipif(
    hasattr(os, "getuid") and os.getuid() == 0,
    reason="Skip when running as root (chmod 000 may not deny read)",
)
def test_unreadable_goal_file_no_traceback(tmp_path: Path, capsys):
    """--goal-file pointing to a chmod-000 file gives a clean error, not a traceback."""
    goal_file = tmp_path / "secret_goal.md"
    goal_file.write_text("secret content")
    try:
        goal_file.chmod(0o000)
    except OSError:
        pytest.skip("Cannot chmod 000 on this platform")

    try:
        with (
            patch("kodo.cli._params.has_claude", return_value=True),
            patch("kodo.cli._params.check_api_key", return_value=None),
        ):
            sys.argv = [
                "kodo",
                "--goal-file",
                str(goal_file),
                "--skip-intake",
                "--yes",
                str(tmp_path),
            ]
            with pytest.raises(SystemExit):
                _main_inner()

        combined = capsys.readouterr().out + capsys.readouterr().err
        assert "Traceback" not in combined
    finally:
        goal_file.chmod(0o644)
