"""Tests for kodo.orchestrators.run_status."""

from __future__ import annotations

from pathlib import Path

from kodo import log
from kodo.orchestrators.run_status import read_run_status, write_run_status


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
        "worker_fast", cost_usd=0.01,
        input_tokens=30000, output_tokens=15000,
        elapsed_s=492.0, is_error=False, cost_bucket="claude_subscription",
    )
    stats.record_agent(
        "tester", cost_usd=0.0,
        input_tokens=8000, output_tokens=4000,
        elapsed_s=225.0, is_error=False, cost_bucket="cursor_subscription",
    )

    content = write_run_status(tmp_path, "goal")
    assert "## Agent Stats" in content
    assert "worker_fast" in content
    assert "tester" in content
    assert "| Agent |" in content


def test_write_includes_stage_label(tmp_path: Path):
    """Stage label appears in progress section."""
    content = write_run_status(
        tmp_path, "goal",
        stage_label="2/3: Implementation",
        cycle_num=3, max_cycles=5,
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
