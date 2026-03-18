"""Tests for kodo.cli._test — attack surface analysis, fault injection, breakage-oriented testing."""

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
        assert _is_recon_stage("Attack Surface Analysis") is True
        assert _is_recon_stage("Tool Forge & Story Mapping") is True
        assert _is_recon_stage("Reconnaissance") is True
        assert _is_recon_stage("Audit & Baseline") is True

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
                    name="Attack Surface Analysis",
                    description="Map attack surfaces",
                    acceptance_criteria="Done",
                    persist_changes=True,
                ),
                GoalStage(
                    index=2,
                    name="Fault Injection",
                    description="Inject faults",
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

    def test_attack_tooling_in_prompt(self, tmp_path):
        """Discovery prompt must mention attack tooling."""
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
        assert "attack" in prompt or "fault" in prompt or "break" in prompt

    def test_attack_surface_in_prompt(self, tmp_path):
        """Discovery prompt must mention attack surfaces."""
        run_dir = RunDir.create(tmp_path, "test")
        captured = {}

        def capture(run_dir, system_prompt="", **kw):
            captured["prompt"] = system_prompt
            return None

        with patch(
            "kodo.cli._intake.run_single_turn_plan", autospec=True, side_effect=capture
        ):
            run_test_discovery(run_dir, "/tmp/r.md")
        assert "attack surface" in captured["prompt"].lower()


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

    def test_injected_recon_is_attack_surface(self):
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
        assert "Attack Surface" in recon.name

    def test_adds_missing_report(self):
        plan = GoalPlan(
            context="Test",
            stages=[
                GoalStage(
                    index=1,
                    name="Attack Surface Analysis",
                    description="Map surfaces",
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
                    name="Attack Surface Analysis",
                    description="Map surfaces",
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
                    name="Attack Surface Analysis",
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
                    name="Attack Surface Analysis",
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
    def test_has_four_stages(self):
        plan = _build_test_fallback_plan("/tmp/run/test-report.md")
        assert len(plan.stages) == 4

    def test_first_stage_is_attack_surface(self):
        plan = _build_test_fallback_plan("/tmp/run/test-report.md")
        assert "attack surface" in plan.stages[0].name.lower()

    def test_first_stage_mentions_attack_surfaces(self):
        plan = _build_test_fallback_plan("/tmp/run/test-report.md")
        assert "attack" in plan.stages[0].description.lower()

    def test_first_stage_mentions_building_tools(self):
        plan = _build_test_fallback_plan("/tmp/run/test-report.md")
        desc = plan.stages[0].description.lower()
        assert "tool" in desc

    def test_last_stage_is_triage(self):
        plan = _build_test_fallback_plan("/tmp/run/test-report.md")
        assert _is_report_stage(plan.stages[-1].name)
        assert "triage" in plan.stages[-1].name.lower()

    def test_middle_stages_parallel(self):
        plan = _build_test_fallback_plan("/tmp/run/test-report.md")
        assert plan.stages[1].parallel_group == 1
        assert plan.stages[2].parallel_group == 1

    def test_middle_stages_are_attack_oriented(self):
        """Middle stages should be about fault injection and state corruption."""
        plan = _build_test_fallback_plan("/tmp/run/test-report.md")
        for stage in plan.stages[1:3]:
            desc = stage.description.lower()
            name = stage.name.lower()
            assert (
                "fault" in desc or "corrupt" in desc or "break" in desc
                or "fault" in name or "corrupt" in name or "boundar" in name
            )

    def test_attack_surface_persists(self):
        plan = _build_test_fallback_plan("/tmp/run/test-report.md")
        assert plan.stages[0].persist_changes is True

    def test_attack_stages_read_only(self):
        plan = _build_test_fallback_plan("/tmp/run/test-report.md")
        assert plan.stages[1].persist_changes is False
        assert plan.stages[2].persist_changes is False

    def test_focus_in_context(self):
        plan = _build_test_fallback_plan("/tmp/run/test-report.md", focus="auth")
        assert "auth" in plan.context

    def test_target_in_context(self):
        plan = _build_test_fallback_plan("/tmp/run/test-report.md", targets=["src/"])
        assert "src/" in plan.context

    def test_context_emphasizes_breakage(self):
        plan = _build_test_fallback_plan("/tmp/run/test-report.md")
        ctx = plan.context.lower()
        assert "bug" in ctx or "break" in ctx or "finding" in ctx


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
    def test_full_report_old_format(self):
        """Backward compat: old report format with separate sections."""
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

    def test_full_report_new_format(self):
        """New report format with flat Findings section."""
        report = (
            "## Summary\n"
            "- **Attack surfaces probed:** 5\n"
            "- **Findings:** 3 (1/0/1/0/1/0/0)\n"
            "- **Regression tests written:** 2\n\n"
            "## Findings\n"
            "- **F1:** crash on empty input\n"
            "- **F2:** race on concurrent writes\n"
            "- **F3:** misleading error message\n\n"
            "## Regression Tests & Fixes\n"
            "- tests/a.py:t1 — F1\n- tests/b.py:t2 — F2\n\n"
            "## Unreachable Attack Surfaces\n- Docker testing — needs container\n"
        )
        r = parse_test_report_summary(report)
        assert r["findings_count"] == 3
        assert r["findings_item_count"] == 3
        assert r["regression_count"] == 2
        assert r["untestable_count"] == 1

    def test_empty(self):
        r = parse_test_report_summary("")
        assert r["critical_count"] == 0
        assert r["findings_item_count"] == 0


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
            "## Unreachable Attack Surfaces\n- websocket — needs mock\n",
            encoding="utf-8",
        )
        with patch("kodo.log._runs_root", autospec=True, return_value=runs):
            result = _collect_prior_test_work(run_dir)
        assert "tests/a.py" in result
        assert "websocket" in result

    def test_collects_old_format(self, tmp_path):
        """Backward compat: old section names still collected."""
        from kodo.cli._test import _collect_prior_test_work

        run_dir = RunDir.create(tmp_path, "current")
        runs = tmp_path / "runs"
        prev = runs / "prev"
        prev.mkdir(parents=True)
        (prev / "test-report.md").write_text(
            "## Regression Tests & Fixes\n- tests/a.py:t — F1\n\n"
            "## Untestable Gaps\n- websocket — needs mock\n",
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
    def test_discovery_emphasizes_breakage(self):
        from kodo.prompts.test import DISCOVERY_PROMPT

        p = DISCOVERY_PROMPT.lower()
        assert "break" in p or "bug" in p or "fault" in p

    def test_discovery_emphasizes_attack_surfaces(self):
        from kodo.prompts.test import DISCOVERY_PROMPT

        assert "attack surface" in DISCOVERY_PROMPT.lower()

    def test_orchestrator_rejects_zero_findings(self):
        from kodo.prompts.roles import TEST_ORCHESTRATOR_SYSTEM_PROMPT

        p = TEST_ORCHESTRATOR_SYSTEM_PROMPT.lower()
        assert "zero findings" in p or "push back" in p

    def test_orchestrator_fault_finding(self):
        from kodo.prompts.roles import TEST_ORCHESTRATOR_SYSTEM_PROMPT

        p = TEST_ORCHESTRATOR_SYSTEM_PROMPT.lower()
        assert "fault" in p or "break" in p

    def test_report_has_attack_surface_table(self):
        from kodo.prompts.test import TEST_REPORT_FORMAT

        assert "Attack Surface Coverage" in TEST_REPORT_FORMAT

    def test_report_has_self_critique(self):
        from kodo.prompts.test import TEST_REPORT_FORMAT

        assert "Self-Critique" in TEST_REPORT_FORMAT

    def test_report_has_unreachable_surfaces(self):
        from kodo.prompts.test import TEST_REPORT_FORMAT

        assert "Unreachable Attack Surfaces" in TEST_REPORT_FORMAT

    def test_tool_forge_guidance_lists_attack_tools(self):
        from kodo.prompts.test import TOOL_FORGE_GUIDANCE

        g = TOOL_FORGE_GUIDANCE.lower()
        assert "scenario generator" in g or "state manipulator" in g
        assert "interrupt" in g or "concurrency" in g

    def test_attack_surface_file_constant(self):
        from kodo.prompts.test import ATTACK_SURFACE_FILE

        assert ATTACK_SURFACE_FILE == ".kodo/attack-surfaces.md"

    def test_methodology_library_attack_oriented(self):
        from kodo.prompts.test import METHODOLOGY_LIBRARY

        m = METHODOLOGY_LIBRARY.lower()
        assert "fault injection" in m
        assert "state corruption" in m
        assert "boundary" in m
        assert "assumption" in m

    def test_time_guidance_has_split(self):
        from kodo.prompts.test import TEST_TIME_GUIDANCE

        t = TEST_TIME_GUIDANCE.lower()
        assert "20%" in t
        assert "70%" in t
