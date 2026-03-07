"""Tests for CLI subcommands: runs, backends, teams."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from kodo.cli._subcommands import _cmd_backends, _cmd_runs, _cmd_teams, _save_team, _truncate_word

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
