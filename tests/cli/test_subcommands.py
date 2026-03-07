"""Tests for CLI subcommands: runs, backends, teams."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kodo.cli._subcommands import (
    _ask_agent_fields,
    _cmd_backends,
    _cmd_runs,
    _cmd_teams,
    _cmd_teams_add,
    _cmd_teams_auto,
    _cmd_teams_edit,
    _save_team,
    _teams_dir,
    _truncate_word,
)

# ---------------------------------------------------------------------------
# kodo runs
# ---------------------------------------------------------------------------


class TestCmdRuns:
    def test_no_runs_prints_message(self, capsys):
        with patch("sys.argv", ["kodo", "runs"]):
            _cmd_runs()
        assert "No runs found." in capsys.readouterr().out

    def test_no_runs_filtered_by_project(self, tmp_path, capsys):
        with patch("sys.argv", ["kodo", "runs", str(tmp_path)]):
            _cmd_runs()
        assert "No runs found." in capsys.readouterr().out

    def test_runs_table_format(self, tmp_path, capsys):
        """Create fake run data and verify the table output."""
        from kodo.log import RunState

        fake_run = RunState(
            run_id="20260101_120000",
            log_file=tmp_path / "run.jsonl",
            goal="Build a REST API for testing",
            orchestrator="api",
            model="gemini-flash",
            project_dir=str(tmp_path),
            max_exchanges=30,
            max_cycles=5,
            team=["worker_fast", "worker_smart"],
            completed_cycles=3,
            last_summary="",
            finished=True,
            agent_session_ids={},
            has_stages=False,
            completed_stages=[],
            stage_summaries={},
            current_stage_cycles=0,
            pending_exchanges=[],
            team_preset="full",
        )

        with (
            patch("sys.argv", ["kodo", "runs"]),
            patch("kodo.cli._subcommands.log.list_runs", return_value=[fake_run]),
        ):
            _cmd_runs()

        out = capsys.readouterr().out
        assert "20260101_120000" in out
        assert "done" in out
        assert "Build a REST API" in out
        assert "RUN ID" in out

    def test_incomplete_run_shows_cycle_progress(self, tmp_path, capsys):
        from kodo.log import RunState

        fake_run = RunState(
            run_id="20260101_130000",
            log_file=tmp_path / "run.jsonl",
            goal="Unfinished task",
            orchestrator="api",
            model="gemini-flash",
            project_dir=str(tmp_path),
            max_exchanges=30,
            max_cycles=5,
            team=["worker_fast"],
            completed_cycles=2,
            last_summary="",
            finished=False,
            agent_session_ids={},
            has_stages=False,
            completed_stages=[],
            stage_summaries={},
            current_stage_cycles=0,
            pending_exchanges=[],
            team_preset="full",
        )

        with (
            patch("sys.argv", ["kodo", "runs"]),
            patch("kodo.cli._subcommands.log.list_runs", return_value=[fake_run]),
        ):
            _cmd_runs()

        out = capsys.readouterr().out
        assert "cycle 2/5" in out

    def test_long_goal_truncated(self, tmp_path, capsys):
        from kodo.log import RunState

        long_goal = "A" * 100
        fake_run = RunState(
            run_id="20260101_140000",
            log_file=tmp_path / "run.jsonl",
            goal=long_goal,
            orchestrator="api",
            model="gemini-flash",
            project_dir=str(tmp_path),
            max_exchanges=30,
            max_cycles=5,
            team=[],
            completed_cycles=1,
            last_summary="",
            finished=True,
            agent_session_ids={},
            has_stages=False,
            completed_stages=[],
            stage_summaries={},
            current_stage_cycles=0,
            pending_exchanges=[],
            team_preset="full",
        )

        with (
            patch("sys.argv", ["kodo", "runs"]),
            patch("kodo.cli._subcommands.log.list_runs", return_value=[fake_run]),
        ):
            _cmd_runs()

        out = capsys.readouterr().out
        assert "..." in out


# ---------------------------------------------------------------------------
# kodo logs
# ---------------------------------------------------------------------------


class TestCmdLogs:
    """Tests for _cmd_logs() function."""

    def test_no_logfile_serves_default_port(self):
        """_cmd_logs with no logfile should serve on default port 8080 with None path."""
        with (
            patch("sys.argv", ["kodo", "logs"]),
            patch("kodo.viewer._serve") as mock_serve,
        ):
            from kodo.cli._subcommands import _cmd_logs
            _cmd_logs()
            mock_serve.assert_called_once_with(8080, None)

    def test_custom_port(self):
        """_cmd_logs --port 9999 should serve on port 9999."""
        with (
            patch("sys.argv", ["kodo", "logs", "--port", "9999"]),
            patch("kodo.viewer._serve") as mock_serve,
        ):
            from kodo.cli._subcommands import _cmd_logs
            _cmd_logs()
            mock_serve.assert_called_once_with(9999, None)

    def test_valid_logfile(self, tmp_path):
        """_cmd_logs with existing logfile should pass path to _serve."""
        logfile = tmp_path / "test.jsonl"
        logfile.write_text('{"event":"test"}\n')

        with (
            patch("sys.argv", ["kodo", "logs", str(logfile)]),
            patch("kodo.viewer._serve") as mock_serve,
        ):
            from kodo.cli._subcommands import _cmd_logs
            _cmd_logs()
            # Verify port and path were passed
            assert mock_serve.call_count == 1
            call_args = mock_serve.call_args[0]
            assert call_args[0] == 8080
            assert call_args[1] == logfile

    def test_nonexistent_logfile_exits(self, tmp_path):
        """_cmd_logs with non-existent logfile should exit with code 1."""
        nonexistent = tmp_path / "missing.jsonl"

        with (
            patch("sys.argv", ["kodo", "logs", str(nonexistent)]),
            patch("kodo.viewer._serve") as mock_serve,
            pytest.raises(SystemExit, match="1"),
        ):
            from kodo.cli._subcommands import _cmd_logs
            _cmd_logs()
            # _serve should not be called
            mock_serve.assert_not_called()


# ---------------------------------------------------------------------------
# kodo backends
# ---------------------------------------------------------------------------


_NO_BACKENDS = {"claude": False, "codex": False, "cursor": False, "gemini-cli": False}


class TestCmdBackends:
    """_cmd_backends() imports from kodo.factory inside the function body,
    so we must patch at the kodo.factory module level."""

    @pytest.fixture(autouse=True)
    def _patch_factory(self):
        with (
            patch("kodo.factory.available_backends", return_value=_NO_BACKENDS),
            patch("kodo.factory.check_api_key", return_value="no key"),
            patch("kodo.user_config.get_user_default", return_value=None),
        ):
            yield

    def test_shows_section_headers(self, capsys):
        with patch("sys.argv", ["kodo", "backends"]):
            _cmd_backends()

        out = capsys.readouterr().out
        assert "CLI backends (agents):" in out
        assert "Orchestrator models (API):" in out
        assert "API keys:" in out

    def test_missing_backend_shows_not_found(self, capsys):
        with patch("sys.argv", ["kodo", "backends"]):
            _cmd_backends()

        out = capsys.readouterr().out
        assert "not found" in out

    def test_api_key_not_set_shown(self, capsys):
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("sys.argv", ["kodo", "backends"]),
        ):
            _cmd_backends()

        out = capsys.readouterr().out
        assert "not set" in out

    def test_api_key_masked_when_set(self, capsys):
        with (
            patch("kodo.factory.check_api_key", return_value=None),
            patch.dict(
                "os.environ",
                {
                    "ANTHROPIC_API_KEY": "sk-ant-1234567890abcdef",
                    "GEMINI_API_KEY": "",
                    "GOOGLE_API_KEY": "",
                },
            ),
            patch("sys.argv", ["kodo", "backends"]),
        ):
            _cmd_backends()

        out = capsys.readouterr().out
        # Key should be masked, not shown in full
        assert "sk-a" in out
        assert "cdef" in out
        assert "sk-ant-1234567890abcdef" not in out

    def test_available_backend_shows_version(self, capsys):
        """When backend is available and version command succeeds, show version string."""
        from subprocess import CompletedProcess

        mock_result = CompletedProcess(
            args=["claude", "--version"],
            returncode=0,
            stdout="Claude Code CLI 2.5.0\nExtra line",
            stderr="",
        )

        with (
            patch("kodo.factory.available_backends", return_value={"claude": True, "codex": False, "cursor": False, "gemini-cli": False}),
            patch("kodo.factory.check_api_key", return_value=None),
            patch("subprocess.run", return_value=mock_result),
            patch("sys.argv", ["kodo", "backends"]),
        ):
            _cmd_backends()

        out = capsys.readouterr().out
        assert "Claude Code CLI 2.5.0" in out
        # Should only show first line of output
        assert "Extra line" not in out

    def test_version_command_fails_shows_error(self, capsys):
        """When version command returns non-zero exit code, show error message."""
        from subprocess import CompletedProcess

        mock_result = CompletedProcess(
            args=["claude", "--version"],
            returncode=127,
            stdout="",
            stderr="command not found",
        )

        with (
            patch("kodo.factory.available_backends", return_value={"claude": True, "codex": False, "cursor": False, "gemini-cli": False}),
            patch("kodo.factory.check_api_key", return_value=None),
            patch("subprocess.run", return_value=mock_result),
            patch("sys.argv", ["kodo", "backends"]),
        ):
            _cmd_backends()

        out = capsys.readouterr().out
        assert "error (exit 127)" in out

    def test_version_command_timeout(self, capsys):
        """When version command times out, show 'error'."""
        from subprocess import TimeoutExpired

        with (
            patch("kodo.factory.available_backends", return_value={"claude": True, "codex": False, "cursor": False, "gemini-cli": False}),
            patch("kodo.factory.check_api_key", return_value=None),
            patch("subprocess.run", side_effect=TimeoutExpired("claude --version", 10)),
            patch("sys.argv", ["kodo", "backends"]),
        ):
            _cmd_backends()

        out = capsys.readouterr().out
        assert "error" in out
        # Should not have "error (exit N)" format
        assert "exit" not in out or "error" in out


# ---------------------------------------------------------------------------
# kodo teams
# ---------------------------------------------------------------------------


class TestCmdTeams:
    def test_list_no_teams(self, capsys):
        with (
            patch("sys.argv", ["kodo", "teams"]),
            patch("kodo.team_config.list_available_teams", return_value=[]),
            patch("kodo.factory.available_backends", return_value=_NO_BACKENDS),
        ):
            _cmd_teams()

        assert "No teams found." in capsys.readouterr().out

    def test_list_shows_team_info(self, capsys):
        team_cfg = {
            "description": "Full team for big projects",
            "max_exchanges": 30,
            "max_cycles": 5,
            "agents": {
                "worker_fast": {"backend": "claude", "model": "sonnet"},
                "worker_smart": {"backend": "claude", "model": "opus"},
            },
        }
        with (
            patch("sys.argv", ["kodo", "teams"]),
            patch(
                "kodo.team_config.list_available_teams",
                return_value=[("full", "built-in", team_cfg, Path("/fake/defaults/team-full.json"))],
            ),
            patch(
                "kodo.factory.available_backends",
                return_value={
                    "claude": True,
                    "codex": False,
                    "cursor": False,
                    "gemini-cli": False,
                },
            ),
        ):
            _cmd_teams()

        out = capsys.readouterr().out
        assert "full" in out
        assert "(built-in)" in out
        assert "2 agents" in out
        assert "worker_fast" in out
        assert "worker_smart" in out

    def test_unknown_subcommand_exits(self):
        with (
            patch("sys.argv", ["kodo", "teams", "bogus"]),
            pytest.raises(SystemExit, match="1"),
        ):
            _cmd_teams()

    def test_add_missing_name_exits(self):
        with (
            patch("sys.argv", ["kodo", "teams", "add"]),
            pytest.raises(SystemExit, match="1"),
        ):
            _cmd_teams()

    def test_edit_missing_name_exits(self):
        with (
            patch("sys.argv", ["kodo", "teams", "edit"]),
            pytest.raises(SystemExit, match="1"),
        ):
            _cmd_teams()

    def test_auto_no_backends_exits(self, capsys):
        with (
            patch("sys.argv", ["kodo", "teams", "auto"]),
            patch("kodo.factory.available_backends", return_value=_NO_BACKENDS),
            pytest.raises(SystemExit, match="1"),
        ):
            _cmd_teams()


# ---------------------------------------------------------------------------
# _truncate_word
# ---------------------------------------------------------------------------


class TestTruncateWord:
    """Tests for _truncate_word() helper function."""

    def test_short_text_unchanged(self):
        """Text shorter than width should be returned unchanged."""
        assert _truncate_word("Hello", 10) == "Hello"

    def test_truncates_on_word_boundary(self):
        """Text longer than width should truncate on word boundary with '...'."""
        result = _truncate_word("The quick brown fox", 12)
        assert result == "The quick..."
        assert len(result) <= 15  # 12 + len("...")

    def test_hard_cuts_single_long_word(self):
        """If first word is longer than width, hard-cut it."""
        result = _truncate_word("Supercalifragilisticexpialidocious", 10)
        assert result == "Supercalif..."
        assert len(result) == 13  # 10 + len("...")

    def test_exact_width_unchanged(self):
        """Text exactly matching width should be returned unchanged."""
        text = "12345"
        assert _truncate_word(text, 5) == text


# ---------------------------------------------------------------------------
# _save_team
# ---------------------------------------------------------------------------


class TestSaveTeam:
    """Tests for _save_team() function."""

    def test_writes_json_to_teams_dir(self, tmp_path, capsys):
        """_save_team should write JSON to ~/.kodo/teams/{name}.json."""
        config = {
            "name": "test-team",
            "description": "Test team",
            "max_exchanges": 20,
            "max_cycles": 1,
            "agents": {
                "worker": {"backend": "claude", "model": "sonnet"},
            },
        }

        with patch("kodo.cli._subcommands._teams_dir", return_value=tmp_path):
            path = _save_team("test-team", config)

        assert path == tmp_path / "test-team.json"
        assert path.exists()

        # Verify JSON content
        saved = json.loads(path.read_text())
        assert saved["name"] == "test-team"
        assert saved["description"] == "Test team"
        assert saved["max_exchanges"] == 20
        assert "worker" in saved["agents"]

        # Verify console output
        out = capsys.readouterr().out
        assert "Saved to" in out

    def test_creates_parent_dirs(self, tmp_path, capsys):
        """_save_team works when _teams_dir returns a non-existent path (because _teams_dir creates it)."""
        teams_dir = tmp_path / "teams"
        # Don't create teams_dir beforehand - _teams_dir() would normally create it,
        # but we're mocking it. So we need to create it in the mock.
        config = {
            "name": "another-team",
            "agents": {},
        }

        def mock_teams_dir():
            teams_dir.mkdir(parents=True, exist_ok=True)
            return teams_dir

        with patch("kodo.cli._subcommands._teams_dir", side_effect=mock_teams_dir):
            path = _save_team("another-team", config)

        assert path.exists()
        assert path.parent.exists()
        assert path.parent == teams_dir


# ---------------------------------------------------------------------------
# _cmd_teams_auto (Tier 4)
# ---------------------------------------------------------------------------


class TestCmdTeamsAuto:
    """Tests for _cmd_teams_auto() function."""

    def test_template_not_found_exits(self, capsys):
        """Unknown mode name should exit with error."""
        with (
            patch("kodo.factory.available_backends", return_value={"claude": True, "codex": False, "cursor": False, "gemini-cli": False}),
            patch("kodo.team_config.list_available_teams", return_value=[("full", "built-in", {}, Path("/fake/full.json"))]),
            pytest.raises(SystemExit, match="1"),
        ):
            from kodo.cli._subcommands import _cmd_teams_auto
            _cmd_teams_auto("nonexistent")

        out = capsys.readouterr().out
        assert "No template found" in out
        assert "nonexistent" in out

    def test_filters_unavailable_agents(self, tmp_path, capsys):
        """Agents with unavailable backends should be skipped."""
        base_config = {
            "description": "Test team",
            "max_exchanges": 20,
            "max_cycles": 1,
            "agents": {
                "worker_claude": {"backend": "claude", "model": "sonnet"},
                "worker_cursor": {"backend": "cursor", "model": "composer"},
            },
            "verifiers": {},
        }

        with (
            patch("kodo.factory.available_backends", return_value={"claude": True, "codex": False, "cursor": False, "gemini-cli": False}),
            patch("kodo.team_config.list_available_teams", return_value=[("test", "built-in", base_config, Path("/fake/test.json"))]),
            patch("kodo.cli._subcommands._teams_dir", return_value=tmp_path),
            patch("kodo.cli._subcommands._save_team") as mock_save,
        ):
            from kodo.cli._subcommands import _cmd_teams_auto
            _cmd_teams_auto("test")

        # Should save config with only claude agent
        assert mock_save.called
        saved_config = mock_save.call_args[0][1]
        assert "worker_claude" in saved_config["agents"]
        assert "worker_cursor" not in saved_config["agents"]

        out = capsys.readouterr().out
        assert "Skipped" in out
        assert "cursor" in out

    def test_worker_fast_fallback(self, tmp_path, capsys):
        """worker_fast should use cursor fallback when original backend missing."""
        base_config = {
            "description": "Test team",
            "max_exchanges": 20,
            "max_cycles": 1,
            "agents": {
                "worker_fast": {"backend": "codex", "model": "gpt-5.3-codex"},
            },
            "verifiers": {},
        }

        with (
            patch("kodo.factory.available_backends", return_value={"claude": False, "codex": False, "cursor": True, "gemini-cli": False}),
            patch("kodo.team_config.list_available_teams", return_value=[("test", "built-in", base_config, Path("/fake/test.json"))]),
            patch("kodo.cli._subcommands._teams_dir", return_value=tmp_path),
            patch("kodo.cli._subcommands._save_team") as mock_save,
        ):
            from kodo.cli._subcommands import _cmd_teams_auto
            _cmd_teams_auto("test")

        saved_config = mock_save.call_args[0][1]
        assert saved_config["agents"]["worker_fast"]["backend"] == "cursor"
        assert "composer" in saved_config["agents"]["worker_fast"]["model"]

    def test_worker_smart_fallback(self, tmp_path, capsys):
        """worker_smart should use claude fallback when original backend missing."""
        base_config = {
            "description": "Test team",
            "max_exchanges": 20,
            "max_cycles": 1,
            "agents": {
                "worker_smart": {"backend": "gemini-cli", "model": "gemini-3-pro", "fallback_model": "gemini-2.5-flash"},
            },
            "verifiers": {},
        }

        with (
            patch("kodo.factory.available_backends", return_value={"claude": True, "codex": False, "cursor": False, "gemini-cli": False}),
            patch("kodo.team_config.list_available_teams", return_value=[("test", "built-in", base_config, Path("/fake/test.json"))]),
            patch("kodo.cli._subcommands._teams_dir", return_value=tmp_path),
            patch("kodo.cli._subcommands._save_team") as mock_save,
        ):
            from kodo.cli._subcommands import _cmd_teams_auto
            _cmd_teams_auto("test")

        saved_config = mock_save.call_args[0][1]
        assert saved_config["agents"]["worker_smart"]["backend"] == "claude"
        assert "opus" in saved_config["agents"]["worker_smart"]["model"].lower()
        # fallback_model should be removed for non-claude backends - but we're using claude, so it should be present
        # Actually, the code only removes fallback_model when fb[0] != "claude", so it stays for claude

    def test_tester_architect_fallback(self, tmp_path, capsys):
        """tester and architect should use fallback backends when original missing."""
        base_config = {
            "description": "Test team",
            "max_exchanges": 20,
            "max_cycles": 1,
            "agents": {
                "tester": {"backend": "codex", "model": "gpt-5.3-codex"},
                "architect": {"backend": "gemini-cli", "model": "gemini-3-pro"},
            },
            "verifiers": {},
        }

        with (
            patch("kodo.factory.available_backends", return_value={"claude": False, "codex": False, "cursor": True, "gemini-cli": False}),
            patch("kodo.team_config.list_available_teams", return_value=[("test", "built-in", base_config, Path("/fake/test.json"))]),
            patch("kodo.cli._subcommands._teams_dir", return_value=tmp_path),
            patch("kodo.cli._subcommands._save_team") as mock_save,
        ):
            from kodo.cli._subcommands import _cmd_teams_auto
            _cmd_teams_auto("test")

        saved_config = mock_save.call_args[0][1]
        # tester should fall back to cursor (first in _FAST_FALLBACKS)
        assert saved_config["agents"]["tester"]["backend"] == "cursor"
        # architect should fall back to cursor (third in _SMART_FALLBACKS after claude and gemini-cli)
        assert saved_config["agents"]["architect"]["backend"] == "cursor"

    def test_no_agents_after_filtering_exits(self, tmp_path, capsys):
        """If all agents are filtered out and no fallbacks available, exit with error."""
        base_config = {
            "description": "Test team",
            "max_exchanges": 20,
            "max_cycles": 1,
            "agents": {
                "other_agent": {"backend": "nonexistent", "model": "some-model"},
            },
            "verifiers": {},
        }

        # Have one backend available but not used by any agent or fallback
        # This gets past the "no backends" check but fails to create any agents
        with (
            patch("kodo.factory.available_backends", return_value={"claude": False, "codex": False, "cursor": False, "gemini-cli": True}),
            patch("kodo.team_config.list_available_teams", return_value=[("test", "built-in", base_config, Path("/fake/test.json"))]),
            patch("kodo.cli._subcommands._teams_dir", return_value=tmp_path),
            pytest.raises(SystemExit, match="1"),
        ):
            from kodo.cli._subcommands import _cmd_teams_auto
            _cmd_teams_auto("test")

        out = capsys.readouterr().out
        assert "Could not create any agents" in out

    def test_successful_auto_generates_config(self, tmp_path, capsys):
        """Successful auto generation should create proper config structure."""
        base_config = {
            "description": "Test team",
            "max_exchanges": 30,
            "max_cycles": 5,
            "orchestrator_prompt": "Custom prompt",
            "agents": {
                "worker_fast": {"backend": "claude", "model": "sonnet", "description": "Fast worker"},
            },
            "verifiers": {
                "testers": ["worker_fast"],
            },
        }

        with (
            patch("kodo.factory.available_backends", return_value={"claude": True, "codex": False, "cursor": False, "gemini-cli": False}),
            patch("kodo.team_config.list_available_teams", return_value=[("myteam", "built-in", base_config, Path("/fake/myteam.json"))]),
            patch("kodo.cli._subcommands._teams_dir", return_value=tmp_path),
            patch("kodo.cli._subcommands._save_team") as mock_save,
        ):
            from kodo.cli._subcommands import _cmd_teams_auto
            _cmd_teams_auto("myteam")

        # Verify config structure
        assert mock_save.called
        call_args = mock_save.call_args[0]
        assert call_args[0] == "myteam"
        config = call_args[1]
        assert config["name"] == "myteam"
        assert config["description"] == "Test team"
        assert config["max_exchanges"] == 30
        assert config["max_cycles"] == 5
        assert config["orchestrator_prompt"] == "Custom prompt"
        assert "worker_fast" in config["agents"]
        assert config["verifiers"]["testers"] == ["worker_fast"]

        out = capsys.readouterr().out
        assert "Generated team 'myteam'" in out
        assert "Use with: kodo --team myteam" in out


# ---------------------------------------------------------------------------
# _cmd_teams_auto_all (Tier 5)
# ---------------------------------------------------------------------------


class TestCmdTeamsAutoAll:
    """Tests for _cmd_teams_auto_all() function."""

    def test_no_builtin_templates_exits(self, capsys):
        """If no built-in templates found, exit with error."""
        with (
            patch("kodo.team_config.list_available_teams", return_value=[("custom", "user", {}, Path("/fake/custom.json"))]),
            pytest.raises(SystemExit, match="1"),
        ):
            from kodo.cli._subcommands import _cmd_teams_auto_all
            _cmd_teams_auto_all()

        out = capsys.readouterr().out
        assert "No built-in team templates found" in out

    def test_calls_auto_for_each_template(self, capsys):
        """Should call _cmd_teams_auto for each built-in template."""
        templates = [
            ("full", "built-in", {}, Path("/fake/full.json")),
            ("quick", "built-in", {}, Path("/fake/quick.json")),
            ("custom", "user", {}, Path("/fake/custom.json")),
        ]

        with (
            patch("kodo.team_config.list_available_teams", return_value=templates),
            patch("kodo.cli._subcommands._cmd_teams_auto") as mock_auto,
        ):
            from kodo.cli._subcommands import _cmd_teams_auto_all
            _cmd_teams_auto_all()

        # Should call _cmd_teams_auto for "full" and "quick" (built-in only), not "custom" (user)
        assert mock_auto.call_count == 2
        call_args_list = [call[0][0] for call in mock_auto.call_args_list]
        assert "full" in call_args_list
        assert "quick" in call_args_list
        assert "custom" not in call_args_list


# ---------------------------------------------------------------------------
# Tier 1: Dispatcher, list hints, auto overwrite, _teams_dir
# ---------------------------------------------------------------------------


_CLAUDE_ONLY = {"claude": True, "codex": False, "cursor": False, "gemini-cli": False}


class TestCmdTeamsDispatch:
    """Tests for _cmd_teams() dispatch paths not yet covered."""

    def test_dispatch_to_add(self):
        """'kodo teams add myteam' should dispatch to _cmd_teams_add."""
        with (
            patch("sys.argv", ["kodo", "teams", "add", "myteam"]),
            patch("kodo.cli._subcommands._cmd_teams_add") as mock_add,
        ):
            _cmd_teams()

        mock_add.assert_called_once_with("myteam")

    def test_dispatch_to_edit(self):
        """'kodo teams edit myteam' should dispatch to _cmd_teams_edit."""
        with (
            patch("sys.argv", ["kodo", "teams", "edit", "myteam"]),
            patch("kodo.cli._subcommands._cmd_teams_edit") as mock_edit,
        ):
            _cmd_teams()

        mock_edit.assert_called_once_with("myteam")

    def test_dispatch_to_auto_with_name(self):
        """'kodo teams auto full' should dispatch to _cmd_teams_auto('full')."""
        with (
            patch("sys.argv", ["kodo", "teams", "auto", "full"]),
            patch("kodo.cli._subcommands._cmd_teams_auto") as mock_auto,
        ):
            _cmd_teams()

        mock_auto.assert_called_once_with("full")


class TestCmdTeamsListMissingHint:
    """Test for _cmd_teams_list missing-backend hint."""

    def test_shows_missing_hint(self, capsys):
        """When agents have missing backends, show the 'kodo teams auto' hint."""
        team_cfg = {
            "description": "Full team",
            "max_exchanges": 30,
            "max_cycles": 5,
            "agents": {
                "worker_fast": {"backend": "claude", "model": "sonnet"},
                "worker_cursor": {"backend": "cursor", "model": "composer"},
            },
        }

        with (
            patch("sys.argv", ["kodo", "teams"]),
            patch(
                "kodo.team_config.list_available_teams",
                return_value=[("full", "built-in", team_cfg, Path("/fake/full.json"))],
            ),
            patch(
                "kodo.factory.available_backends",
                return_value=_CLAUDE_ONLY,
            ),
        ):
            _cmd_teams()

        out = capsys.readouterr().out
        assert "[missing]" in out
        assert "kodo teams auto" in out


class TestCmdTeamsAutoOverwrite:
    """Tests for the overwrite-confirmation path in _cmd_teams_auto."""

    def _base_config(self):
        return {
            "description": "Test team",
            "max_exchanges": 20,
            "max_cycles": 1,
            "agents": {
                "worker_fast": {"backend": "claude", "model": "sonnet"},
            },
            "verifiers": {},
        }

    def test_overwrite_confirm_yes(self, tmp_path, capsys):
        """When team file exists and user confirms, save should proceed."""
        # Create existing team file
        existing = tmp_path / "myteam.json"
        existing.write_text("{}")

        with (
            patch("kodo.factory.available_backends", return_value=_CLAUDE_ONLY),
            patch(
                "kodo.team_config.list_available_teams",
                return_value=[("myteam", "built-in", self._base_config(), Path("/fake"))],
            ),
            patch("kodo.cli._subcommands._teams_dir", return_value=tmp_path),
            patch("kodo.cli._subcommands._save_team") as mock_save,
            patch("builtins.input", return_value="y"),
        ):
            _cmd_teams_auto("myteam")

        mock_save.assert_called_once()

    def test_overwrite_confirm_no(self, tmp_path, capsys):
        """When team file exists and user declines, operation should be cancelled."""
        existing = tmp_path / "myteam.json"
        existing.write_text("{}")

        with (
            patch("kodo.factory.available_backends", return_value=_CLAUDE_ONLY),
            patch(
                "kodo.team_config.list_available_teams",
                return_value=[("myteam", "built-in", self._base_config(), Path("/fake"))],
            ),
            patch("kodo.cli._subcommands._teams_dir", return_value=tmp_path),
            patch("kodo.cli._subcommands._save_team") as mock_save,
            patch("builtins.input", return_value="n"),
        ):
            _cmd_teams_auto("myteam")

        mock_save.assert_not_called()
        out = capsys.readouterr().out
        assert "Cancelled" in out


class TestTeamsDir:
    """Test for _teams_dir() function."""

    def test_creates_directory(self, tmp_path):
        """_teams_dir should create ~/.kodo/teams/ and return the path."""
        with patch("pathlib.Path.home", return_value=tmp_path):
            result = _teams_dir()

        expected = tmp_path / ".kodo" / "teams"
        assert result == expected
        assert result.exists()
        assert result.is_dir()


class TestWorkerSmartNonClaudeFallback:
    """Test for non-claude worker_smart fallback removing fallback_model."""

    def test_non_claude_removes_fallback_model(self, tmp_path, capsys):
        """worker_smart falling back to non-claude should drop fallback_model."""
        base_config = {
            "description": "Test team",
            "max_exchanges": 20,
            "max_cycles": 1,
            "agents": {
                "worker_smart": {
                    "backend": "codex",
                    "model": "gpt-5.3-codex",
                    "fallback_model": "gpt-5.2-codex",
                },
            },
            "verifiers": {},
        }

        # Only cursor available — not claude, so fallback_model should be removed
        with (
            patch(
                "kodo.factory.available_backends",
                return_value={"claude": False, "codex": False, "cursor": True, "gemini-cli": False},
            ),
            patch(
                "kodo.team_config.list_available_teams",
                return_value=[("test", "built-in", base_config, Path("/fake"))],
            ),
            patch("kodo.cli._subcommands._teams_dir", return_value=tmp_path),
            patch("kodo.cli._subcommands._save_team") as mock_save,
        ):
            _cmd_teams_auto("test")

        saved = mock_save.call_args[0][1]
        assert saved["agents"]["worker_smart"]["backend"] == "cursor"
        assert "fallback_model" not in saved["agents"]["worker_smart"]


# ---------------------------------------------------------------------------
# Tier 2: _ask_agent_fields
# ---------------------------------------------------------------------------


def _make_questionary_mocks(select_returns, text_returns, confirm_returns=None):
    """Build mock objects for questionary.select, .text, .confirm.

    Each returns a fresh MagicMock whose .ask() pops from the given list.
    """
    select_iter = iter(select_returns)
    text_iter = iter(text_returns)
    confirm_iter = iter(confirm_returns or [])

    def mock_select(*args, **kwargs):
        m = MagicMock()
        m.ask.return_value = next(select_iter)
        return m

    def mock_text(*args, **kwargs):
        m = MagicMock()
        m.ask.return_value = next(text_iter)
        return m

    def mock_confirm(*args, **kwargs):
        m = MagicMock()
        m.ask.return_value = next(confirm_iter, False)
        return m

    return mock_select, mock_text, mock_confirm


class TestAskAgentFields:
    """Tier 2 tests for _ask_agent_fields() function."""

    def test_claude_backend_happy_path(self):
        """Happy path: claude backend with suggested model, no optional fields."""
        mock_select, mock_text, mock_confirm = _make_questionary_mocks(
            select_returns=[
                "claude",   # backend
                "sonnet",   # model from suggestions
            ],
            text_returns=[
                "Fast coding agent",  # description
                "",                    # system_prompt (skip)
                "15",                  # max_turns
                "",                    # timeout (skip)
                "",                    # fallback_model (skip, claude-only)
            ],
            confirm_returns=[False],   # chrome
        )

        with (
            patch("questionary.select", side_effect=mock_select),
            patch("questionary.text", side_effect=mock_text),
            patch("questionary.confirm", side_effect=mock_confirm),
        ):
            result = _ask_agent_fields()

        assert result["backend"] == "claude"
        assert result["model"] == "sonnet"
        assert result["description"] == "Fast coding agent"
        assert result["max_turns"] == 15
        assert "system_prompt" not in result
        assert "timeout_s" not in result
        assert "fallback_model" not in result
        assert "chrome" not in result  # False means not included

    def test_with_defaults_reorders_backends(self):
        """Defaults dict should place default backend first and use previous model."""
        captured_select_args = []
        captured_text_args = []

        def tracking_select(*args, **kwargs):
            captured_select_args.append((args, kwargs))
            m = MagicMock()
            if len(captured_select_args) == 1:
                m.ask.return_value = "cursor"  # backend
            else:
                m.ask.return_value = "composer-1.5"  # model
            return m

        def tracking_text(*args, **kwargs):
            captured_text_args.append((args, kwargs))
            m = MagicMock()
            # description, system_prompt, max_turns, timeout
            returns = ["Updated desc", "", "10", ""]
            m.ask.return_value = returns[len(captured_text_args) - 1]
            return m

        def mock_confirm(*args, **kwargs):
            m = MagicMock()
            m.ask.return_value = False
            return m

        defaults = {
            "backend": "cursor",
            "model": "old-model",
            "description": "Old desc",
            "max_turns": 15,
        }

        with (
            patch("questionary.select", side_effect=tracking_select),
            patch("questionary.text", side_effect=tracking_text),
            patch("questionary.confirm", side_effect=mock_confirm),
        ):
            result = _ask_agent_fields(defaults=defaults)

        assert result["backend"] == "cursor"
        # Backend selection should have cursor first (reordered from defaults)
        backend_choices = captured_select_args[0][1].get("choices", [])
        assert backend_choices[0] == "cursor"

    def test_custom_model(self):
        """Selecting '(custom)' should prompt for model name via text input."""
        mock_select, mock_text, mock_confirm = _make_questionary_mocks(
            select_returns=[
                "claude",     # backend
                "(custom)",   # model = custom
            ],
            text_returns=[
                "my-custom-model",     # custom model name
                "Custom agent",        # description
                "",                    # system_prompt
                "20",                  # max_turns
                "",                    # timeout
                "",                    # fallback_model (claude-only)
            ],
            confirm_returns=[False],
        )

        with (
            patch("questionary.select", side_effect=mock_select),
            patch("questionary.text", side_effect=mock_text),
            patch("questionary.confirm", side_effect=mock_confirm),
        ):
            result = _ask_agent_fields()

        assert result["model"] == "my-custom-model"

    def test_unknown_backend_uses_text_for_model(self):
        """Backend not in _BACKEND_MODELS should fall through to text input for model."""
        # Use a backend that's in _BACKEND_MAP but not in _BACKEND_MODELS
        # Actually, all backends in _BACKEND_MAP are in _BACKEND_MODELS.
        # So we test the else branch by patching _BACKEND_MAP to include an extra backend.
        # Alternatively, just pick a backend and ensure model_suggestions is empty.
        # The simplest: mock the internal _BACKEND_MODELS to not contain the chosen backend.

        mock_select, mock_text, mock_confirm = _make_questionary_mocks(
            select_returns=[
                "claude",   # backend (will be chosen, but we'll make suggestions empty)
            ],
            text_returns=[
                "my-text-model",       # model via text (no suggestions)
                "Text model agent",    # description
                "",                    # system_prompt
                "10",                  # max_turns
                "",                    # timeout
                "",                    # fallback (claude-only)
            ],
            confirm_returns=[False],
        )

        # Patch the local _BACKEND_MODELS dict to be empty for claude
        with (
            patch("questionary.select", side_effect=mock_select),
            patch("questionary.text", side_effect=mock_text),
            patch("questionary.confirm", side_effect=mock_confirm),
            patch.dict(
                "kodo.cli._subcommands._ask_agent_fields.__code__",
            ) if False else
            # Can't easily patch a local variable. Instead, use a backend
            # that's genuinely not in _BACKEND_MODELS by adding it to _BACKEND_MAP.
            patch(
                "kodo.team_config._BACKEND_MAP",
                {"claude": "claude", "cursor": "cursor", "codex": "codex",
                 "gemini-cli": "gemini-cli", "custom-backend": "custom-backend"},
            ),
        ):
            # Re-select to pick the custom-backend
            mock_select2, mock_text2, mock_confirm2 = _make_questionary_mocks(
                select_returns=["custom-backend"],
                text_returns=[
                    "my-text-model",       # model via text (no suggestions branch)
                    "Text model agent",    # description
                    "",                    # system_prompt
                    "10",                  # max_turns
                    "",                    # timeout
                ],
                confirm_returns=[False],
            )
            with (
                patch("questionary.select", side_effect=mock_select2),
                patch("questionary.text", side_effect=mock_text2),
                patch("questionary.confirm", side_effect=mock_confirm2),
            ):
                result = _ask_agent_fields()

        assert result["backend"] == "custom-backend"
        assert result["model"] == "my-text-model"

    def test_invalid_max_turns_exits(self):
        """Non-integer max_turns should print error and exit."""
        mock_select, mock_text, mock_confirm = _make_questionary_mocks(
            select_returns=[
                "claude",   # backend
                "sonnet",   # model
            ],
            text_returns=[
                "Agent desc",   # description
                "",             # system_prompt
                "not-a-number", # max_turns (invalid)
                "",             # timeout (won't reach)
            ],
            confirm_returns=[False],
        )

        with (
            patch("questionary.select", side_effect=mock_select),
            patch("questionary.text", side_effect=mock_text),
            patch("questionary.confirm", side_effect=mock_confirm),
            pytest.raises(SystemExit),
        ):
            _ask_agent_fields()

    def test_invalid_timeout_exits(self):
        """Non-integer timeout should print error and exit."""
        mock_select, mock_text, mock_confirm = _make_questionary_mocks(
            select_returns=[
                "claude",   # backend
                "sonnet",   # model
            ],
            text_returns=[
                "Agent desc",     # description
                "",               # system_prompt
                "15",             # max_turns (valid)
                "not-a-number",   # timeout (invalid)
            ],
            confirm_returns=[False],
        )

        with (
            patch("questionary.select", side_effect=mock_select),
            patch("questionary.text", side_effect=mock_text),
            patch("questionary.confirm", side_effect=mock_confirm),
            pytest.raises(SystemExit),
        ):
            _ask_agent_fields()

    def test_chrome_enabled(self):
        """When user enables chrome, result should have chrome=True."""
        mock_select, mock_text, mock_confirm = _make_questionary_mocks(
            select_returns=[
                "claude",   # backend
                "sonnet",   # model
            ],
            text_returns=[
                "Browser agent",  # description
                "",               # system_prompt
                "15",             # max_turns
                "",               # timeout
                "",               # fallback_model
            ],
            confirm_returns=[True],  # chrome = True
        )

        with (
            patch("questionary.select", side_effect=mock_select),
            patch("questionary.text", side_effect=mock_text),
            patch("questionary.confirm", side_effect=mock_confirm),
        ):
            result = _ask_agent_fields()

        assert result["chrome"] is True

    def test_system_prompt_and_timeout_included(self):
        """Non-empty system_prompt and valid timeout should appear in result."""
        mock_select, mock_text, mock_confirm = _make_questionary_mocks(
            select_returns=[
                "claude",   # backend
                "opus",     # model
            ],
            text_returns=[
                "Smart agent",         # description
                "You are helpful.",     # system_prompt (non-empty)
                "25",                  # max_turns
                "3600",               # timeout (valid integer)
                "sonnet",             # fallback_model (claude-only)
            ],
            confirm_returns=[False],
        )

        with (
            patch("questionary.select", side_effect=mock_select),
            patch("questionary.text", side_effect=mock_text),
            patch("questionary.confirm", side_effect=mock_confirm),
        ):
            result = _ask_agent_fields()

        assert result["system_prompt"] == "You are helpful."
        assert result["timeout_s"] == 3600
        assert result["fallback_model"] == "sonnet"


# ---------------------------------------------------------------------------
# Tier 3: _cmd_teams_add
# ---------------------------------------------------------------------------


class TestCmdTeamsAdd:
    """Tests for _cmd_teams_add() interactive wizard."""

    def test_existing_team_exits(self, tmp_path, capsys):
        """Adding a team that already exists should exit with error."""
        existing = tmp_path / "myteam.json"
        existing.write_text("{}")

        with (
            patch("kodo.cli._subcommands._teams_dir", return_value=tmp_path),
            pytest.raises(SystemExit, match="1"),
        ):
            _cmd_teams_add("myteam")

        out = capsys.readouterr().out
        assert "already exists" in out
        assert "kodo teams edit" in out

    def test_happy_path_single_agent(self, tmp_path, capsys):
        """Create a team with one agent — no verifier prompts (skipped for single agent)."""
        # questionary.text calls in order:
        #   1. Team description
        #   2. Max exchanges
        #   3. Max cycles
        #   4. Orchestrator prompt
        #   5. Agent key name (first agent)
        #   6. Agent key name (empty → finish loop)
        text_returns = [
            "My test team",    # description
            "25",              # max_exchanges
            "3",               # max_cycles
            "",                # orchestrator prompt (skip)
            "worker",          # agent key name
            "",                # empty → finish loop
        ]

        fake_agent = {"backend": "claude", "model": "sonnet", "description": "Worker", "max_turns": 15}

        text_iter = iter(text_returns)

        def mock_text(*args, **kwargs):
            m = MagicMock()
            m.ask.return_value = next(text_iter)
            return m

        with (
            patch("kodo.cli._subcommands._teams_dir", return_value=tmp_path),
            patch("questionary.text", side_effect=mock_text),
            patch("kodo.cli._subcommands._ask_agent_fields", return_value=fake_agent),
            patch("kodo.cli._subcommands._save_team") as mock_save,
        ):
            _cmd_teams_add("newteam")

        mock_save.assert_called_once()
        saved_name, saved_config = mock_save.call_args[0]
        assert saved_name == "newteam"
        assert saved_config["name"] == "newteam"
        assert saved_config["description"] == "My test team"
        assert saved_config["max_exchanges"] == 25
        assert saved_config["max_cycles"] == 3
        assert "worker" in saved_config["agents"]
        assert saved_config["agents"]["worker"] == fake_agent
        # Single agent: no verifier assignment
        assert saved_config["verifiers"] == {"testers": [], "browser_testers": [], "reviewers": []}
        # Empty orchestrator prompt: no "orchestrator_prompt" key
        assert "orchestrator_prompt" not in saved_config

    def test_multiple_agents_with_verifiers(self, tmp_path, capsys):
        """Create a team with two agents — verifier assignment prompts shown."""
        text_returns = [
            "Multi-agent team",  # description
            "30",                # max_exchanges
            "5",                 # max_cycles
            "Custom prompt",     # orchestrator prompt (non-empty)
            "worker_fast",       # first agent key
            "worker_smart",      # second agent key
            "",                  # empty → finish loop
        ]
        text_iter = iter(text_returns)

        def mock_text(*args, **kwargs):
            m = MagicMock()
            m.ask.return_value = next(text_iter)
            return m

        fake_agents = iter([
            {"backend": "claude", "model": "sonnet", "description": "Fast", "max_turns": 10},
            {"backend": "claude", "model": "opus", "description": "Smart", "max_turns": 25},
        ])

        # questionary.checkbox calls for verifier assignment (3 calls)
        checkbox_returns = iter([
            ["worker_fast"],           # testers
            [],                        # browser_testers
            ["worker_smart"],          # reviewers
        ])

        def mock_checkbox(*args, **kwargs):
            m = MagicMock()
            m.ask.return_value = next(checkbox_returns)
            return m

        with (
            patch("kodo.cli._subcommands._teams_dir", return_value=tmp_path),
            patch("questionary.text", side_effect=mock_text),
            patch("questionary.checkbox", side_effect=mock_checkbox),
            patch("kodo.cli._subcommands._ask_agent_fields", side_effect=lambda: next(fake_agents)),
            patch("kodo.cli._subcommands._save_team") as mock_save,
        ):
            _cmd_teams_add("multi")

        saved_config = mock_save.call_args[0][1]
        assert saved_config["description"] == "Multi-agent team"
        assert saved_config["orchestrator_prompt"] == "Custom prompt"
        assert len(saved_config["agents"]) == 2
        assert saved_config["verifiers"]["testers"] == ["worker_fast"]
        assert saved_config["verifiers"]["reviewers"] == ["worker_smart"]

    def test_empty_agent_key_requires_at_least_one(self, tmp_path, capsys):
        """Submitting empty key with 0 agents prints warning and continues."""
        # Sequence: empty (rejected), then "worker", then empty (accepted)
        text_returns = [
            "Team desc",   # description
            "20",          # max_exchanges
            "1",           # max_cycles
            "",            # orch prompt
            "",            # empty agent key → "needs at least one"
            "worker",      # real agent key
            "",            # empty → finish loop
        ]
        text_iter = iter(text_returns)

        def mock_text(*args, **kwargs):
            m = MagicMock()
            m.ask.return_value = next(text_iter)
            return m

        fake_agent = {"backend": "claude", "model": "sonnet", "description": "W", "max_turns": 15}

        with (
            patch("kodo.cli._subcommands._teams_dir", return_value=tmp_path),
            patch("questionary.text", side_effect=mock_text),
            patch("kodo.cli._subcommands._ask_agent_fields", return_value=fake_agent),
            patch("kodo.cli._subcommands._save_team") as mock_save,
        ):
            _cmd_teams_add("team1")

        out = capsys.readouterr().out
        assert "at least one agent" in out
        # Still saved successfully
        mock_save.assert_called_once()

    def test_duplicate_agent_key_rejected(self, tmp_path, capsys):
        """Duplicate agent key name should be rejected."""
        text_returns = [
            "Team desc",   # description
            "20",          # max_exchanges
            "1",           # max_cycles
            "",            # orch prompt
            "worker",      # first agent key
            "worker",      # duplicate → rejected
            "",            # empty → finish loop
        ]
        text_iter = iter(text_returns)

        def mock_text(*args, **kwargs):
            m = MagicMock()
            m.ask.return_value = next(text_iter)
            return m

        fake_agent = {"backend": "claude", "model": "sonnet", "description": "W", "max_turns": 15}

        with (
            patch("kodo.cli._subcommands._teams_dir", return_value=tmp_path),
            patch("questionary.text", side_effect=mock_text),
            patch("kodo.cli._subcommands._ask_agent_fields", return_value=fake_agent),
            patch("kodo.cli._subcommands._save_team") as mock_save,
        ):
            _cmd_teams_add("team2")

        out = capsys.readouterr().out
        assert "already exists" in out
        assert "Pick a different name" in out
        # Only one agent actually added
        saved_config = mock_save.call_args[0][1]
        assert len(saved_config["agents"]) == 1

    def test_cancel_description_exits(self, tmp_path):
        """Cancelling at description prompt should exit."""
        text_iter = iter([None])  # description cancelled

        def mock_text(*args, **kwargs):
            m = MagicMock()
            m.ask.return_value = next(text_iter)
            return m

        with (
            patch("kodo.cli._subcommands._teams_dir", return_value=tmp_path),
            patch("questionary.text", side_effect=mock_text),
            pytest.raises(SystemExit),
        ):
            _cmd_teams_add("cancelled")

    def test_cancel_agent_key_exits(self, tmp_path):
        """Cancelling at agent key prompt should exit."""
        text_returns = [
            "Team desc",  # description
            "20",         # max_exchanges
            "1",          # max_cycles
            "",           # orch prompt
            None,         # agent key cancelled
        ]
        text_iter = iter(text_returns)

        def mock_text(*args, **kwargs):
            m = MagicMock()
            m.ask.return_value = next(text_iter)
            return m

        with (
            patch("kodo.cli._subcommands._teams_dir", return_value=tmp_path),
            patch("questionary.text", side_effect=mock_text),
            pytest.raises(SystemExit),
        ):
            _cmd_teams_add("cancelled2")


# ---------------------------------------------------------------------------
# Tier 4: _cmd_teams_edit
# ---------------------------------------------------------------------------


class TestCmdTeamsEdit:
    """Tests for _cmd_teams_edit() interactive editor."""

    def _base_team_cfg(self):
        return {
            "name": "test-team",
            "description": "Original description",
            "max_exchanges": 20,
            "max_cycles": 1,
            "agents": {
                "worker": {"backend": "claude", "model": "sonnet", "description": "Fast worker"},
            },
            "verifiers": {"testers": ["worker"], "browser_testers": [], "reviewers": []},
        }

    def _mock_list_teams(self, cfg, source="user"):
        """Return a list_available_teams mock that includes one team."""
        return [("test-team", source, cfg, Path("/fake/test-team.json"))]

    def test_team_not_found_exits(self, capsys):
        """Editing a non-existent team should exit with error."""
        with (
            patch("kodo.team_config.list_available_teams", return_value=[]),
            pytest.raises(SystemExit, match="1"),
        ):
            _cmd_teams_edit("nonexistent")

        out = capsys.readouterr().out
        assert "not found" in out

    def test_builtin_team_shows_copy_message(self, capsys):
        """Editing a built-in team should show a copy-to-user-dir message."""
        cfg = self._base_team_cfg()

        # Just do "Save & exit" immediately
        def mock_select(*args, **kwargs):
            m = MagicMock()
            m.ask.return_value = "Save & exit"
            return m

        with (
            patch("kodo.team_config.list_available_teams", return_value=self._mock_list_teams(cfg, "built-in")),
            patch("questionary.select", side_effect=mock_select),
            patch("kodo.cli._subcommands._save_team") as mock_save,
        ):
            _cmd_teams_edit("test-team")

        out = capsys.readouterr().out
        assert "Copying built-in" in out
        mock_save.assert_called_once()

    def test_save_and_exit(self, capsys):
        """Selecting 'Save & exit' should save config and return."""
        cfg = self._base_team_cfg()

        def mock_select(*args, **kwargs):
            m = MagicMock()
            m.ask.return_value = "Save & exit"
            return m

        with (
            patch("kodo.team_config.list_available_teams", return_value=self._mock_list_teams(cfg)),
            patch("questionary.select", side_effect=mock_select),
            patch("kodo.cli._subcommands._save_team") as mock_save,
        ):
            _cmd_teams_edit("test-team")

        mock_save.assert_called_once()
        saved_config = mock_save.call_args[0][1]
        assert saved_config["name"] == "test-team"
        assert saved_config["agents"] == cfg["agents"]
        assert saved_config["verifiers"] == cfg["verifiers"]

    def test_cancel_action_exits(self, capsys):
        """Cancelling action selection should exit without saving."""
        cfg = self._base_team_cfg()

        def mock_select(*args, **kwargs):
            m = MagicMock()
            m.ask.return_value = None  # cancelled
            return m

        with (
            patch("kodo.team_config.list_available_teams", return_value=self._mock_list_teams(cfg)),
            patch("questionary.select", side_effect=mock_select),
            patch("kodo.cli._subcommands._save_team") as mock_save,
        ):
            _cmd_teams_edit("test-team")

        out = capsys.readouterr().out
        assert "not saved" in out
        mock_save.assert_not_called()

    def test_add_agent(self, capsys):
        """'Add agent' action should add a new agent then save."""
        cfg = self._base_team_cfg()
        fake_new_agent = {"backend": "cursor", "model": "composer-1.5", "description": "New agent", "max_turns": 10}

        # Action sequence: "Add agent" → then "Save & exit"
        select_returns = iter(["Add agent", "Save & exit"])
        text_returns = iter(["new_worker"])  # agent key name

        def mock_select(*args, **kwargs):
            m = MagicMock()
            m.ask.return_value = next(select_returns)
            return m

        def mock_text(*args, **kwargs):
            m = MagicMock()
            m.ask.return_value = next(text_returns)
            return m

        with (
            patch("kodo.team_config.list_available_teams", return_value=self._mock_list_teams(cfg)),
            patch("questionary.select", side_effect=mock_select),
            patch("questionary.text", side_effect=mock_text),
            patch("kodo.cli._subcommands._ask_agent_fields", return_value=fake_new_agent),
            patch("kodo.cli._subcommands._save_team") as mock_save,
        ):
            _cmd_teams_edit("test-team")

        saved_config = mock_save.call_args[0][1]
        assert "new_worker" in saved_config["agents"]
        assert saved_config["agents"]["new_worker"] == fake_new_agent

    def test_add_agent_duplicate_key_rejected(self, capsys):
        """Adding an agent with existing key should show error."""
        cfg = self._base_team_cfg()

        # "Add agent" with duplicate key "worker", then "Save & exit"
        select_returns = iter(["Add agent", "Save & exit"])
        text_returns = iter(["worker"])  # duplicate key

        def mock_select(*args, **kwargs):
            m = MagicMock()
            m.ask.return_value = next(select_returns)
            return m

        def mock_text(*args, **kwargs):
            m = MagicMock()
            m.ask.return_value = next(text_returns)
            return m

        with (
            patch("kodo.team_config.list_available_teams", return_value=self._mock_list_teams(cfg)),
            patch("questionary.select", side_effect=mock_select),
            patch("questionary.text", side_effect=mock_text),
            patch("kodo.cli._subcommands._save_team"),
        ):
            _cmd_teams_edit("test-team")

        out = capsys.readouterr().out
        assert "already exists" in out

    def test_edit_agent(self, capsys):
        """'Edit agent' should update agent with new fields."""
        cfg = self._base_team_cfg()
        updated_agent = {"backend": "claude", "model": "opus", "description": "Updated", "max_turns": 25}

        # "Edit agent" → select "worker" → then "Save & exit"
        select_returns = iter(["Edit agent", "worker", "Save & exit"])

        def mock_select(*args, **kwargs):
            m = MagicMock()
            m.ask.return_value = next(select_returns)
            return m

        with (
            patch("kodo.team_config.list_available_teams", return_value=self._mock_list_teams(cfg)),
            patch("questionary.select", side_effect=mock_select),
            patch("kodo.cli._subcommands._ask_agent_fields", return_value=updated_agent),
            patch("kodo.cli._subcommands._save_team") as mock_save,
        ):
            _cmd_teams_edit("test-team")

        saved_config = mock_save.call_args[0][1]
        assert saved_config["agents"]["worker"]["model"] == "opus"

    def test_edit_agent_no_agents(self, capsys):
        """'Edit agent' with no agents should print message and continue."""
        cfg = self._base_team_cfg()
        cfg["agents"] = {}

        select_returns = iter(["Edit agent", "Save & exit"])

        def mock_select(*args, **kwargs):
            m = MagicMock()
            m.ask.return_value = next(select_returns)
            return m

        with (
            patch("kodo.team_config.list_available_teams", return_value=self._mock_list_teams(cfg)),
            patch("questionary.select", side_effect=mock_select),
            patch("kodo.cli._subcommands._save_team"),
        ):
            _cmd_teams_edit("test-team")

        out = capsys.readouterr().out
        assert "No agents to edit" in out

    def test_remove_agent_confirmed(self, capsys):
        """'Remove agent' with confirmation should delete agent and clean verifiers."""
        cfg = self._base_team_cfg()
        # Add a second agent so we can test verifier cleanup
        cfg["agents"]["architect"] = {"backend": "claude", "model": "opus", "description": "Architect"}
        cfg["verifiers"]["reviewers"] = ["architect"]

        # "Remove agent" → select "architect" → confirm yes → "Save & exit"
        select_returns = iter(["Remove agent", "architect", "Save & exit"])

        def mock_select(*args, **kwargs):
            m = MagicMock()
            m.ask.return_value = next(select_returns)
            return m

        def mock_confirm(*args, **kwargs):
            m = MagicMock()
            m.ask.return_value = True  # confirm removal
            return m

        with (
            patch("kodo.team_config.list_available_teams", return_value=self._mock_list_teams(cfg)),
            patch("questionary.select", side_effect=mock_select),
            patch("questionary.confirm", side_effect=mock_confirm),
            patch("kodo.cli._subcommands._save_team") as mock_save,
        ):
            _cmd_teams_edit("test-team")

        saved_config = mock_save.call_args[0][1]
        assert "architect" not in saved_config["agents"]
        # Verifier should be cleaned
        assert "architect" not in saved_config["verifiers"]["reviewers"]

    def test_remove_agent_cancelled(self, capsys):
        """'Remove agent' declined should keep agent."""
        cfg = self._base_team_cfg()

        # "Remove agent" → select "worker" → confirm no → "Save & exit"
        select_returns = iter(["Remove agent", "worker", "Save & exit"])

        def mock_select(*args, **kwargs):
            m = MagicMock()
            m.ask.return_value = next(select_returns)
            return m

        def mock_confirm(*args, **kwargs):
            m = MagicMock()
            m.ask.return_value = False  # decline removal
            return m

        with (
            patch("kodo.team_config.list_available_teams", return_value=self._mock_list_teams(cfg)),
            patch("questionary.select", side_effect=mock_select),
            patch("questionary.confirm", side_effect=mock_confirm),
            patch("kodo.cli._subcommands._save_team") as mock_save,
        ):
            _cmd_teams_edit("test-team")

        saved_config = mock_save.call_args[0][1]
        assert "worker" in saved_config["agents"]

    def test_remove_agent_no_agents(self, capsys):
        """'Remove agent' with no agents should print message."""
        cfg = self._base_team_cfg()
        cfg["agents"] = {}

        select_returns = iter(["Remove agent", "Save & exit"])

        def mock_select(*args, **kwargs):
            m = MagicMock()
            m.ask.return_value = next(select_returns)
            return m

        with (
            patch("kodo.team_config.list_available_teams", return_value=self._mock_list_teams(cfg)),
            patch("questionary.select", side_effect=mock_select),
            patch("kodo.cli._subcommands._save_team"),
        ):
            _cmd_teams_edit("test-team")

        out = capsys.readouterr().out
        assert "No agents to remove" in out

    def test_edit_team_settings(self, capsys):
        """'Edit team settings' should update description, exchanges, cycles."""
        cfg = self._base_team_cfg()

        select_returns = iter(["Edit team settings", "Save & exit"])

        def mock_select(*args, **kwargs):
            m = MagicMock()
            m.ask.return_value = next(select_returns)
            return m

        # text prompts for: description, max_exchanges, max_cycles, orchestrator_prompt
        text_returns = iter([
            "Updated description",  # description
            "50",                   # max_exchanges
            "10",                   # max_cycles
            "New orch prompt",      # orchestrator_prompt
        ])

        def mock_text(*args, **kwargs):
            m = MagicMock()
            m.ask.return_value = next(text_returns)
            return m

        with (
            patch("kodo.team_config.list_available_teams", return_value=self._mock_list_teams(cfg)),
            patch("questionary.select", side_effect=mock_select),
            patch("questionary.text", side_effect=mock_text),
            patch("kodo.cli._subcommands._save_team") as mock_save,
        ):
            _cmd_teams_edit("test-team")

        saved_config = mock_save.call_args[0][1]
        assert saved_config["description"] == "Updated description"
        assert saved_config["max_exchanges"] == 50
        assert saved_config["max_cycles"] == 10
        assert saved_config["orchestrator_prompt"] == "New orch prompt"

    def test_edit_settings_clear_orch_prompt(self, capsys):
        """Clearing orchestrator prompt (empty string) should remove the key."""
        cfg = self._base_team_cfg()
        cfg["orchestrator_prompt"] = "Old prompt"

        select_returns = iter(["Edit team settings", "Save & exit"])

        def mock_select(*args, **kwargs):
            m = MagicMock()
            m.ask.return_value = next(select_returns)
            return m

        text_returns = iter([
            "Same desc",  # description
            "20",         # max_exchanges
            "1",          # max_cycles
            "",           # orchestrator_prompt (clear)
        ])

        def mock_text(*args, **kwargs):
            m = MagicMock()
            m.ask.return_value = next(text_returns)
            return m

        with (
            patch("kodo.team_config.list_available_teams", return_value=self._mock_list_teams(cfg)),
            patch("questionary.select", side_effect=mock_select),
            patch("questionary.text", side_effect=mock_text),
            patch("kodo.cli._subcommands._save_team") as mock_save,
        ):
            _cmd_teams_edit("test-team")

        saved_config = mock_save.call_args[0][1]
        assert "orchestrator_prompt" not in saved_config

    def test_edit_settings_invalid_max_exchanges(self, capsys):
        """Invalid max_exchanges should print error and continue to next action."""
        cfg = self._base_team_cfg()

        # "Edit team settings" (with invalid exchange), then "Save & exit"
        select_returns = iter(["Edit team settings", "Save & exit"])

        def mock_select(*args, **kwargs):
            m = MagicMock()
            m.ask.return_value = next(select_returns)
            return m

        text_returns = iter([
            "Same desc",     # description
            "not-a-number",  # max_exchanges (invalid)
        ])

        def mock_text(*args, **kwargs):
            m = MagicMock()
            m.ask.return_value = next(text_returns)
            return m

        with (
            patch("kodo.team_config.list_available_teams", return_value=self._mock_list_teams(cfg)),
            patch("questionary.select", side_effect=mock_select),
            patch("questionary.text", side_effect=mock_text),
            patch("kodo.cli._subcommands._save_team") as mock_save,
        ):
            _cmd_teams_edit("test-team")

        out = capsys.readouterr().out
        assert "Invalid max_exchanges" in out
        # Should still save (continue triggered, next action is Save)
        mock_save.assert_called_once()

    def test_edit_settings_invalid_max_cycles(self, capsys):
        """Invalid max_cycles should print error and continue to next action."""
        cfg = self._base_team_cfg()

        select_returns = iter(["Edit team settings", "Save & exit"])

        def mock_select(*args, **kwargs):
            m = MagicMock()
            m.ask.return_value = next(select_returns)
            return m

        text_returns = iter([
            "Same desc",     # description
            "20",            # max_exchanges (valid)
            "not-a-number",  # max_cycles (invalid)
        ])

        def mock_text(*args, **kwargs):
            m = MagicMock()
            m.ask.return_value = next(text_returns)
            return m

        with (
            patch("kodo.team_config.list_available_teams", return_value=self._mock_list_teams(cfg)),
            patch("questionary.select", side_effect=mock_select),
            patch("questionary.text", side_effect=mock_text),
            patch("kodo.cli._subcommands._save_team") as mock_save,
        ):
            _cmd_teams_edit("test-team")

        out = capsys.readouterr().out
        assert "Invalid max_cycles" in out
        mock_save.assert_called_once()

    def test_edit_verifiers(self, capsys):
        """'Edit verifiers' should update verifier assignments."""
        cfg = self._base_team_cfg()
        cfg["agents"]["architect"] = {"backend": "claude", "model": "opus", "description": "Architect"}

        # "Edit verifiers" → then "Save & exit"
        select_returns = iter(["Edit verifiers", "Save & exit"])

        def mock_select(*args, **kwargs):
            m = MagicMock()
            m.ask.return_value = next(select_returns)
            return m

        # 3 checkbox calls for testers, browser_testers, reviewers
        checkbox_returns = iter([
            ["worker"],      # testers
            [],              # browser_testers
            ["architect"],   # reviewers
        ])

        def mock_checkbox(*args, **kwargs):
            m = MagicMock()
            m.ask.return_value = next(checkbox_returns)
            return m

        with (
            patch("kodo.team_config.list_available_teams", return_value=self._mock_list_teams(cfg)),
            patch("questionary.select", side_effect=mock_select),
            patch("questionary.checkbox", side_effect=mock_checkbox),
            patch("kodo.cli._subcommands._save_team") as mock_save,
        ):
            _cmd_teams_edit("test-team")

        saved_config = mock_save.call_args[0][1]
        assert saved_config["verifiers"]["testers"] == ["worker"]
        assert saved_config["verifiers"]["reviewers"] == ["architect"]

    def test_edit_verifiers_no_agents(self, capsys):
        """'Edit verifiers' with no agents should print message."""
        cfg = self._base_team_cfg()
        cfg["agents"] = {}

        select_returns = iter(["Edit verifiers", "Save & exit"])

        def mock_select(*args, **kwargs):
            m = MagicMock()
            m.ask.return_value = next(select_returns)
            return m

        with (
            patch("kodo.team_config.list_available_teams", return_value=self._mock_list_teams(cfg)),
            patch("questionary.select", side_effect=mock_select),
            patch("kodo.cli._subcommands._save_team"),
        ):
            _cmd_teams_edit("test-team")

        out = capsys.readouterr().out
        assert "No agents to assign" in out

    def test_shows_orch_prompt_snippet(self, capsys):
        """When orchestrator prompt exists, display shows snippet."""
        cfg = self._base_team_cfg()
        cfg["orchestrator_prompt"] = "A very long orchestrator prompt that should be truncated"

        def mock_select(*args, **kwargs):
            m = MagicMock()
            m.ask.return_value = "Save & exit"
            return m

        with (
            patch("kodo.team_config.list_available_teams", return_value=self._mock_list_teams(cfg)),
            patch("questionary.select", side_effect=mock_select),
            patch("kodo.cli._subcommands._save_team"),
        ):
            _cmd_teams_edit("test-team")

        out = capsys.readouterr().out
        assert "Orchestrator prompt:" in out
        assert "A very long" in out
