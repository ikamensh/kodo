"""Tests for kodo.cli._test — user-experience-first testing with edge case probing."""

from __future__ import annotations

from unittest.mock import patch

from kodo.cli._shared import slugify
from kodo.cli._test import (
    _build_test_fallback_plan,
    _is_recon_stage,
    _is_report_stage,
    _validate_test_plan,
    extract_test_section,
    parse_test_report_summary,
    run_test_discovery,
)
from kodo.log import RunDir
from kodo.orchestrators.base import GoalPlan, GoalStage


# ---------------------------------------------------------------------------
# Stage detection
# ---------------------------------------------------------------------------


class TestSlugify:
    def test_basic(self):
        assert slugify("Tool Forge") == "tool-forge"

    def test_empty(self):
        assert slugify("!!!") == "stage"


class TestStageDetection:
    def test_recon_variants(self):
        assert _is_recon_stage("Setup & Discovery") is True
        assert _is_recon_stage("Install & Feature Map") is True
        assert _is_recon_stage("Reconnaissance") is True
        assert _is_recon_stage("Tool Forge & Story Mapping") is True

    def test_recon_negative(self):
        assert _is_recon_stage("Integration Testing") is False

    def test_report_variants(self):
        assert _is_report_stage("Triage & Regression Tests") is True
        assert _is_report_stage("Regression Tests & Report") is True
        assert _is_report_stage("Final Report") is True

    def test_report_negative(self):
        assert _is_report_stage("Integration Testing") is False


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


class TestRunTestDiscovery:
    def test_returns_plan(self, tmp_path):
        run_dir = RunDir.create(tmp_path, "test")
        raw_plan = GoalPlan(
            context="Python CLI project",
            stages=[
                GoalStage(
                    index=1,
                    name="Setup & Discovery",
                    description="Map features",
                    acceptance_criteria="Done",
                    persist_changes=True,
                ),
                GoalStage(
                    index=2,
                    name="Feature Walkthroughs",
                    description="Test features",
                    acceptance_criteria="Findings",
                    parallel_group=1,
                ),
                GoalStage(
                    index=3,
                    name="Triage & Regression Tests",
                    description="Triage",
                    acceptance_criteria="Report written",
                    persist_changes=True,
                ),
            ],
        )
        with patch(
            "kodo.cli._intake.run_single_turn_plan",
            autospec=True,
            return_value=raw_plan,
        ):
            result = run_test_discovery(run_dir, "/tmp/test-report.md")
        assert result is not None
        assert len(result.stages) >= 3

    def test_returns_none_on_empty(self, tmp_path):
        run_dir = RunDir.create(tmp_path, "test")
        with patch(
            "kodo.cli._intake.run_single_turn_plan", autospec=True, return_value=None
        ):
            assert run_test_discovery(run_dir, "/tmp/test-report.md") is None

    def test_focus_in_prompt(self, tmp_path):
        run_dir = RunDir.create(tmp_path, "test")
        captured = {}

        def capture(run_dir, system_prompt="", **kw):
            captured["prompt"] = system_prompt
            return None

        with patch(
            "kodo.cli._intake.run_single_turn_plan", autospec=True, side_effect=capture
        ):
            run_test_discovery(run_dir, "/tmp/r.md", focus="auth")
        assert "auth" in captured["prompt"]

    def test_targets_in_prompt(self, tmp_path):
        run_dir = RunDir.create(tmp_path, "test")
        captured = {}

        def capture(run_dir, system_prompt="", **kw):
            captured["prompt"] = system_prompt
            return None

        with patch(
            "kodo.cli._intake.run_single_turn_plan", autospec=True, side_effect=capture
        ):
            run_test_discovery(run_dir, "/tmp/r.md", targets=["src/cli/"])
        assert "src/cli/" in captured["prompt"]
        assert "Target Scope" in captured["prompt"]

    def test_feature_testing_in_prompt(self, tmp_path):
        """Discovery prompt must mention features and testing."""
        run_dir = RunDir.create(tmp_path, "test")
        captured = {}

        def capture(run_dir, system_prompt="", **kw):
            captured["prompt"] = system_prompt
            return None

        with patch(
            "kodo.cli._intake.run_single_turn_plan", autospec=True, side_effect=capture
        ):
            run_test_discovery(run_dir, "/tmp/r.md")
        prompt = captured["prompt"].lower()
        assert "feature" in prompt or "workflow" in prompt or "install" in prompt

    def test_user_perspective_in_prompt(self, tmp_path):
        """Discovery prompt must mention real user perspective."""
        run_dir = RunDir.create(tmp_path, "test")
        captured = {}

        def capture(run_dir, system_prompt="", **kw):
            captured["prompt"] = system_prompt
            return None

        with patch(
            "kodo.cli._intake.run_single_turn_plan", autospec=True, side_effect=capture
        ):
            run_test_discovery(run_dir, "/tmp/r.md")
        assert (
            "real user" in captured["prompt"].lower()
            or "user" in captured["prompt"].lower()
        )


# ---------------------------------------------------------------------------
# Validate plan
# ---------------------------------------------------------------------------


class TestValidateTestPlan:
    def test_adds_missing_recon(self):
        plan = GoalPlan(
            context="Test",
            stages=[
                GoalStage(
                    index=1,
                    name="Testing",
                    description="Test",
                    acceptance_criteria="Done",
                ),
            ],
        )
        result = _validate_test_plan(plan, "/tmp/r.md", "/tmp/run")
        assert any(_is_recon_stage(s.name) for s in result.stages)

    def test_injected_recon_is_setup(self):
        plan = GoalPlan(
            context="Test",
            stages=[
                GoalStage(
                    index=1,
                    name="Testing",
                    description="Test",
                    acceptance_criteria="Done",
                ),
            ],
        )
        result = _validate_test_plan(plan, "/tmp/r.md", "/tmp/run")
        recon = [s for s in result.stages if _is_recon_stage(s.name)][0]
        assert "Setup" in recon.name or "Discovery" in recon.name

    def test_adds_missing_report(self):
        plan = GoalPlan(
            context="Test",
            stages=[
                GoalStage(
                    index=1,
                    name="Setup & Discovery",
                    description="Map features",
                    acceptance_criteria="Done",
                ),
            ],
        )
        result = _validate_test_plan(plan, "/tmp/r.md", "/tmp/run")
        assert any(_is_report_stage(s.name) for s in result.stages)

    def test_injected_report_is_triage(self):
        plan = GoalPlan(
            context="Test",
            stages=[
                GoalStage(
                    index=1,
                    name="Setup & Discovery",
                    description="Map features",
                    acceptance_criteria="Done",
                ),
            ],
        )
        result = _validate_test_plan(plan, "/tmp/r.md", "/tmp/run")
        report = [s for s in result.stages if _is_report_stage(s.name)][0]
        assert "Triage" in report.name

    def test_reindexes(self):
        plan = GoalPlan(
            context="Test",
            stages=[
                GoalStage(
                    index=5,
                    name="Setup & Discovery",
                    description="Map",
                    acceptance_criteria="Done",
                ),
                GoalStage(
                    index=10,
                    name="Testing",
                    description="Test",
                    acceptance_criteria="Done",
                ),
                GoalStage(
                    index=15,
                    name="Triage & Regression Tests",
                    description="Triage",
                    acceptance_criteria="Done",
                ),
            ],
        )
        result = _validate_test_plan(plan, "/tmp/r.md", "/tmp/run")
        assert [s.index for s in result.stages] == [1, 2, 3]

    def test_preserves_persist_changes(self):
        plan = GoalPlan(
            context="Test",
            stages=[
                GoalStage(
                    index=1,
                    name="Setup & Discovery",
                    description="Map",
                    acceptance_criteria="Done",
                    persist_changes=True,
                ),
                GoalStage(
                    index=2,
                    name="Explore",
                    description="Explore",
                    acceptance_criteria="Done",
                    persist_changes=False,
                ),
                GoalStage(
                    index=3,
                    name="Triage & Regression Tests",
                    description="Triage",
                    acceptance_criteria="Done",
                    persist_changes=True,
                ),
            ],
        )
        result = _validate_test_plan(plan, "/tmp/r.md", "/tmp/run")
        assert result.stages[0].persist_changes is True
        assert result.stages[1].persist_changes is False
        assert result.stages[2].persist_changes is True


# ---------------------------------------------------------------------------
# Fallback plan
# ---------------------------------------------------------------------------


class TestBuildTestFallbackPlan:
    """Consolidated tests for _build_test_fallback_plan() structure."""

    def test_plan_structure(self):
        """4 stages: setup first (with install/tool refs), triage last, sequential indices."""
        plan = _build_test_fallback_plan("/tmp/run/test-report.md")
        assert len(plan.stages) == 4
        first = plan.stages[0]
        assert "setup" in first.name.lower() or "discover" in first.name.lower()
        assert "install" in first.description.lower()
        assert "tool" in first.description.lower() or "build" in first.description.lower()
        assert _is_report_stage(plan.stages[-1].name)
        assert "triage" in plan.stages[-1].name.lower()

    def test_parallelism_and_persistence(self):
        """Middle stages run in parallel and are read-only; setup persists."""
        plan = _build_test_fallback_plan("/tmp/run/test-report.md")
        assert plan.stages[0].persist_changes is True
        assert plan.stages[1].parallel_group == 1
        assert plan.stages[2].parallel_group == 1
        assert plan.stages[1].persist_changes is False
        assert plan.stages[2].persist_changes is False

    def test_content_and_customization(self):
        """Middle stages cover features + edges; focus/targets appear in context."""
        plan = _build_test_fallback_plan("/tmp/run/test-report.md")
        all_text = " ".join(
            f"{s.name} {s.description}" for s in plan.stages[1:3]
        ).lower()
        assert "feature" in all_text or "workflow" in all_text
        assert "edge" in all_text or "boundary" in all_text or "invalid" in all_text
        ctx = plan.context.lower()
        assert "user" in ctx or "feature" in ctx or "workflow" in ctx

        # focus and targets propagate to context
        plan_focus = _build_test_fallback_plan("/tmp/run/test-report.md", focus="auth")
        assert "auth" in plan_focus.context
        plan_target = _build_test_fallback_plan(
            "/tmp/run/test-report.md", targets=["src/"]
        )
        assert "src/" in plan_target.context


# ---------------------------------------------------------------------------
# Report parsing
# ---------------------------------------------------------------------------


class TestExtractTestSection:
    def test_extracts(self):
        text = "## Critical Findings\n- **F1:** bug\n## Other\n"
        assert "F1" in extract_test_section(text, "Critical Findings")

    def test_stops_at_next(self):
        text = "## A\n- a\n## B\n- b\n"
        assert "b" not in extract_test_section(text, "A")

    def test_missing(self):
        assert extract_test_section("## A\n", "B") == ""


class TestParseTestReportSummary:
    def test_full_report(self):
        """New report format with flat Findings section."""
        report = (
            "## Summary\n"
            "- **Features tested:** 5\n"
            "- **Findings:** 3\n"
            "- **Regression tests written:** 2\n\n"
            "## Findings\n"
            "- **F1:** crash on empty input\n"
            "- **F2:** race on concurrent writes\n"
            "- **F3:** misleading error message\n\n"
            "## Regression Tests & Fixes\n"
            "- tests/a.py:t1 — F1\n- tests/b.py:t2 — F2\n\n"
            "## Blocked Workflows\n- Docker testing — needs container\n"
        )
        r = parse_test_report_summary(report)
        assert r["findings_count"] == 3
        assert r["findings_item_count"] == 3
        assert r["regression_count"] == 2
        assert r["blocked_count"] == 1

    def test_empty(self):
        r = parse_test_report_summary("")
        assert r["findings_item_count"] == 0
        assert r["blocked_count"] == 0


# ---------------------------------------------------------------------------
# Prior-run awareness
# ---------------------------------------------------------------------------


class TestCollectPriorTestWork:
    def test_collects(self, tmp_path):
        from kodo.cli._test import _collect_prior_test_work

        run_dir = RunDir.create(tmp_path, "current")
        runs = tmp_path / "runs"
        prev = runs / "prev"
        prev.mkdir(parents=True)
        (prev / "test-report.md").write_text(
            "## Regression Tests & Fixes\n- tests/a.py:t — F1\n\n"
            "## Blocked Workflows\n- websocket — needs mock\n",
            encoding="utf-8",
        )
        with patch("kodo.log._runs_root", autospec=True, return_value=runs):
            result = _collect_prior_test_work(run_dir)
        assert "tests/a.py" in result
        assert "websocket" in result

    def test_skips_current(self, tmp_path):
        from kodo.cli._test import _collect_prior_test_work

        run_dir = RunDir.create(tmp_path, "current")
        runs = tmp_path / "runs"
        (runs / "current").mkdir(parents=True)
        ((runs / "current") / "test-report.md").write_text(
            "## Regression Tests Added\n- x\n"
        )
        with patch("kodo.log._runs_root", autospec=True, return_value=runs):
            assert _collect_prior_test_work(run_dir) == ""

    def test_empty(self, tmp_path):
        from kodo.cli._test import _collect_prior_test_work

        run_dir = RunDir.create(tmp_path, "current")
        runs = tmp_path / "runs"
        runs.mkdir(parents=True)
        with patch("kodo.log._runs_root", autospec=True, return_value=runs):
            assert _collect_prior_test_work(run_dir) == ""


# ---------------------------------------------------------------------------
# Prompt content — structural checks
# ---------------------------------------------------------------------------


class TestPromptContent:
    def test_discovery_mentions_features(self):
        from kodo.prompts.test import DISCOVERY_PROMPT

        p = DISCOVERY_PROMPT.lower()
        assert "feature" in p or "workflow" in p

    def test_discovery_mentions_user_perspective(self):
        from kodo.prompts.test import DISCOVERY_PROMPT

        assert "real user" in DISCOVERY_PROMPT.lower()

    def test_discovery_mentions_install(self):
        from kodo.prompts.test import DISCOVERY_PROMPT

        assert "install" in DISCOVERY_PROMPT.lower()

    def test_orchestrator_pushes_for_coverage(self):
        from kodo.prompts.roles import TEST_ORCHESTRATOR_SYSTEM_PROMPT

        p = TEST_ORCHESTRATOR_SYSTEM_PROMPT.lower()
        assert "zero findings" in p or "push back" in p

    def test_orchestrator_mentions_features(self):
        from kodo.prompts.roles import TEST_ORCHESTRATOR_SYSTEM_PROMPT

        p = TEST_ORCHESTRATOR_SYSTEM_PROMPT.lower()
        assert "feature" in p or "workflow" in p

    def test_report_has_feature_coverage_table(self):
        from kodo.prompts.test import TEST_REPORT_FORMAT

        assert "Feature Coverage" in TEST_REPORT_FORMAT

    def test_report_has_self_critique(self):
        from kodo.prompts.test import TEST_REPORT_FORMAT

        assert "Self-Critique" in TEST_REPORT_FORMAT

    def test_report_has_blocked_workflows(self):
        from kodo.prompts.test import TEST_REPORT_FORMAT

        assert "Blocked Workflows" in TEST_REPORT_FORMAT

    def test_tool_forge_guidance_mentions_install(self):
        from kodo.prompts.test import TOOL_FORGE_GUIDANCE

        g = TOOL_FORGE_GUIDANCE.lower()
        assert "install" in g or "build" in g

    def test_feature_coverage_file_constant(self):
        from kodo.prompts.test import FEATURE_COVERAGE_FILE

        assert FEATURE_COVERAGE_FILE == ".kodo/test-coverage.md"

    def test_methodology_covers_full_spectrum(self):
        from kodo.prompts.test import METHODOLOGY_LIBRARY

        m = METHODOLOGY_LIBRARY.lower()
        assert "install" in m
        assert "feature" in m or "workflow" in m
        assert "edge" in m or "boundary" in m
        assert "error" in m
        assert "concurrency" in m or "interrupt" in m

    def test_time_guidance_has_split(self):
        from kodo.prompts.test import TEST_TIME_GUIDANCE

        t = TEST_TIME_GUIDANCE.lower()
        assert "15%" in t
        assert "60%" in t
