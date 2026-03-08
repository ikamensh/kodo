"""Tests for kodo.cli._launch — launch/resume logic and helpers."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kodo.agent import Agent
from kodo.cli._launch import (
    EXIT_ERROR,
    EXIT_PARTIAL,
    EXIT_SUCCESS,
    _apply_effort_to_team,
    _build_advisor,
    _build_team_from_config,
    _emit_json_and_exit,
    _fail,
    _format_json_output,
    _print_debug_summary,
    _print_run_summary,
    _resolve_auto_commit,
    _try_auto_fix_team,
    json_output_redirect,
    launch_resume,
    launch_run,
)
from kodo.factory import TeamPreset
from kodo.log import RunDir
from kodo.orchestrators.base import (
    CycleResult,
    RunResult,
    StageResult,
)
from tests.conftest import FakeSession


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_team_preset(**overrides):
    defaults = dict(
        name="full",
        description="Full team",
        system_prompt="You are an orchestrator.",
        build_team=lambda: {},
        default_max_exchanges=30,
        default_max_cycles=5,
    )
    defaults.update(overrides)
    return TeamPreset(**defaults)


def _minimal_params(**overrides):
    d = {
        "team": "full",
        "orchestrator": "api",
        "orchestrator_model": "gemini-flash",
        "max_exchanges": 30,
        "max_cycles": 5,
    }
    d.update(overrides)
    return d


# ---------------------------------------------------------------------------
# _try_auto_fix_team
# ---------------------------------------------------------------------------


class TestTryAutoFixTeam:
    def test_user_accepts_auto_fix_with_json_config(self):
        """User types 'y' → _cmd_teams_auto_all is called, team rebuilt from JSON config."""
        fake_team = {"worker": Agent(FakeSession(), "w")}
        fake_config = {
            "agents": {"worker": {"backend": "claude", "model": "sonnet"}},
            "orchestrator_prompt": "Custom prompt",
            "verifiers": {"testers": ["worker"]},
        }

        with (
            patch("builtins.input", return_value="y"),
            patch("kodo.cli._subcommands._cmd_teams_auto_all"),
            patch("kodo.cli._launch.load_team_config", return_value=fake_config),
            patch("kodo.cli._launch.get_team", return_value=_fake_team_preset()),
            patch("kodo.cli._launch.build_team_from_json", return_value=fake_team),
        ):
            team, system_prompt, verifiers = _try_auto_fix_team(
                "full", Path("/fake"), RuntimeError("missing backend"),
            )

        assert team == fake_team
        assert system_prompt == "Custom prompt"
        assert verifiers == {"testers": ["worker"]}

    def test_user_accepts_auto_fix_no_json_config(self):
        """User types '' (default yes) → auto fix runs, falls back to built-in preset."""
        fake_team = {"worker": Agent(FakeSession(), "w")}
        preset = _fake_team_preset(build_team=lambda: fake_team)

        with (
            patch("builtins.input", return_value=""),
            patch("kodo.cli._subcommands._cmd_teams_auto_all"),
            patch("kodo.cli._launch.load_team_config", return_value=None),
            patch("kodo.cli._launch.get_team", return_value=preset),
        ):
            team, system_prompt, verifiers = _try_auto_fix_team(
                "full", Path("/fake"), RuntimeError("missing"),
            )

        assert team == fake_team
        assert system_prompt == preset.system_prompt
        assert verifiers is None

    def test_user_declines_auto_fix_exits(self):
        """User types 'n' → _fail is called → SystemExit."""
        with (
            patch("builtins.input", return_value="n"),
            pytest.raises(SystemExit),
        ):
            _try_auto_fix_team("full", Path("/fake"), RuntimeError("missing"))

    def test_eof_during_prompt_exits(self):
        """EOFError during input → declines → exits."""
        with (
            patch("builtins.input", side_effect=EOFError),
            pytest.raises(SystemExit),
        ):
            _try_auto_fix_team("full", Path("/fake"), RuntimeError("missing"))

    def test_keyboard_interrupt_during_prompt_exits(self):
        """KeyboardInterrupt during input → declines → exits."""
        with (
            patch("builtins.input", side_effect=KeyboardInterrupt),
            pytest.raises(SystemExit),
        ):
            _try_auto_fix_team("full", Path("/fake"), RuntimeError("missing"))


# ---------------------------------------------------------------------------
# _build_team_from_config
# ---------------------------------------------------------------------------


class TestBuildTeamFromConfig:
    def test_with_json_config(self):
        """When team_config is provided, build from JSON."""
        fake_team = {"worker": Agent(FakeSession(), "w")}
        config = {
            "agents": {"worker": {"backend": "claude", "model": "sonnet"}},
            "orchestrator_prompt": "Custom",
            "verifiers": {"testers": ["worker"]},
        }
        preset = _fake_team_preset()

        with (
            patch("kodo.cli._launch.build_team_from_json", return_value=fake_team),
            patch("kodo.cli._launch.validate_verifiers", return_value={"testers": ["worker"]}),
        ):
            team, prompt, verifiers = _build_team_from_config(
                config, preset, "full", Path("/fake"),
            )

        assert team == fake_team
        assert prompt == "Custom"
        assert verifiers == {"testers": ["worker"]}

    def test_without_json_config_uses_preset(self):
        """When team_config is None, build from preset."""
        fake_team = {"worker": Agent(FakeSession(), "w")}
        preset = _fake_team_preset(build_team=lambda: fake_team)

        team, prompt, verifiers = _build_team_from_config(
            None, preset, "full", Path("/fake"),
        )

        assert team == fake_team
        assert prompt == preset.system_prompt
        assert verifiers is None

    def test_runtime_error_triggers_auto_fix(self):
        """RuntimeError during team build → delegates to _try_auto_fix_team."""
        preset = _fake_team_preset()
        fake_team = {"w": Agent(FakeSession(), "w")}

        with (
            patch(
                "kodo.cli._launch.build_team_from_json",
                side_effect=RuntimeError("no backend"),
            ),
            patch(
                "kodo.cli._launch._try_auto_fix_team",
                return_value=(fake_team, "prompt", None),
            ) as mock_fix,
        ):
            team, prompt, verifiers = _build_team_from_config(
                {"agents": {}}, preset, "full", Path("/fake"),
            )

        mock_fix.assert_called_once()
        assert team == fake_team

    def test_value_error_exits(self):
        """ValueError during team build → _fail → SystemExit."""
        preset = _fake_team_preset()

        with (
            patch(
                "kodo.cli._launch.build_team_from_json",
                side_effect=ValueError("bad config"),
            ),
            pytest.raises(SystemExit),
        ):
            _build_team_from_config({"agents": {}}, preset, "full", Path("/fake"))


# ---------------------------------------------------------------------------
# _apply_effort_to_team
# ---------------------------------------------------------------------------


class TestApplyEffortToTeam:
    def test_standard_effort_is_noop(self):
        """'standard' effort maps to None, so nothing changes."""
        mock_session = MagicMock()
        agent = Agent(mock_session, "w")
        team = {"worker": agent}

        _apply_effort_to_team(team, "standard")
        # effort attribute should not be set
        assert not hasattr(mock_session, "effort") or mock_session.effort != "standard"

    def test_high_effort_sets_on_claude_sessions(self):
        """'high' effort should set session.effort on ClaudeSession instances."""
        from kodo.sessions.claude import ClaudeSession

        mock_session = MagicMock(spec=ClaudeSession)
        agent = Agent(mock_session, "w")
        team = {"worker": agent}

        _apply_effort_to_team(team, "high")
        assert mock_session.effort == "high"

    def test_non_claude_session_unchanged(self):
        """Non-ClaudeSession instances should not get effort set."""
        mock_session = FakeSession()
        agent = Agent(mock_session, "w")
        team = {"worker": agent}

        _apply_effort_to_team(team, "high")
        assert not hasattr(mock_session, "effort")


# ---------------------------------------------------------------------------
# _resolve_auto_commit
# ---------------------------------------------------------------------------


class TestResolveAutoCommit:
    def test_disables_when_no_git(self, tmp_path, capsys):
        """No .git dir → auto_commit disabled, message printed."""
        result = _resolve_auto_commit({"auto_commit": True}, tmp_path)
        assert result is False
        assert "Auto-commit disabled" in capsys.readouterr().out

    def test_keeps_enabled_with_git(self, tmp_path):
        """With .git dir → auto_commit stays True."""
        (tmp_path / ".git").mkdir()
        result = _resolve_auto_commit({"auto_commit": True}, tmp_path)
        assert result is True

    def test_quiet_suppresses_message(self, tmp_path, capsys):
        """quiet=True → no message printed."""
        _resolve_auto_commit({"auto_commit": True}, tmp_path, quiet=True)
        assert "Auto-commit disabled" not in capsys.readouterr().out

    def test_already_false_stays_false(self, tmp_path):
        """auto_commit=False → stays False without checking .git."""
        result = _resolve_auto_commit({"auto_commit": False}, tmp_path)
        assert result is False


# ---------------------------------------------------------------------------
# _build_advisor
# ---------------------------------------------------------------------------


class TestBuildAdvisor:
    def test_gemini_key_creates_advisor(self):
        """GEMINI_API_KEY → Advisor with google-gla model."""
        with (
            patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}, clear=True),
            patch("kodo.orchestrators.advisor.Advisor") as MockAdvisor,
        ):
            _build_advisor(_minimal_params())

        MockAdvisor.assert_called_once()
        assert "google-gla:" in MockAdvisor.call_args[1]["model"]

    def test_anthropic_key_creates_advisor(self):
        """ANTHROPIC_API_KEY (no Gemini) → Advisor with mapped model."""
        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"}, clear=True),
            patch("kodo.orchestrators.advisor.Advisor") as MockAdvisor,
        ):
            _build_advisor(_minimal_params(orchestrator_model="opus"))

        MockAdvisor.assert_called_once()

    def test_no_keys_returns_none(self):
        """No API keys → returns None."""
        with patch.dict("os.environ", {}, clear=True):
            result = _build_advisor(_minimal_params())

        assert result is None


# ---------------------------------------------------------------------------
# json_output_redirect
# ---------------------------------------------------------------------------


class TestJsonOutputRedirect:
    def test_redirects_stdout_to_stderr(self, capsys):
        """Inside context, sys.stdout should be sys.stderr."""
        import sys

        original = sys.stdout
        with json_output_redirect() as saved:
            assert saved is original
            assert sys.stdout is sys.stderr
        # Restored after context
        assert sys.stdout is original

    def test_restores_on_exception(self):
        """sys.stdout should be restored even if context raises."""
        import sys

        original = sys.stdout
        try:
            with json_output_redirect():
                raise ValueError("boom")
        except ValueError:
            pass
        assert sys.stdout is original


# ---------------------------------------------------------------------------
# _fail
# ---------------------------------------------------------------------------


class TestFail:
    def test_normal_mode_exits(self):
        """Without JSON mode, prints error and exits."""
        with pytest.raises(SystemExit, match="1"):
            _fail("something broke")

    def test_json_mode_outputs_json(self, capsys):
        """In JSON mode (_original_stdout set), outputs JSON and exits."""
        import sys

        with (
            patch("kodo.cli._launch._original_stdout", sys.stdout),
            pytest.raises(SystemExit, match=str(EXIT_ERROR)),
        ):
            _fail("something broke")

        out = capsys.readouterr().out
        output = json.loads(out)
        assert output["status"] == "error"
        assert output["error"] == "something broke"

    def test_custom_exit_code(self):
        """Custom exit code should be used."""
        with pytest.raises(SystemExit, match="42"):
            _fail("custom error", code=42)


# ---------------------------------------------------------------------------
# _format_json_output
# ---------------------------------------------------------------------------


class TestFormatJsonOutput:
    def test_error_output(self):
        result = _format_json_output(error="bad input")
        assert result == {"status": "error", "error": "bad input"}

    def test_no_result_output(self):
        result = _format_json_output()
        assert result["status"] == "error"

    def test_completed_output(self):
        rr = RunResult(
            cycles=[CycleResult(exchanges=5, total_cost_usd=0.01, finished=True, summary="done")],
        )
        result = _format_json_output(rr)
        assert result["status"] == "completed"
        assert result["finished"] is True
        assert result["cycles"] == 1

    def test_partial_output(self):
        rr = RunResult(
            cycles=[CycleResult(exchanges=3, total_cost_usd=0.005, finished=False, summary="partial")],
        )
        result = _format_json_output(rr)
        assert result["status"] == "partial"
        assert result["finished"] is False

    def test_failed_output(self):
        rr = RunResult(cycles=[])
        result = _format_json_output(rr)
        assert result["status"] == "failed"

    def test_with_stages(self):
        rr = RunResult(
            cycles=[CycleResult(exchanges=5, total_cost_usd=0.01, finished=True)],
            stage_results=[
                StageResult(stage_index=1, stage_name="Setup", finished=True, success=True, summary="OK", cycles=[MagicMock()]),
            ],
        )
        result = _format_json_output(rr)
        assert "stages" in result
        assert result["stages"][0]["name"] == "Setup"

    def test_with_improve_report(self):
        rr = RunResult(
            cycles=[CycleResult(exchanges=5, total_cost_usd=0.01, finished=True)],
        )
        result = _format_json_output(rr, improve_report="# Report\n- found bug")
        assert result["improve_report"] == "# Report\n- found bug"


# ---------------------------------------------------------------------------
# _emit_json_and_exit
# ---------------------------------------------------------------------------


class TestEmitJsonAndExit:
    def test_exits_success_when_finished(self, capsys):
        """Finished result → EXIT_SUCCESS."""
        import sys

        args = MagicMock(json=True)
        result = RunResult(
            cycles=[CycleResult(exchanges=1, total_cost_usd=0.0, finished=True)],
        )

        with (
            patch("kodo.cli._launch._original_stdout", sys.stdout),
            pytest.raises(SystemExit, match=str(EXIT_SUCCESS)),
        ):
            _emit_json_and_exit(args, result)

    def test_exits_partial_when_unfinished(self, capsys):
        """Unfinished result → EXIT_PARTIAL."""
        import sys

        args = MagicMock(json=True)
        result = RunResult(
            cycles=[CycleResult(exchanges=1, total_cost_usd=0.0, finished=False)],
        )

        with (
            patch("kodo.cli._launch._original_stdout", sys.stdout),
            pytest.raises(SystemExit, match=str(EXIT_PARTIAL)),
        ):
            _emit_json_and_exit(args, result)


# ---------------------------------------------------------------------------
# _print_run_summary
# ---------------------------------------------------------------------------


class TestPrintRunSummary:
    def test_simple_summary(self, capsys):
        """Basic summary without stages."""
        result = RunResult(
            cycles=[CycleResult(exchanges=5, total_cost_usd=0.01, finished=True, summary="All done")],
        )
        _print_run_summary(result)
        out = capsys.readouterr().out
        assert "Done:" in out
        assert "1 cycle" in out
        assert "5 exchanges" in out
        assert "All done" in out

    def test_summary_with_stages(self, capsys):
        """Summary with stage results."""
        result = RunResult(
            cycles=[CycleResult(exchanges=10, total_cost_usd=0.02, finished=True)],
            stage_results=[
                StageResult(stage_index=1, stage_name="Setup", finished=True, success=True, summary="OK", cycles=[MagicMock()]),
                StageResult(stage_index=2, stage_name="Build", finished=False, summary="Timeout", cycles=[MagicMock()]),
            ],
        )
        _print_run_summary(result)
        out = capsys.readouterr().out
        assert "1/2 stages" in out
        assert "+ Stage 1: Setup" in out
        assert "- Stage 2: Build" in out

    def test_total_cycles_override(self, capsys):
        """When total_cycles is provided, use it instead of len(cycles)."""
        result = RunResult(
            cycles=[CycleResult(exchanges=5, total_cost_usd=0.01, finished=True)],
        )
        _print_run_summary(result, total_cycles=10)
        out = capsys.readouterr().out
        assert "10 cycles" in out


# ---------------------------------------------------------------------------
# launch_resume
# ---------------------------------------------------------------------------


class TestLaunchResume:
    @pytest.fixture(autouse=True)
    def _mock_log(self):
        """Mock log.init_append to avoid needing real log files."""
        with patch("kodo.cli._launch.log") as mock_log:
            # Let print_stats_table be a no-op
            mock_log.print_stats_table = MagicMock()
            mock_log.init_append = MagicMock()
            yield mock_log

    def _make_state(self, tmp_path, **overrides):
        from kodo.log import RunState

        defaults = dict(
            run_id="20260101_120000",
            log_file=tmp_path / "runs" / "20260101_120000" / "run.jsonl",
            goal="Build X",
            orchestrator="api",
            model="gemini-flash",
            project_dir=str(tmp_path),
            max_exchanges=30,
            max_cycles=5,
            team=["worker_fast"],
            completed_cycles=2,
            last_summary="partial progress",
            finished=False,
            agent_session_ids={},
            has_stages=False,
            completed_stages=[],
            stage_summaries={},
            current_stage_cycles=0,
            pending_exchanges=[],
            team_preset="full",
        )
        defaults.update(overrides)
        return RunState(**defaults)

    def test_basic_resume(self, tmp_path):
        """Basic resume: loads config, builds team, runs orchestrator."""
        run_dir = RunDir.create(tmp_path, "20260101_120000")
        # Write config.json
        config = _minimal_params()
        run_dir.config_file.write_text(json.dumps(config))

        state = self._make_state(tmp_path)
        fake_team = {"worker": Agent(FakeSession(), "w")}
        preset = _fake_team_preset(build_team=lambda: fake_team)

        fake_result = RunResult(
            cycles=[CycleResult(exchanges=3, total_cost_usd=0.0, finished=True)],
        )

        with (
            patch("kodo.cli._launch.get_team", return_value=preset),
            patch("kodo.cli._launch.load_team_config", return_value=None),
            patch("kodo.cli._launch.build_orchestrator") as mock_orch,
            patch("kodo.cli._launch._resolve_auto_commit", return_value=True),
            patch("kodo.cli._launch._build_advisor", return_value=None),
        ):
            mock_orch.return_value.run.return_value = fake_result
            mock_orch.return_value.model = "gemini-flash"
            result = launch_resume(run_dir, state)

        assert result.finished is True

    def test_resume_with_legacy_mode_key(self, tmp_path):
        """Config with 'mode' key (legacy) should be migrated to 'team'."""
        run_dir = RunDir.create(tmp_path, "20260101_120000")
        legacy_config = {
            "mode": "full",
            "orchestrator": "api",
            "orchestrator_model": "gemini-flash",
            "max_exchanges": 30,
            "max_cycles": 5,
        }
        run_dir.config_file.write_text(json.dumps(legacy_config))

        state = self._make_state(tmp_path)
        fake_team = {"worker": Agent(FakeSession(), "w")}
        preset = _fake_team_preset(build_team=lambda: fake_team)
        fake_result = RunResult(
            cycles=[CycleResult(exchanges=1, total_cost_usd=0.0, finished=True)],
        )

        with (
            patch("kodo.cli._launch.get_team", return_value=preset),
            patch("kodo.cli._launch.load_team_config", return_value=None),
            patch("kodo.cli._launch.build_orchestrator") as mock_orch,
            patch("kodo.cli._launch._resolve_auto_commit", return_value=True),
            patch("kodo.cli._launch._build_advisor", return_value=None),
        ):
            mock_orch.return_value.run.return_value = fake_result
            mock_orch.return_value.model = "gemini-flash"
            launch_resume(run_dir, state)

        # Config should have been migrated
        migrated = json.loads(run_dir.config_file.read_text())
        assert "team" in migrated
        assert "mode" not in migrated

    def test_resume_no_config_file_reconstructs(self, tmp_path):
        """When config.json doesn't exist, reconstruct params from RunState."""
        run_dir = RunDir.create(tmp_path, "20260101_120000")
        # Don't write config.json
        state = self._make_state(tmp_path)
        fake_team = {"worker": Agent(FakeSession(), "w")}
        preset = _fake_team_preset(build_team=lambda: fake_team)
        fake_result = RunResult(
            cycles=[CycleResult(exchanges=1, total_cost_usd=0.0, finished=True)],
        )

        with (
            patch("kodo.cli._launch.get_team", return_value=preset),
            patch("kodo.cli._launch.load_team_config", return_value=None),
            patch("kodo.cli._launch.build_orchestrator") as mock_orch,
            patch("kodo.cli._launch._resolve_auto_commit", return_value=True),
            patch("kodo.cli._launch._build_advisor", return_value=None),
        ):
            mock_orch.return_value.run.return_value = fake_result
            mock_orch.return_value.model = "gemini-flash"
            result = launch_resume(run_dir, state)

        assert result.finished is True

    def test_resume_unknown_team_falls_back(self, tmp_path, capsys):
        """Unknown team in config → falls back to 'full' with warning."""
        run_dir = RunDir.create(tmp_path, "20260101_120000")
        config = _minimal_params(team="deleted-team")
        run_dir.config_file.write_text(json.dumps(config))

        state = self._make_state(tmp_path, team_preset="deleted-team")
        fake_team = {"worker": Agent(FakeSession(), "w")}
        preset = _fake_team_preset(build_team=lambda: fake_team)
        fake_result = RunResult(
            cycles=[CycleResult(exchanges=1, total_cost_usd=0.0, finished=True)],
        )

        def get_team_side_effect(name):
            if name == "deleted-team":
                raise KeyError(name)
            return preset

        with (
            patch("kodo.cli._launch.get_team", side_effect=get_team_side_effect),
            patch("kodo.cli._launch.load_team_config", return_value=None),
            patch("kodo.cli._launch.build_orchestrator") as mock_orch,
            patch("kodo.cli._launch._resolve_auto_commit", return_value=True),
            patch("kodo.cli._launch._build_advisor", return_value=None),
        ):
            mock_orch.return_value.run.return_value = fake_result
            mock_orch.return_value.model = "gemini-flash"
            launch_resume(run_dir, state)

        out = capsys.readouterr().out
        assert "deleted-team" in out
        assert "using 'full'" in out

    def test_resume_with_team_override(self, tmp_path):
        """team_override should load config for the override team."""
        run_dir = RunDir.create(tmp_path, "20260101_120000")
        config = _minimal_params()
        run_dir.config_file.write_text(json.dumps(config))

        state = self._make_state(tmp_path)
        fake_team = {"worker": Agent(FakeSession(), "w")}
        preset = _fake_team_preset(build_team=lambda: fake_team)
        fake_result = RunResult(
            cycles=[CycleResult(exchanges=1, total_cost_usd=0.0, finished=True)],
        )

        with (
            patch("kodo.cli._launch.get_team", return_value=preset),
            patch("kodo.cli._launch.load_team_config", return_value=None),
            patch("kodo.cli._launch.build_orchestrator") as mock_orch,
            patch("kodo.cli._launch._resolve_auto_commit", return_value=True),
            patch("kodo.cli._launch._build_advisor", return_value=None),
        ):
            mock_orch.return_value.run.return_value = fake_result
            mock_orch.return_value.model = "gemini-flash"
            result = launch_resume(run_dir, state, team_override="quick")

        assert result.finished is True

    def test_resume_unknown_team_override_exits(self, tmp_path):
        """Unknown team_override → exits."""
        run_dir = RunDir.create(tmp_path, "20260101_120000")
        config = _minimal_params()
        run_dir.config_file.write_text(json.dumps(config))

        state = self._make_state(tmp_path)
        preset = _fake_team_preset()

        def get_team_side_effect(name):
            if name == "nonexistent":
                raise KeyError(name)
            return preset

        with (
            patch("kodo.cli._launch.get_team", side_effect=get_team_side_effect),
            patch("kodo.cli._launch.load_team_config", return_value=None),
            pytest.raises(SystemExit),
        ):
            launch_resume(run_dir, state, team_override="nonexistent")

    def test_resume_staged_run_loads_plan(self, tmp_path):
        """Staged run should load goal-plan.json."""
        run_dir = RunDir.create(tmp_path, "20260101_120000")
        config = _minimal_params()
        run_dir.config_file.write_text(json.dumps(config))

        plan_data = {
            "context": "Test project",
            "stages": [
                {"index": 1, "name": "S1", "description": "Do it", "acceptance_criteria": "Done"},
            ],
        }
        run_dir.goal_plan_file.write_text(json.dumps(plan_data))

        state = self._make_state(tmp_path, has_stages=True, completed_stages=[])
        fake_team = {"worker": Agent(FakeSession(), "w")}
        preset = _fake_team_preset(build_team=lambda: fake_team)
        fake_result = RunResult(
            cycles=[CycleResult(exchanges=1, total_cost_usd=0.0, finished=True)],
        )

        with (
            patch("kodo.cli._launch.get_team", return_value=preset),
            patch("kodo.cli._launch.load_team_config", return_value=None),
            patch("kodo.cli._launch.build_orchestrator") as mock_orch,
            patch("kodo.cli._launch._resolve_auto_commit", return_value=True),
            patch("kodo.cli._launch._build_advisor", return_value=None),
        ):
            mock_orch.return_value.run.return_value = fake_result
            mock_orch.return_value.model = "gemini-flash"
            launch_resume(run_dir, state)

        # Plan should have been passed to orchestrator.run
        call_kwargs = mock_orch.return_value.run.call_args[1]
        assert call_kwargs["plan"] is not None

    def test_resume_staged_no_plan_file_exits(self, tmp_path):
        """Staged run without goal-plan.json → exits."""
        run_dir = RunDir.create(tmp_path, "20260101_120000")
        config = _minimal_params()
        run_dir.config_file.write_text(json.dumps(config))
        # No goal-plan.json written

        state = self._make_state(tmp_path, has_stages=True)
        fake_team = {"worker": Agent(FakeSession(), "w")}
        preset = _fake_team_preset(build_team=lambda: fake_team)

        with (
            patch("kodo.cli._launch.get_team", return_value=preset),
            patch("kodo.cli._launch.load_team_config", return_value=None),
            patch("kodo.cli._launch.build_orchestrator") as mock_orch,
            patch("kodo.cli._launch._resolve_auto_commit", return_value=True),
            pytest.raises(SystemExit),
        ):
            mock_orch.return_value.model = "gemini-flash"
            launch_resume(run_dir, state)

    def test_resume_with_effort(self, tmp_path):
        """Resume with effort != standard should apply effort."""
        run_dir = RunDir.create(tmp_path, "20260101_120000")
        config = _minimal_params(effort="high")
        run_dir.config_file.write_text(json.dumps(config))

        state = self._make_state(tmp_path)
        fake_team = {"worker": Agent(FakeSession(), "w")}
        preset = _fake_team_preset(build_team=lambda: fake_team)
        fake_result = RunResult(
            cycles=[CycleResult(exchanges=1, total_cost_usd=0.0, finished=True)],
        )

        with (
            patch("kodo.cli._launch.get_team", return_value=preset),
            patch("kodo.cli._launch.load_team_config", return_value=None),
            patch("kodo.cli._launch.build_orchestrator") as mock_orch,
            patch("kodo.cli._launch._resolve_auto_commit", return_value=True),
            patch("kodo.cli._launch._build_advisor", return_value=None),
            patch("kodo.cli._launch._apply_effort_to_team") as mock_effort,
            patch("kodo.prompts.roles.build_orchestrator_prompt", return_value="effort prompt"),
        ):
            mock_orch.return_value.run.return_value = fake_result
            mock_orch.return_value.model = "gemini-flash"
            launch_resume(run_dir, state)

        mock_effort.assert_called_once()

    def test_resume_prints_info(self, tmp_path, capsys):
        """Resume should print team info, cycles, log path."""
        run_dir = RunDir.create(tmp_path, "20260101_120000")
        config = _minimal_params()
        run_dir.config_file.write_text(json.dumps(config))

        state = self._make_state(
            tmp_path,
            agent_session_ids={"worker": "sess-123"},
            pending_exchanges=[{"type": "test"}],
        )
        fake_team = {"worker": Agent(FakeSession(), "w")}
        preset = _fake_team_preset(build_team=lambda: fake_team)
        fake_result = RunResult(
            cycles=[CycleResult(exchanges=1, total_cost_usd=0.0, finished=True)],
        )

        with (
            patch("kodo.cli._launch.get_team", return_value=preset),
            patch("kodo.cli._launch.load_team_config", return_value=None),
            patch("kodo.cli._launch.build_orchestrator") as mock_orch,
            patch("kodo.cli._launch._resolve_auto_commit", return_value=True),
            patch("kodo.cli._launch._build_advisor", return_value=None),
        ):
            mock_orch.return_value.run.return_value = fake_result
            mock_orch.return_value.model = "gemini-flash"
            launch_resume(run_dir, state)

        out = capsys.readouterr().out
        assert "Resuming run:" in out
        assert "20260101_120000" in out
        assert "Resuming sessions:" in out
        assert "worker" in out
        assert "Resuming mid-cycle:" in out

    def test_resume_reads_team_json(self, tmp_path):
        """When team.json exists in run dir, it should be loaded."""
        run_dir = RunDir.create(tmp_path, "20260101_120000")
        config = _minimal_params()
        run_dir.config_file.write_text(json.dumps(config))

        # Write a team.json snapshot
        team_json = {
            "agents": {"worker": {"backend": "claude", "model": "sonnet"}},
            "orchestrator_prompt": "Custom prompt from snapshot",
        }
        run_dir.team_file.write_text(json.dumps(team_json))

        state = self._make_state(tmp_path)
        fake_team = {"worker": Agent(FakeSession(), "w")}
        preset = _fake_team_preset(build_team=lambda: fake_team)
        fake_result = RunResult(
            cycles=[CycleResult(exchanges=1, total_cost_usd=0.0, finished=True)],
        )

        with (
            patch("kodo.cli._launch.get_team", return_value=preset),
            patch("kodo.cli._launch.load_team_config", return_value=None),
            patch("kodo.cli._launch.build_team_from_json", return_value=fake_team),
            patch("kodo.cli._launch.validate_verifiers", return_value=None),
            patch("kodo.cli._launch.build_orchestrator") as mock_orch,
            patch("kodo.cli._launch._resolve_auto_commit", return_value=True),
            patch("kodo.cli._launch._build_advisor", return_value=None),
        ):
            mock_orch.return_value.run.return_value = fake_result
            mock_orch.return_value.model = "gemini-flash"
            result = launch_resume(run_dir, state)

        assert result.finished is True

    def test_resume_corrupt_config_reconstructs(self, tmp_path):
        """Corrupt config.json → reconstructs from RunState."""
        run_dir = RunDir.create(tmp_path, "20260101_120000")
        run_dir.config_file.write_text("not valid json!")

        state = self._make_state(tmp_path)
        fake_team = {"worker": Agent(FakeSession(), "w")}
        preset = _fake_team_preset(build_team=lambda: fake_team)
        fake_result = RunResult(
            cycles=[CycleResult(exchanges=1, total_cost_usd=0.0, finished=True)],
        )

        with (
            patch("kodo.cli._launch.get_team", return_value=preset),
            patch("kodo.cli._launch.load_team_config", return_value=None),
            patch("kodo.cli._launch.build_orchestrator") as mock_orch,
            patch("kodo.cli._launch._resolve_auto_commit", return_value=True),
            patch("kodo.cli._launch._build_advisor", return_value=None),
        ):
            mock_orch.return_value.run.return_value = fake_result
            mock_orch.return_value.model = "gemini-flash"
            result = launch_resume(run_dir, state)

        assert result.finished is True

    def test_resume_team_override_value_error_falls_to_preset(self, tmp_path):
        """team_override with ValueError from load_team_config falls back to preset."""
        run_dir = RunDir.create(tmp_path, "20260101_120000")
        config = _minimal_params()
        run_dir.config_file.write_text(json.dumps(config))

        state = self._make_state(tmp_path)
        fake_team = {"worker": Agent(FakeSession(), "w")}
        preset = _fake_team_preset(build_team=lambda: fake_team)
        fake_result = RunResult(
            cycles=[CycleResult(exchanges=1, total_cost_usd=0.0, finished=True)],
        )

        with (
            patch("kodo.cli._launch.get_team", return_value=preset),
            patch(
                "kodo.cli._launch.load_team_config",
                side_effect=ValueError("bad config"),
            ),
            patch("kodo.cli._launch.build_orchestrator") as mock_orch,
            patch("kodo.cli._launch._resolve_auto_commit", return_value=True),
            patch("kodo.cli._launch._build_advisor", return_value=None),
        ):
            mock_orch.return_value.run.return_value = fake_result
            mock_orch.return_value.model = "gemini-flash"
            result = launch_resume(run_dir, state, team_override="custom")

        assert result.finished is True

    def test_resume_passes_resume_state_to_orchestrator(self, tmp_path):
        """launch_resume must pass resume=ResumeState(...) to orchestrator.run(),
        not resume=None. Without this, resume silently starts a fresh run."""
        run_dir = RunDir.create(tmp_path, "20260101_120000")
        config = _minimal_params()
        run_dir.config_file.write_text(json.dumps(config))

        state = self._make_state(
            tmp_path,
            completed_cycles=3,
            last_summary="partial work done",
            agent_session_ids={"worker": "sess-abc"},
        )
        fake_team = {"worker": Agent(FakeSession(), "w")}
        preset = _fake_team_preset(build_team=lambda: fake_team)
        fake_result = RunResult(
            cycles=[CycleResult(exchanges=1, total_cost_usd=0.0, finished=True)],
        )

        with (
            patch("kodo.cli._launch.get_team", return_value=preset),
            patch("kodo.cli._launch.load_team_config", return_value=None),
            patch("kodo.cli._launch.build_orchestrator") as mock_orch,
            patch("kodo.cli._launch._resolve_auto_commit", return_value=True),
            patch("kodo.cli._launch._build_advisor", return_value=None),
        ):
            mock_orch.return_value.run.return_value = fake_result
            mock_orch.return_value.model = "gemini-flash"
            launch_resume(run_dir, state)

        call_kwargs = mock_orch.return_value.run.call_args[1]
        resume_arg = call_kwargs["resume"]
        assert resume_arg is not None, "resume= must not be None — that would start a fresh run"
        assert resume_arg.completed_cycles == 3
        assert resume_arg.prior_summary == "partial work done"
        assert resume_arg.agent_session_ids == {"worker": "sess-abc"}

    def test_resume_passes_goal_from_state(self, tmp_path):
        """launch_resume should use the goal from the saved RunState, not a fresh one."""
        run_dir = RunDir.create(tmp_path, "20260101_120000")
        config = _minimal_params()
        run_dir.config_file.write_text(json.dumps(config))

        state = self._make_state(tmp_path, goal="Specific saved goal text")
        fake_team = {"worker": Agent(FakeSession(), "w")}
        preset = _fake_team_preset(build_team=lambda: fake_team)
        fake_result = RunResult(
            cycles=[CycleResult(exchanges=1, total_cost_usd=0.0, finished=True)],
        )

        with (
            patch("kodo.cli._launch.get_team", return_value=preset),
            patch("kodo.cli._launch.load_team_config", return_value=None),
            patch("kodo.cli._launch.build_orchestrator") as mock_orch,
            patch("kodo.cli._launch._resolve_auto_commit", return_value=True),
            patch("kodo.cli._launch._build_advisor", return_value=None),
        ):
            mock_orch.return_value.run.return_value = fake_result
            mock_orch.return_value.model = "gemini-flash"
            launch_resume(run_dir, state)

        call_args = mock_orch.return_value.run.call_args[0]
        assert call_args[0] == "Specific saved goal text"


# ---------------------------------------------------------------------------
# _print_debug_summary
# ---------------------------------------------------------------------------


class TestPrintDebugSummary:
    def test_debug_summary_output(self, capsys):
        """Debug summary should print letter assignments and token counts."""
        mock_orch = MagicMock()

        class FakeDebugSession:
            def __init__(self, letter, gen, seen):
                self.letter = letter
                self.generated_tokens = gen
                self.seen_tokens = seen

        sessions = {
            "worker": FakeDebugSession("A", 100, 500),
            "orchestrator": FakeDebugSession("B", 200, 300),
        }

        _print_debug_summary(sessions)

        out = capsys.readouterr().out
        assert "DEBUG SUMMARY" in out
        assert "A (worker)" in out or "A (" in out
        assert "generated 100" in out
        assert "saw 500" in out
        assert "B (orchestrator)" in out or "B (" in out

    def test_empty_sessions(self, capsys):
        """Empty sessions dict should still print header."""
        _print_debug_summary({})
        out = capsys.readouterr().out
        assert "DEBUG SUMMARY" in out


# ---------------------------------------------------------------------------
# A11: launch_run effort flow-through (mutation-tested)
# ---------------------------------------------------------------------------


class TestLaunchRunEffortFlowThrough:
    """Verify that effort from params actually reaches orchestrator.run() and
    _apply_effort_to_team inside launch_run — not just in isolation."""

    @pytest.fixture(autouse=True)
    def _mock_deps(self, tmp_path):
        """Common mocks for launch_run tests (non-debug path)."""
        self.run_dir = RunDir.create(tmp_path)
        self.fake_team = {"worker": Agent(FakeSession(), "w")}
        self.preset = _fake_team_preset(build_team=lambda: self.fake_team)
        self.fake_result = RunResult(
            cycles=[CycleResult(exchanges=1, total_cost_usd=0.0, finished=True)],
        )
        with (
            patch("kodo.cli._launch.log") as mock_log,
            patch("kodo.cli._launch.get_team", return_value=self.preset),
            patch("kodo.cli._launch.load_team_config", return_value=None),
            patch("kodo.cli._launch.preflight_check_backends", return_value=[]),
            patch("kodo.cli._launch.build_orchestrator") as self.mock_orch,
            patch("kodo.cli._launch._resolve_auto_commit", return_value=False),
            patch("kodo.cli._launch._build_advisor", return_value=None),
        ):
            mock_log.init = MagicMock(return_value=Path("/fake/log"))
            mock_log.emit = MagicMock()
            mock_log.print_stats_table = MagicMock()
            mock_log._start_time = None
            mock_log._fmt_time = MagicMock(return_value="0s")
            self.mock_orch.return_value.run.return_value = self.fake_result
            self.mock_orch.return_value.model = "test-model"
            yield

    def test_high_effort_passed_to_orchestrator_run(self, tmp_path):
        """effort='high' in params must reach orchestrator.run(effort='high')."""
        params = _minimal_params(effort="high")
        with patch("kodo.cli._launch._apply_effort_to_team") as mock_apply:
            launch_run(self.run_dir, "test goal", params)

        call_kwargs = self.mock_orch.return_value.run.call_args[1]
        assert call_kwargs["effort"] == "high", \
            "effort='high' must be forwarded to orchestrator.run()"
        mock_apply.assert_called_once_with(self.fake_team, "high")

    def test_max_effort_passed_to_orchestrator_run(self, tmp_path):
        """effort='max' in params must reach orchestrator.run(effort='max')."""
        params = _minimal_params(effort="max")
        with patch("kodo.cli._launch._apply_effort_to_team"):
            launch_run(self.run_dir, "test goal", params)

        call_kwargs = self.mock_orch.return_value.run.call_args[1]
        assert call_kwargs["effort"] == "max"

    def test_standard_effort_default_passed_to_orchestrator(self, tmp_path):
        """No effort in params → default 'standard' reaches orchestrator.run()."""
        params = _minimal_params()  # no effort key
        launch_run(self.run_dir, "test goal", params)

        call_kwargs = self.mock_orch.return_value.run.call_args[1]
        assert call_kwargs["effort"] == "standard"

    def test_high_effort_modifies_system_prompt(self, tmp_path):
        """Non-standard effort must trigger build_orchestrator_prompt."""
        params = _minimal_params(effort="high")
        with (
            patch("kodo.cli._launch._apply_effort_to_team"),
            patch("kodo.prompts.roles.build_orchestrator_prompt",
                  return_value="effort-enhanced prompt") as mock_build,
        ):
            launch_run(self.run_dir, "test goal", params)

        mock_build.assert_called_once()
        # Verify the enhanced prompt was used to build the orchestrator
        orch_call_kwargs = self.mock_orch.call_args[1]
        assert orch_call_kwargs["system_prompt"] == "effort-enhanced prompt"


# ---------------------------------------------------------------------------
# A12: launch_run auto_commit flow-through (mutation-tested)
# ---------------------------------------------------------------------------


class TestLaunchRunAutoCommitFlowThrough:
    """Verify auto_commit from _resolve_auto_commit reaches orchestrator.run()."""

    @pytest.fixture(autouse=True)
    def _mock_deps(self, tmp_path):
        self.run_dir = RunDir.create(tmp_path)
        self.fake_team = {"worker": Agent(FakeSession(), "w")}
        self.preset = _fake_team_preset(build_team=lambda: self.fake_team)
        self.fake_result = RunResult(
            cycles=[CycleResult(exchanges=1, total_cost_usd=0.0, finished=True)],
        )
        with (
            patch("kodo.cli._launch.log") as mock_log,
            patch("kodo.cli._launch.get_team", return_value=self.preset),
            patch("kodo.cli._launch.load_team_config", return_value=None),
            patch("kodo.cli._launch.preflight_check_backends", return_value=[]),
            patch("kodo.cli._launch.build_orchestrator") as self.mock_orch,
            patch("kodo.cli._launch._build_advisor", return_value=None),
        ):
            mock_log.init = MagicMock(return_value=Path("/fake/log"))
            mock_log.emit = MagicMock()
            mock_log.print_stats_table = MagicMock()
            mock_log._start_time = None
            mock_log._fmt_time = MagicMock(return_value="0s")
            self.mock_orch.return_value.run.return_value = self.fake_result
            self.mock_orch.return_value.model = "test-model"
            yield

    def test_auto_commit_false_reaches_orchestrator(self, tmp_path):
        """auto_commit=False in params must reach orchestrator.run(auto_commit=False)."""
        params = _minimal_params(auto_commit=False)
        with patch("kodo.cli._launch._resolve_auto_commit", return_value=False):
            launch_run(self.run_dir, "test goal", params)

        call_kwargs = self.mock_orch.return_value.run.call_args[1]
        assert call_kwargs["auto_commit"] is False, \
            "auto_commit=False must be forwarded to orchestrator.run()"

    def test_auto_commit_true_reaches_orchestrator(self, tmp_path):
        """auto_commit=True in params must reach orchestrator.run(auto_commit=True)."""
        params = _minimal_params(auto_commit=True)
        with patch("kodo.cli._launch._resolve_auto_commit", return_value=True):
            launch_run(self.run_dir, "test goal", params)

        call_kwargs = self.mock_orch.return_value.run.call_args[1]
        assert call_kwargs["auto_commit"] is True

    def test_resolve_auto_commit_is_called(self, tmp_path):
        """launch_run must call _resolve_auto_commit (not hardcode the value)."""
        params = _minimal_params(auto_commit=True)
        with patch("kodo.cli._launch._resolve_auto_commit",
                    return_value=False) as mock_resolve:
            launch_run(self.run_dir, "test goal", params)

        mock_resolve.assert_called_once()
        # Even though params says True, _resolve_auto_commit returned False
        call_kwargs = self.mock_orch.return_value.run.call_args[1]
        assert call_kwargs["auto_commit"] is False


# ---------------------------------------------------------------------------
# A13: launch_run debug mode (mutation-tested)
# ---------------------------------------------------------------------------


class TestLaunchRunDebugMode:
    """Verify that debug=True in launch_run uses mock backends, not real ones."""

    @pytest.fixture(autouse=True)
    def _mock_deps(self, tmp_path):
        self.run_dir = RunDir.create(tmp_path)
        self.fake_result = RunResult(
            cycles=[CycleResult(exchanges=1, total_cost_usd=0.0, finished=True)],
        )
        with (
            patch("kodo.cli._launch.log") as mock_log,
            patch("kodo.cli._launch.get_team",
                  return_value=_fake_team_preset()),
        ):
            mock_log.init = MagicMock(return_value=Path("/fake/log"))
            mock_log.emit = MagicMock()
            mock_log.print_stats_table = MagicMock()
            mock_log._start_time = None
            mock_log._fmt_time = MagicMock(return_value="0s")
            yield

    def test_debug_mode_uses_mock_orchestrator(self, tmp_path):
        """debug=True must call build_debug_team and build_mock_orchestrator."""
        fake_team = {"worker": Agent(FakeSession(), "w")}
        mock_orch = MagicMock()
        mock_orch.run.return_value = self.fake_result
        mock_orch.model = "mock-A"

        class FakeDebugSession:
            def __init__(self, letter):
                self.letter = letter
                self.generated_tokens = 0
                self.seen_tokens = 0

        mock_alloc = MagicMock()
        mock_alloc.next.return_value = "A"
        mock_alloc.assignments = [("A", "orchestrator")]

        with (
            patch("kodo.debug.build_debug_team",
                  return_value=(fake_team, {"worker": FakeDebugSession("B")})) as mock_build_team,
            patch("kodo.debug.build_mock_orchestrator",
                  return_value=(mock_orch, FakeDebugSession("A"))) as mock_build_orch,
            patch("kodo.debug._allocator", mock_alloc),
        ):
            params = _minimal_params()
            result = launch_run(self.run_dir, "test goal", params, debug=True)

        mock_build_team.assert_called_once_with("full")
        mock_build_orch.assert_called_once()
        assert result.finished is True

    def test_debug_mode_does_not_call_real_orchestrator_builder(self, tmp_path):
        """debug=True must NOT call build_orchestrator (the real one)."""
        fake_team = {"worker": Agent(FakeSession(), "w")}
        mock_orch = MagicMock()
        mock_orch.run.return_value = self.fake_result
        mock_orch.model = "mock-A"

        class FakeDebugSession:
            def __init__(self, letter):
                self.letter = letter
                self.generated_tokens = 0
                self.seen_tokens = 0

        mock_alloc = MagicMock()
        mock_alloc.next.return_value = "A"
        mock_alloc.assignments = [("A", "orchestrator")]

        with (
            patch("kodo.debug.build_debug_team",
                  return_value=(fake_team, {"worker": FakeDebugSession("B")})),
            patch("kodo.debug.build_mock_orchestrator",
                  return_value=(mock_orch, FakeDebugSession("A"))),
            patch("kodo.debug._allocator", mock_alloc),
            patch("kodo.cli._launch.build_orchestrator") as mock_real_orch,
        ):
            params = _minimal_params()
            launch_run(self.run_dir, "test goal", params, debug=True)

        mock_real_orch.assert_not_called(), \
            "debug=True must NOT call the real build_orchestrator"

    def test_debug_false_does_not_use_mock(self, tmp_path):
        """debug=False must NOT call build_debug_team."""
        fake_team = {"worker": Agent(FakeSession(), "w")}
        preset = _fake_team_preset(build_team=lambda: fake_team)

        with (
            patch("kodo.cli._launch.get_team", return_value=preset),
            patch("kodo.cli._launch.load_team_config", return_value=None),
            patch("kodo.cli._launch.preflight_check_backends", return_value=[]),
            patch("kodo.cli._launch.build_orchestrator") as mock_real_orch,
            patch("kodo.cli._launch._resolve_auto_commit", return_value=False),
            patch("kodo.cli._launch._build_advisor", return_value=None),
        ):
            mock_real_orch.return_value.run.return_value = self.fake_result
            mock_real_orch.return_value.model = "test-model"
            params = _minimal_params()
            launch_run(self.run_dir, "test goal", params, debug=False)

        mock_real_orch.assert_called_once(), \
            "debug=False must call the real build_orchestrator"
