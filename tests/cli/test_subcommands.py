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
