"""Tests for kodo.cli._test — tool forge, user story tracking, exploratory testing."""

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
        assert _is_recon_stage("Tool Forge & Story Mapping") is True
        assert _is_recon_stage("Reconnaissance") is True
        assert _is_recon_stage("Audit & Baseline") is True

    def test_recon_negative(self):
        assert _is_recon_stage("Integration Testing") is False

    def test_report_variants(self):
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
                    name="Tool Forge & Story Mapping",
                    description="Build tools",
                    acceptance_criteria="Done",
                    persist_changes=True,
                ),
                GoalStage(
                    index=2,
                    name="Testing",
                    description="Test",
                    acceptance_criteria="Findings",
                    parallel_group=1,
                ),
                GoalStage(
                    index=3,
                    name="Regression Tests & Report",
                    description="Report",
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

    def test_tool_forge_in_prompt(self, tmp_path):
        """Discovery prompt must mention tool building."""
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
            "Tool Forge" in captured["prompt"] or "tool" in captured["prompt"].lower()
        )

    def test_story_mapping_in_prompt(self, tmp_path):
        """Discovery prompt must mention user stories."""
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
            "user stor" in captured["prompt"].lower()
            or "story" in captured["prompt"].lower()
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

    def test_adds_missing_report(self):
        plan = GoalPlan(
            context="Test",
            stages=[
                GoalStage(
                    index=1,
                    name="Tool Forge",
                    description="Build",
                    acceptance_criteria="Done",
                ),
            ],
        )
        result = _validate_test_plan(plan, "/tmp/r.md", "/tmp/run")
        assert any(_is_report_stage(s.name) for s in result.stages)

    def test_reindexes(self):
        plan = GoalPlan(
            context="Test",
            stages=[
                GoalStage(
                    index=5,
                    name="Tool Forge",
                    description="Build",
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
                    name="Regression Tests & Report",
                    description="Report",
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
                    name="Tool Forge",
                    description="Build",
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
                    name="Regression Tests & Report",
                    description="Report",
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
    def test_has_four_stages(self):
        plan = _build_test_fallback_plan("/tmp/run/test-report.md")
        assert len(plan.stages) == 4

    def test_first_stage_is_tool_forge(self):
        plan = _build_test_fallback_plan("/tmp/run/test-report.md")
        assert "tool forge" in plan.stages[0].name.lower()

    def test_first_stage_mentions_user_stories(self):
        plan = _build_test_fallback_plan("/tmp/run/test-report.md")
        assert "stor" in plan.stages[0].description.lower()

    def test_first_stage_mentions_building_tools(self):
        plan = _build_test_fallback_plan("/tmp/run/test-report.md")
        desc = plan.stages[0].description.lower()
        assert "build" in desc and "tool" in desc

    def test_last_stage_is_report(self):
        plan = _build_test_fallback_plan("/tmp/run/test-report.md")
        assert _is_report_stage(plan.stages[-1].name)

    def test_middle_stages_parallel(self):
        plan = _build_test_fallback_plan("/tmp/run/test-report.md")
        assert plan.stages[1].parallel_group == 1
        assert plan.stages[2].parallel_group == 1

    def test_exploration_stages_reference_tools(self):
        """Exploration stages should tell agents to use tools from Stage 1."""
        plan = _build_test_fallback_plan("/tmp/run/test-report.md")
        for stage in plan.stages[1:3]:
            assert (
                "stage 1" in stage.description.lower()
                or "tool" in stage.description.lower()
            )

    def test_tool_forge_persists(self):
        plan = _build_test_fallback_plan("/tmp/run/test-report.md")
        assert plan.stages[0].persist_changes is True  # tools are deliverable

    def test_exploration_read_only(self):
        plan = _build_test_fallback_plan("/tmp/run/test-report.md")
        assert plan.stages[1].persist_changes is False
        assert plan.stages[2].persist_changes is False

    def test_focus_in_context(self):
        plan = _build_test_fallback_plan("/tmp/run/test-report.md", focus="auth")
        assert "auth" in plan.context

    def test_target_in_context(self):
        plan = _build_test_fallback_plan("/tmp/run/test-report.md", targets=["src/"])
        assert "src/" in plan.context

    def test_context_emphasizes_tools_and_stories(self):
        plan = _build_test_fallback_plan("/tmp/run/test-report.md")
        ctx = plan.context.lower()
        assert "tool" in ctx
        assert "stor" in ctx or "workflow" in ctx


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
        report = (
            "## Summary\n"
            "- **Findings:** 8 (3/3/2)\n"
            "- **Bugs confirmed:** 3\n"
            "- **Usability gaps:** 2\n"
            "- **Regression tests written:** 4\n\n"
            "## Critical Findings\n"
            "- **F1:** bug1\n- **F2:** bug2\n- **F3:** bug3\n\n"
            "## Regression Tests Added\n"
            "- tests/a.py:t1 — F1\n- tests/b.py:t2 — F2\n\n"
            "## Untestable Gaps\n- network — needs infra\n"
        )
        r = parse_test_report_summary(report)
        assert r["findings_count"] == 8
        assert r["bugs_confirmed"] == 3
        assert r["critical_count"] == 3
        assert r["regression_count"] == 2
        assert r["untestable_count"] == 1

    def test_empty(self):
        r = parse_test_report_summary("")
        assert r["critical_count"] == 0


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
            "## Untestable Gaps\n- websocket — needs mock\n"
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
    def test_discovery_emphasizes_tools(self):
        from kodo.prompts.test import DISCOVERY_PROMPT

        p = DISCOVERY_PROMPT.lower()
        assert "tool" in p
        assert "build" in p or "forge" in p

    def test_discovery_emphasizes_stories(self):
        from kodo.prompts.test import DISCOVERY_PROMPT

        assert "stor" in DISCOVERY_PROMPT.lower()

    def test_orchestrator_prevents_unit_test_fallback(self):
        from kodo.prompts.roles import TEST_ORCHESTRATOR_SYSTEM_PROMPT

        p = TEST_ORCHESTRATOR_SYSTEM_PROMPT.lower()
        assert "unit test" in p  # mentioned in the "NOT" context
        assert "tool" in p

    def test_report_has_stories_table(self):
        from kodo.prompts.test import TEST_REPORT_FORMAT

        assert "User Stories Tested" in TEST_REPORT_FORMAT

    def test_report_has_blocked_stories(self):
        from kodo.prompts.test import TEST_REPORT_FORMAT

        assert "Blocked Stories" in TEST_REPORT_FORMAT

    def test_report_has_tools_built(self):
        from kodo.prompts.test import TEST_REPORT_FORMAT

        assert "Testing Tools Built" in TEST_REPORT_FORMAT

    def test_tool_forge_guidance_lists_tool_types(self):
        from kodo.prompts.test import TOOL_FORGE_GUIDANCE

        g = TOOL_FORGE_GUIDANCE.lower()
        assert "cli" in g
        assert "docker" in g or "environment" in g
        assert "blocked" in g  # mentions what to do when tools aren't available

    def test_story_file_constant(self):
        from kodo.prompts.test import USER_STORY_FILE

        assert USER_STORY_FILE == ".kodo/test-stories.md"
