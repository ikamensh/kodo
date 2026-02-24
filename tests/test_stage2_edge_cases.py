"""Edge case and error handling tests for kodo CLI.

Tests input validation and error paths that may reveal real bugs.
"""

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


def _run_expecting_error(capsys, *, expect_stderr: bool = True) -> tuple[str, str]:
    """Run _main_inner expecting SystemExit, return (out, err)."""
    with pytest.raises(SystemExit):
        _main_inner()
    captured = capsys.readouterr()
    return captured.out, captured.err


# ---------------------------------------------------------------------------
# Test 1: --goal-file nonexistent
# ---------------------------------------------------------------------------


def test_goal_file_nonexistent_produces_clean_error(tmp_path: Path, capsys):
    """--goal-file with nonexistent path produces clean error, not traceback."""
    nonexistent = tmp_path / "nonexistent_file_xyz.md"
    assert not nonexistent.exists()

    with (
        patch("kodo.cli._params.has_claude", return_value=True),
        patch("kodo.cli._params.check_api_key", return_value=None),
    ):
        sys.argv = [
            "kodo",
            "--goal-file",
            str(nonexistent),
            "--skip-intake",
            "--yes",
            str(tmp_path),
        ]
        out, err = _run_expecting_error(capsys)

    combined = out + err
    assert (
        "Error:" in combined
        or "not found" in combined.lower()
        or "Goal file" in combined
    )
    assert "Traceback" not in combined


# ---------------------------------------------------------------------------
# Test 2: --goal-file unreadable (permission denied)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    hasattr(os, "getuid") and os.getuid() == 0,
    reason="Skip when running as root (chmod 000 may not deny read)",
)
def test_goal_file_unreadable_produces_clean_error(tmp_path: Path, capsys):
    """--goal-file with unreadable file (chmod 000) produces clean error."""
    goal_file = tmp_path / "secret_goal.md"
    goal_file.write_text("secret content")
    try:
        goal_file.chmod(0o000)
    except OSError:
        pytest.skip("Cannot chmod 000 on this platform (e.g. Windows)")

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
            out, err = _run_expecting_error(capsys)

        combined = out + err
        assert (
            "Error:" in combined
            or "Cannot read" in combined
            or "Permission" in combined
        )
        assert "Traceback" not in combined
    finally:
        goal_file.chmod(0o644)


# ---------------------------------------------------------------------------
# Test 3: Project directory doesn't exist
# ---------------------------------------------------------------------------


def test_project_dir_nonexistent(tmp_path: Path, capsys):
    """Nonexistent project_dir — CLI creates it (mkdir parents) or fails cleanly."""
    project_dir = tmp_path / "new_project_xyz"
    assert not project_dir.exists()

    with (
        patch("kodo.cli._params.has_claude", return_value=True),
        patch("kodo.cli._params.check_api_key", return_value=None),
        patch("kodo.cli._main.launch_run") as mock_launch,
    ):
        mock_launch.return_value = _fake_run_result()
        sys.argv = [
            "kodo",
            "--goal",
            "test",
            "--skip-intake",
            "--yes",
            str(project_dir),
        ]
        _main_inner()

    # Current behavior: CLI creates the dir and proceeds
    assert project_dir.exists()


# ---------------------------------------------------------------------------
# Test 4: --exchanges 0 and --exchanges -1
# ---------------------------------------------------------------------------


def test_exchanges_zero_produces_error(tmp_path: Path, capsys):
    """--exchanges 0 produces error about invalid value."""
    with (
        patch("kodo.cli._params.has_claude", return_value=True),
        patch("kodo.cli._params.check_api_key", return_value=None),
    ):
        sys.argv = [
            "kodo",
            "--goal",
            "test",
            "--exchanges",
            "0",
            "--skip-intake",
            "--yes",
            str(tmp_path),
        ]
        out, err = _run_expecting_error(capsys)

    combined = out + err
    assert "exchanges" in combined.lower()
    assert "Traceback" not in combined


def test_exchanges_negative_produces_error(tmp_path: Path, capsys):
    """--exchanges -1 produces error about invalid value."""
    with (
        patch("kodo.cli._params.has_claude", return_value=True),
        patch("kodo.cli._params.check_api_key", return_value=None),
    ):
        sys.argv = [
            "kodo",
            "--goal",
            "test",
            "--exchanges",
            "-1",
            "--skip-intake",
            "--yes",
            str(tmp_path),
        ]
        out, err = _run_expecting_error(capsys)

    combined = out + err
    assert "exchanges" in combined.lower()
    assert "Traceback" not in combined


# ---------------------------------------------------------------------------
# Test 5: --team invalid (argparse rejects)
# ---------------------------------------------------------------------------


def test_team_invalid_produces_clean_error(tmp_path: Path, capsys):
    """--team nonexistent_team_name produces clean error (argparse invalid choice)."""
    with (
        patch("kodo.cli._params.has_claude", return_value=True),
        patch("kodo.cli._params.check_api_key", return_value=None),
    ):
        sys.argv = [
            "kodo",
            "--goal",
            "test",
            "--team",
            "nonexistent_team_name",
            "--skip-intake",
            "--yes",
            str(tmp_path),
        ]
        out, err = _run_expecting_error(capsys)

    combined = out + err
    assert "Traceback" not in combined
    assert (
        "invalid" in combined.lower()
        or "choice" in combined.lower()
        or "team" in combined.lower()
    )


# ---------------------------------------------------------------------------
# Test 6: Malformed .kodo/team.json
# ---------------------------------------------------------------------------


def test_malformed_team_json_handled_gracefully(tmp_path: Path, capsys):
    """Invalid JSON in .kodo/team.json — handled gracefully, no traceback."""
    kodo_dir = tmp_path / ".kodo"
    kodo_dir.mkdir(parents=True, exist_ok=True)
    (kodo_dir / "team.json").write_text("{invalid json!!")

    with (
        patch("kodo.cli._params.has_claude", return_value=True),
        patch("kodo.cli._params.check_api_key", return_value=None),
    ):
        sys.argv = [
            "kodo",
            "--goal",
            "test",
            "--skip-intake",
            "--yes",
            str(tmp_path),
        ]
        out, err = _run_expecting_error(capsys)

    combined = out + err
    assert "Traceback" not in combined
    assert "Error:" in combined or "Invalid" in combined or "JSON" in combined


# ---------------------------------------------------------------------------
# Test 7: Very long goal text
# ---------------------------------------------------------------------------


def test_very_long_goal_works(tmp_path: Path):
    """Goal of 10000 chars works without truncation or crash."""
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


# ---------------------------------------------------------------------------
# Test 8: Goal with special characters
# ---------------------------------------------------------------------------


def test_goal_special_characters_passes_through(tmp_path: Path):
    """Goal with unicode, newlines, quotes passes through to launch_run."""
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
    assert "quotes" in goal_passed
    assert "apostrophes" in goal_passed
    assert "🚀" in goal_passed
    assert "\n" in goal_passed
