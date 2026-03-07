"""Tests for kodo.cli._improve — improve discovery, validation, and prior findings."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kodo.cli._improve import (
    _build_fallback_plan,
    _collect_prior_needs_decision,
    _extract_section,
    _is_fix_stage,
    _is_triage_stage,
    _slugify,
    _validate_improve_plan,
    run_improve_discovery,
)
from kodo.log import RunDir
from kodo.orchestrators.base import GoalPlan, GoalStage
from tests.conftest import make_scripted_session


# ---------------------------------------------------------------------------
# _slugify
# ---------------------------------------------------------------------------


class TestSlugify:
    def test_basic_name(self):
        assert _slugify("Baseline Analysis") == "baseline-analysis"

    def test_special_chars(self):
        assert _slugify("Happy Path: Testing!") == "happy-path-testing"

    def test_empty_name_returns_stage(self):
        assert _slugify("!!!") == "stage"

    def test_leading_trailing_dashes_stripped(self):
        assert _slugify("  --test--  ") == "test"


# ---------------------------------------------------------------------------
# _is_triage_stage / _is_fix_stage
# ---------------------------------------------------------------------------


class TestStageDetection:
    def test_triage_detected(self):
        assert _is_triage_stage("Triage & Verify") is True
        assert _is_triage_stage("VERIFY FINDINGS") is True

    def test_triage_not_detected(self):
        assert _is_triage_stage("Happy Path Testing") is False

    def test_fix_detected(self):
        assert _is_fix_stage("Fix & Report") is True
        assert _is_fix_stage("Generate Report") is True

    def test_fix_not_detected(self):
        assert _is_fix_stage("Baseline Analysis") is False


# ---------------------------------------------------------------------------
# run_improve_discovery
# ---------------------------------------------------------------------------


class TestRunImproveDiscovery:
    def test_returns_plan_when_discovery_succeeds(self, tmp_path):
        """When single-turn plan produces valid GoalPlan, returns validated plan."""
        run_dir = RunDir.create(tmp_path, "test")
        raw_plan = GoalPlan(
            context="Test project",
            stages=[
                GoalStage(index=1, name="Baseline", description="Do baseline", acceptance_criteria="Done"),
                GoalStage(index=2, name="Triage & Verify", description="Triage", acceptance_criteria="All triaged"),
                GoalStage(index=3, name="Fix & Report", description="Fix", acceptance_criteria="Report done"),
            ],
        )

        with patch("kodo.cli._intake.run_single_turn_plan", return_value=raw_plan):
            result = run_improve_discovery(run_dir, "/tmp/report.md")

        assert result is not None
        assert isinstance(result, GoalPlan)
        assert len(result.stages) >= 3

    def test_returns_none_on_no_plan(self, tmp_path):
        """When single-turn plan returns None, discovery returns None."""
        run_dir = RunDir.create(tmp_path, "test")

        with patch("kodo.cli._intake.run_single_turn_plan", return_value=None):
            result = run_improve_discovery(run_dir, "/tmp/report.md")

        assert result is None

    def test_returns_none_on_empty_plan(self, tmp_path):
        """When plan has no stages, returns None."""
        run_dir = RunDir.create(tmp_path, "test")
        empty_plan = GoalPlan(context="Test", stages=[])

        with patch("kodo.cli._intake.run_single_turn_plan", return_value=empty_plan):
            result = run_improve_discovery(run_dir, "/tmp/report.md")

        assert result is None

    def test_focus_area_appended_to_prompt(self, tmp_path):
        """When focus is provided, it should appear in the prompt."""
        run_dir = RunDir.create(tmp_path, "test")
        captured_kwargs = {}

        def capture_plan(run_dir, system_prompt="", initial_message="", **kwargs):
            captured_kwargs["system_prompt"] = system_prompt
            captured_kwargs["initial_message"] = initial_message
            return None

        with patch("kodo.cli._intake.run_single_turn_plan", side_effect=capture_plan):
            run_improve_discovery(run_dir, "/tmp/report.md", focus="performance")

        assert "performance" in captured_kwargs["system_prompt"]
        assert "performance" in captured_kwargs["initial_message"]

    def test_docker_detected(self, tmp_path):
        """When Docker is available, prompt should include Docker info."""
        run_dir = RunDir.create(tmp_path, "test")
        captured_kwargs = {}

        def capture_plan(run_dir, system_prompt="", **kwargs):
            captured_kwargs["system_prompt"] = system_prompt
            return None

        with (
            patch("kodo.cli._intake.run_single_turn_plan", side_effect=capture_plan),
            patch("kodo.cli._improve.detect_docker", return_value=True),
        ):
            run_improve_discovery(run_dir, "/tmp/report.md")

        assert "Docker" in captured_kwargs["system_prompt"]
        assert "available" in captured_kwargs["system_prompt"]

    def test_no_docker(self, tmp_path):
        """When Docker is not available, prompt should say so."""
        run_dir = RunDir.create(tmp_path, "test")
        captured_kwargs = {}

        def capture_plan(run_dir, system_prompt="", **kwargs):
            captured_kwargs["system_prompt"] = system_prompt
            return None

        with (
            patch("kodo.cli._intake.run_single_turn_plan", side_effect=capture_plan),
            patch("kodo.cli._improve.detect_docker", return_value=False),
        ):
            run_improve_discovery(run_dir, "/tmp/report.md")

        assert "not available" in captured_kwargs["system_prompt"]

    def test_prior_needs_decision_passed(self, tmp_path):
        """Prior needs-decision items are forwarded to validation."""
        run_dir = RunDir.create(tmp_path, "test")
        plan = GoalPlan(
            context="Test",
            stages=[
                GoalStage(index=1, name="Analysis", description="Analyze", acceptance_criteria="Done"),
                GoalStage(index=2, name="Triage & Verify", description="Triage", acceptance_criteria="Triaged"),
                GoalStage(index=3, name="Fix & Report", description="Fix", acceptance_criteria="Fixed"),
            ],
        )
        prior = "\n## Prior items\n- issue1\n"

        with patch("kodo.cli._intake.run_single_turn_plan", return_value=plan):
            result = run_improve_discovery(run_dir, "/tmp/report.md", prior_needs_decision=prior)

        assert result is not None
        # Prior items should be in triage stage description
        triage = [s for s in result.stages if _is_triage_stage(s.name)][0]
        assert "Prior items" in triage.description


# ---------------------------------------------------------------------------
# _collect_prior_needs_decision
# ---------------------------------------------------------------------------


class TestCollectPriorNeedsDecision:
    def test_collects_from_previous_reports(self, tmp_path):
        """Scans previous reports and extracts 'Needs decision' items."""
        run_dir = RunDir.create(tmp_path, "current_run")

        # Create a previous run's report
        runs_root = tmp_path / "runs"
        prev_run = runs_root / "prev_run"
        prev_run.mkdir(parents=True)
        (prev_run / "improve-report.md").write_text(
            "## Auto-fixed\n- fixed thing\n\n"
            "## Needs decision\n"
            "- consider refactoring auth module\n"
            "- evaluate migration to async\n"
        )

        with patch("kodo.log._runs_root", return_value=runs_root):
            result = _collect_prior_needs_decision(run_dir)

        assert "consider refactoring auth module" in result
        assert "evaluate migration to async" in result
        assert "Prior unresolved items" in result

    def test_skips_current_run(self, tmp_path):
        """Should skip the current run's report."""
        run_dir = RunDir.create(tmp_path, "current_run")

        runs_root = tmp_path / "runs"
        current = runs_root / "current_run"
        current.mkdir(parents=True)
        (current / "improve-report.md").write_text(
            "## Needs decision\n- should not appear\n"
        )

        with patch("kodo.log._runs_root", return_value=runs_root):
            result = _collect_prior_needs_decision(run_dir)

        assert result == ""

    def test_returns_empty_when_no_reports(self, tmp_path):
        """No previous reports → empty string."""
        run_dir = RunDir.create(tmp_path, "current_run")
        runs_root = tmp_path / "runs"
        runs_root.mkdir(parents=True)

        with patch("kodo.log._runs_root", return_value=runs_root):
            result = _collect_prior_needs_decision(run_dir)

        assert result == ""

    def test_returns_empty_when_no_runs_dir(self, tmp_path):
        """No runs directory → empty string."""
        run_dir = RunDir.create(tmp_path, "current_run")
        nonexistent = tmp_path / "nonexistent_runs"

        with patch("kodo.log._runs_root", return_value=nonexistent):
            result = _collect_prior_needs_decision(run_dir)

        assert result == ""

    def test_handles_unreadable_report(self, tmp_path):
        """OSError reading report → skip it, don't crash."""
        run_dir = RunDir.create(tmp_path, "current_run")

        runs_root = tmp_path / "runs"
        prev = runs_root / "prev"
        prev.mkdir(parents=True)
        report = prev / "improve-report.md"
        report.write_text("## Needs decision\n- item\n")

        original_read_text = Path.read_text

        def patched_read_text(self, *args, **kwargs):
            if str(self) == str(report):
                raise OSError("permission denied")
            return original_read_text(self, *args, **kwargs)

        with (
            patch("kodo.log._runs_root", return_value=runs_root),
            patch.object(Path, "read_text", patched_read_text),
        ):
            result = _collect_prior_needs_decision(run_dir)

        assert result == ""

    def test_ignores_non_bullet_lines(self, tmp_path):
        """Only lines starting with '- ' are collected."""
        run_dir = RunDir.create(tmp_path, "current_run")

        runs_root = tmp_path / "runs"
        prev = runs_root / "prev"
        prev.mkdir(parents=True)
        (prev / "improve-report.md").write_text(
            "## Needs decision\n"
            "Some preamble text.\n"
            "- real item\n"
            "Not a bullet.\n"
        )

        with patch("kodo.log._runs_root", return_value=runs_root):
            result = _collect_prior_needs_decision(run_dir)

        assert "real item" in result
        assert "Some preamble text" not in result


# ---------------------------------------------------------------------------
# _build_fallback_plan with focus
# ---------------------------------------------------------------------------


class TestBuildFallbackPlanFocus:
    def test_focus_area_in_context(self):
        """Focus area should appear in plan context."""
        plan = _build_fallback_plan("/tmp/report.md", focus="security")
        assert "security" in plan.context

    def test_no_focus_no_extra_context(self):
        """Without focus, no focus section in context."""
        plan = _build_fallback_plan("/tmp/report.md")
        assert "Focus area" not in plan.context
