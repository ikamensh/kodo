"""Tests for kodo.orchestrators.run_status."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from kodo import log
from kodo.orchestrators.git_ops import create_worktree, remove_worktree
from kodo.orchestrators.run_status import read_run_status, write_run_status


@pytest.mark.slow
@pytest.mark.parametrize("location", ["checkout", "worktree", "subdirectory"])
def test_generated_status_does_not_dirty_repository(git_project: Path, location: str):
    """Runtime status stays out of commits; user files and ignore rules stay intact."""
    worktree = None
    project = git_project
    if location == "worktree":
        worktree, branch = create_worktree(git_project, "status")
        project = worktree
    elif location == "subdirectory":
        project = git_project / "nested [draft]"
        project.mkdir()

    exclude = git_project / ".git" / "info" / "exclude"
    existing_rules = "# Existing user rules\nkeep.local"
    exclude.write_text(existing_rules)
    try:
        write_run_status(project, "first goal")
        write_run_status(project, "next goal")
        (project / ".kodo" / "config.json").write_text("{}")

        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=project,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert "run-status.md" not in status
        assert ".kodo/config.json" in status
        assert read_run_status(project).startswith("# Run Status")
        assert exclude.read_text().startswith(existing_rules + "\n")
        assert exclude.read_text().count("run-status.md") == 1

        subprocess.run(["git", "add", "-A"], cwd=project, check=True)
        staged = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=project,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert "run-status.md" not in staged
        assert ".kodo/config.json" in staged
    finally:
        if worktree:
            remove_worktree(git_project, worktree, branch)


def test_write_creates_file(tmp_path: Path):
    """write_run_status creates .kodo/run-status.md."""
    content = write_run_status(tmp_path, "Build a widget")
    status_file = tmp_path / ".kodo" / "run-status.md"
    assert status_file.exists()
    assert status_file.read_text() == content
    assert "Build a widget" in content


def test_write_includes_agent_stats(tmp_path: Path):
    """Agent stats table appears when stats are recorded."""
    rd = log.RunDir.create(tmp_path, "test_run")
    log.init(rd)

    stats = log.get_run_stats()
    stats.record_agent(
        "worker_fast",
        cost_usd=0.01,
        input_tokens=30000,
        output_tokens=15000,
        elapsed_s=492.0,
        is_error=False,
        cost_bucket="claude_subscription",
    )
    stats.record_agent(
        "tester",
        cost_usd=0.0,
        input_tokens=8000,
        output_tokens=4000,
        elapsed_s=225.0,
        is_error=False,
        cost_bucket="cursor_subscription",
    )

    content = write_run_status(tmp_path, "goal")
    assert "## Agent Stats" in content
    assert "worker_fast" in content
    assert "tester" in content
    assert "| Agent |" in content


def test_write_includes_stage_label(tmp_path: Path):
    """Stage label appears in progress section."""
    content = write_run_status(
        tmp_path,
        "goal",
        stage_label="2/3: Implementation",
        cycle_num=3,
        max_cycles=5,
    )
    assert "2/3: Implementation" in content
    assert "Cycle: 3/5" in content


def test_write_truncates_long_goal(tmp_path: Path):
    """Goals longer than 500 chars are truncated."""
    long_goal = "x" * 600
    content = write_run_status(tmp_path, long_goal)
    assert "..." in content
    assert len([line for line in content.split("\n") if "x" in line][0]) < 510


def test_read_missing(tmp_path: Path):
    """read_run_status returns empty string when file is missing."""
    assert read_run_status(tmp_path) == ""


def test_read_existing(tmp_path: Path):
    """read_run_status returns file content."""
    write_run_status(tmp_path, "test goal", cycle_num=1, max_cycles=3)
    content = read_run_status(tmp_path)
    assert "# Run Status" in content
    assert "test goal" in content
