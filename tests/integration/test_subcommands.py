"""Integration tests for CLI subcommands — error paths and contracts."""

import pytest

from tests.integration.cli_wrapper import run_kodo

pytestmark = pytest.mark.integration


class TestLogs:
    def test_logs_help_shows_options(self) -> None:
        result = run_kodo("logs", "--help")
        assert result.success()
        assert "--port" in result.output()

    def test_logs_nonexistent_logfile(self) -> None:
        result = run_kodo("logs", "/tmp/nonexistent_xyz_12345.jsonl", timeout=10)
        assert result.exit_code != 0


class TestIssue:
    def test_issue_help_exits_zero(self) -> None:
        result = run_kodo("issue", "--help")
        assert result.success()
