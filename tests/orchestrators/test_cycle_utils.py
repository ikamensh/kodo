"""Tests for kodo.orchestrators.cycle_utils — apply_done_signal, build_cycle_prompt."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from kodo.orchestrators.cycle_utils import apply_done_signal, build_cycle_prompt
from kodo.orchestrators.types import CycleResult, DoneSignal


# ── apply_done_signal ───────────────────────────────────────────────────


class TestApplyDoneSignal:
    def _make(self, *, terminal, summary="done", success=False):
        ds = DoneSignal()
        ds.called = True
        ds.terminal = terminal
        ds.summary = summary
        ds.success = success
        return ds

    def test_not_called_is_noop(self):
        result = CycleResult()
        ds = DoneSignal()  # called=False by default
        apply_done_signal(result, ds)
        assert result.finished is False
        assert result.summary == ""

    def test_goal_done(self):
        result = CycleResult()
        apply_done_signal(result, self._make(terminal="goal_done", summary="all done"))
        assert result.finished is True
        assert result.success is True
        assert result.summary == "all done"

    def test_end_cycle(self):
        result = CycleResult()
        apply_done_signal(result, self._make(terminal="end_cycle", summary="partial"))
        assert result.finished is False
        assert result.success is False
        assert result.summary == "partial"

    def test_raise_issue(self):
        result = CycleResult()
        apply_done_signal(result, self._make(terminal="raise_issue", summary="blocked"))
        assert result.finished is True
        assert result.success is False
        assert result.summary == "blocked"

    def test_legacy_uses_success_field(self):
        result = CycleResult()
        apply_done_signal(
            result, self._make(terminal="legacy", summary="ok", success=True)
        )
        assert result.finished is True
        assert result.success is True

    def test_legacy_failure(self):
        result = CycleResult()
        apply_done_signal(
            result, self._make(terminal="legacy", summary="fail", success=False)
        )
        assert result.finished is True
        assert result.success is False

    def test_unknown_terminal_treated_as_legacy(self):
        result = CycleResult()
        apply_done_signal(
            result, self._make(terminal=None, summary="mystery", success=True)
        )
        assert result.finished is True
        assert result.success is True


# ── build_cycle_prompt ──────────────────────────────────────────────────


class TestBuildCyclePrompt:
    def test_basic_prompt_contains_goal_and_dir(self, tmp_path: Path):
        with patch(
            "kodo.orchestrators.run_status.read_run_status", autospec=True, return_value=""
        ):
            prompt = build_cycle_prompt("Build X", tmp_path)
        assert "Build X" in prompt
        assert str(tmp_path) in prompt
        assert "# Goal" in prompt

    def test_prior_summary_appended(self, tmp_path: Path):
        with patch(
            "kodo.orchestrators.run_status.read_run_status", autospec=True, return_value=""
        ):
            prompt = build_cycle_prompt("Build X", tmp_path, prior_summary="Did Y")
        assert "Previous progress" in prompt
        assert "Did Y" in prompt
        assert "Continue working" in prompt

    def test_no_prior_summary_section_when_empty(self, tmp_path: Path):
        with patch(
            "kodo.orchestrators.run_status.read_run_status", autospec=True, return_value=""
        ):
            prompt = build_cycle_prompt("Build X", tmp_path, prior_summary="")
        assert "Previous progress" not in prompt

    def test_run_status_included(self, tmp_path: Path):
        with patch(
            "kodo.orchestrators.run_status.read_run_status", autospec=True,
            return_value="## Status\nAll green",
        ):
            prompt = build_cycle_prompt("Build X", tmp_path)
        assert "All green" in prompt
