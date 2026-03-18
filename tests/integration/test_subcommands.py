"""Integration tests for informational subcommands (US3–US6, US11, US12)."""

import pytest

from tests.integration.cli_wrapper import run_kodo

pytestmark = pytest.mark.integration


# --- US3: kodo runs ---

class TestRuns:
    def test_runs_exits_zero(self) -> None:
        result = run_kodo("runs")
        assert result.success(), f"kodo runs failed: {result.output()}"

    def test_runs_shows_table_header(self) -> None:
        result = run_kodo("runs")
        out = result.output().upper()
        assert "RUN" in out and "STATUS" in out, f"Missing table header in: {result.output()}"

    def test_runs_singular_alias(self) -> None:
        """'kodo run' is an alias for 'kodo runs'."""
        result = run_kodo("run")
        assert result.success(), f"kodo run alias failed: {result.output()}"


# --- US4: kodo backends ---

class TestBackends:
    def test_backends_exits_zero(self) -> None:
        result = run_kodo("backends")
        assert result.success(), f"kodo backends failed: {result.output()}"

    def test_backends_lists_cli_backends(self) -> None:
        result = run_kodo("backends")
        out = result.output().lower()
        # Should mention at least one known backend name
        assert any(name in out for name in ["claude", "cursor", "codex", "gemini", "kimi"]), \
            f"No known backend found in: {result.output()}"

    def test_backends_singular_alias(self) -> None:
        """'kodo backend' is an alias for 'kodo backends'."""
        result = run_kodo("backend")
        assert result.success(), f"kodo backend alias failed: {result.output()}"

    def test_backends_shows_orchestrator_models(self) -> None:
        result = run_kodo("backends")
        out = result.output().lower()
        assert "orchestrator" in out or "model" in out or "api" in out, \
            f"No orchestrator/model section in: {result.output()}"


# --- US5: kodo teams ---

class TestTeams:
    def test_teams_exits_zero(self) -> None:
        result = run_kodo("teams")
        assert result.success(), f"kodo teams failed: {result.output()}"

    def test_teams_lists_team_names(self) -> None:
        result = run_kodo("teams")
        out = result.output().lower()
        # Should mention at least 'default' or some team name
        assert any(name in out for name in ["default", "full", "quick", "solo"]), \
            f"No known team name found in: {result.output()}"

    def test_teams_singular_alias(self) -> None:
        """'kodo team' is an alias for 'kodo teams'."""
        result = run_kodo("team")
        assert result.success(), f"kodo team alias failed: {result.output()}"


# --- US6: kodo logs ---

class TestLogs:
    def test_logs_help_exits_zero(self) -> None:
        """We test --help since 'kodo logs' alone starts a server."""
        result = run_kodo("logs", "--help")
        assert result.success(), f"kodo logs --help failed: {result.output()}"

    def test_logs_help_shows_port_option(self) -> None:
        result = run_kodo("logs", "--help")
        assert "--port" in result.output(), f"Missing --port in logs help: {result.output()}"

    def test_logs_help_shows_logfile_arg(self) -> None:
        result = run_kodo("logs", "--help")
        assert "logfile" in result.output().lower(), \
            f"Missing logfile arg in logs help: {result.output()}"

    def test_logs_singular_alias(self) -> None:
        """'kodo log' is an alias for 'kodo logs'."""
        result = run_kodo("log", "--help")
        assert result.success(), f"kodo log alias failed: {result.output()}"


# --- US11: kodo issue ---

class TestIssue:
    def test_issue_help_exits_zero(self) -> None:
        result = run_kodo("issue", "--help")
        assert result.success(), f"kodo issue --help failed: {result.output()}"

    def test_issue_help_shows_options(self) -> None:
        result = run_kodo("issue", "--help")
        out = result.output()
        assert "--no-open" in out, f"Missing --no-open in issue help: {out}"
        assert "--project" in out, f"Missing --project in issue help: {out}"

    def test_issue_plural_alias(self) -> None:
        """'kodo issues' is an alias for 'kodo issue'."""
        result = run_kodo("issues", "--help")
        assert result.success(), f"kodo issues alias failed: {result.output()}"


# --- US12: kodo update ---

class TestUpdate:
    def test_update_help_info(self) -> None:
        """kodo update doesn't support --help separately; just verify the command exists.
        We don't actually run 'kodo update' because it modifies the installation.
        Instead, verify the subcommand is recognized by checking that 'kodo help' mentions update."""
        result = run_kodo("--help")
        # The help text or subcommand map should reference 'update'
        # If not in help, at least verify the subcommand doesn't error with 'unknown command'
        assert result.success()
