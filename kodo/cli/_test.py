"""Test mode: attack surface analysis, fault injection, breakage-oriented testing."""

import re
from pathlib import Path
from typing import TYPE_CHECKING

from kodo.cli._shared import (
    build_environment_section,
    collect_prior_report_items,
    extract_section as extract_test_section,
)
from kodo.orchestrators.base import GoalPlan, GoalStage, QuickCheck
from kodo.prompts.test import (
    ATTACK_SURFACE_FILE,
    ATTACK_SURFACE_FORMAT,
    ATTACK_SURFACE_MAPPING_GUIDANCE,
    DISCOVERY_PROMPT,
    METHODOLOGY_LIBRARY,
    TEST_EXPLORATION_GUIDANCE,
    TEST_FINDING_FORMAT,
    TEST_REPORT_FORMAT,
    TEST_TIME_GUIDANCE,
    TOOL_FORGE_GUIDANCE,
)

if TYPE_CHECKING:
    from kodo.log import RunDir


# ---------------------------------------------------------------------------
# Discovery runner
# ---------------------------------------------------------------------------


def run_test_discovery(
    run_dir: "RunDir",
    report_path: str,
    *,
    focus: str | None = None,
    targets: list[str] | None = None,
    prior_test_work: str = "",
) -> GoalPlan | None:
    """Run AI discovery to build a dynamic test improvement plan.

    Uses the shared single-turn plan mechanism from intake to inspect the
    project and produce a ``GoalPlan`` tailored to testing.  Falls back
    to ``None`` when no backend is available or parsing fails.
    """
    from kodo.cli._intake import run_single_turn_plan

    output_file = run_dir.goal_plan_file
    run_dir_str = str(run_dir.root)

    environment = build_environment_section()

    focus_section = ""
    if focus:
        focus_section = (
            f"\n\n## Focus Area\n"
            f"The user wants you to focus attacks on: **{focus}**\n"
            f"Prioritize attack surfaces for this area. "
            f"Other areas can still be tested but should be secondary."
        )

    target_section = ""
    if targets:
        target_list = ", ".join(f"`{t}`" for t in targets)
        target_section = (
            f"\n\n## Target Scope\n"
            f"Focus attacks on these files/directories: {target_list}\n"
            f"Attack the workflows and integration points involving these paths."
        )

    surface_file = ATTACK_SURFACE_FILE
    surface_mapping = ATTACK_SURFACE_MAPPING_GUIDANCE.format(
        surface_file=surface_file,
        surface_format=ATTACK_SURFACE_FORMAT,
    )

    prompt = (
        DISCOVERY_PROMPT.format(
            output_path=str(output_file),
            methodologies=METHODOLOGY_LIBRARY,
            environment=environment,
            run_dir=run_dir_str,
            report_path=report_path,
            finding_format=TEST_FINDING_FORMAT,
            exploration_guidance=TEST_EXPLORATION_GUIDANCE,
            report_format=TEST_REPORT_FORMAT,
            time_guidance=TEST_TIME_GUIDANCE,
            tool_forge_guidance=TOOL_FORGE_GUIDANCE,
            surface_mapping_guidance=surface_mapping,
            surface_file=surface_file,
        )
        + focus_section
        + target_section
        + prior_test_work
    )

    initial_message = "Analyze this project and find bugs. Assume happy paths work — focus on what breaks."
    if focus:
        initial_message += f" Focus attacks on: {focus}"
    if targets:
        initial_message += f" Target: {', '.join(targets)}"

    plan = run_single_turn_plan(
        run_dir,
        system_prompt=prompt,
        initial_message=initial_message,
        spinner_text="Planning test attacks",
    )

    if isinstance(plan, GoalPlan) and plan.stages:
        return _validate_test_plan(plan, report_path, run_dir_str)
    return None


# ---------------------------------------------------------------------------
# Plan validation
# ---------------------------------------------------------------------------


def _is_recon_stage(name: str) -> bool:
    n = name.lower()
    return (
        "recon" in n
        or "audit" in n
        or "baseline" in n
        or "tool forge" in n
        or "attack surface" in n
    )


def _is_report_stage(name: str) -> bool:
    n = name.lower()
    return "report" in n or "regress" in n or "triage" in n


def _validate_test_plan(
    plan: GoalPlan,
    report_path: str,
    run_dir: str,
) -> GoalPlan:
    """Post-process an AI-generated test plan to ensure correctness.

    1. Ensure recon and report stages exist.
    2. Re-index stages.
    """
    recon_path = f"{run_dir}/test-recon.md"
    stages = list(plan.stages)

    has_recon = any(_is_recon_stage(s.name) for s in stages)
    has_report = any(_is_report_stage(s.name) for s in stages)

    if not has_recon:
        stages.insert(
            0,
            GoalStage(
                index=0,
                name="Attack Surface Analysis",
                description=(
                    "Identify attack surfaces — where can this software break? "
                    "Read the README, run --help, check examples. Run the existing test suite. "
                    "Map what inputs, state, and dependencies can be attacked.\n\n"
                    f"Write recon to `{recon_path}`.\n\n"
                    "Spend no more than 20% of total effort here.\n\n"
                    f"{TEST_FINDING_FORMAT}"
                ),
                acceptance_criteria=(
                    f"Recon documented at {recon_path} with attack surfaces and attack plan."
                ),
                persist_changes=False,
                verification=[
                    QuickCheck(
                        path=recon_path,
                        description="Recon file exists",
                        error_message=f"Expected recon at {recon_path}",
                    ),
                ],
            ),
        )

    if not has_report:
        stages.append(
            GoalStage(
                index=len(stages) + 1,
                name="Triage & Regression Tests",
                description=(
                    "For each confirmed bug, write a minimal regression test. "
                    "Run the full test suite to verify no regressions.\n\n"
                    f"Write report to `{report_path}`:\n\n"
                    f"{TEST_REPORT_FORMAT}\n\n"
                    'Commit regression tests: "test: add regression tests for findings (kodo test)".'
                ),
                acceptance_criteria=(
                    f"Report at {report_path}. Regression tests committed for confirmed bugs."
                ),
                persist_changes=True,
            ),
        )

    # Re-index
    final: list[GoalStage] = []
    for i, stage in enumerate(stages, 1):
        final.append(
            GoalStage(
                index=i,
                name=stage.name,
                description=stage.description,
                acceptance_criteria=stage.acceptance_criteria,
                browser_testing=stage.browser_testing,
                parallel_group=stage.parallel_group,
                persist_changes=stage.persist_changes,
                verification=stage.verification,
            ),
        )

    return GoalPlan(context=plan.context, stages=final)


# ---------------------------------------------------------------------------
# Fallback plan — used when discovery is unavailable
# ---------------------------------------------------------------------------


def _build_test_fallback_plan(
    report_path: str,
    *,
    focus: str | None = None,
    targets: list[str] | None = None,
    prior_test_work: str = "",
) -> GoalPlan:
    """Build a hardcoded test plan (fallback when discovery fails).

    Attack Surface Analysis → two parallel attack stages → triage & regression.
    """
    run_dir = str(Path(report_path).parent)
    focus_ctx = f"\n\n**Focus area:** {focus}" if focus else ""
    target_ctx = ""
    if targets:
        target_list = ", ".join(f"`{t}`" for t in targets)
        target_ctx = f"\n\n**Target scope:** {target_list} — focus attacks on these files/directories."
    recon_path = f"{run_dir}/test-recon.md"
    surface_file = ATTACK_SURFACE_FILE
    findings_injection = f"{run_dir}/findings-fault-injection.md"
    findings_corruption = f"{run_dir}/findings-state-corruption.md"

    surface_mapping = ATTACK_SURFACE_MAPPING_GUIDANCE.format(
        surface_file=surface_file,
        surface_format=ATTACK_SURFACE_FORMAT,
    )

    return GoalPlan(
        context=(
            "Find bugs. Assume happy paths work. Zero findings means the "
            "testing approach failed, not that the software is perfect.\n\n"
            f"{TEST_TIME_GUIDANCE}"
            f"{focus_ctx}"
            f"{target_ctx}"
            f"{prior_test_work}"
        ),
        stages=[
            GoalStage(
                index=1,
                name="Attack Surface Analysis",
                persist_changes=True,
                description=(
                    f"{TOOL_FORGE_GUIDANCE}\n\n"
                    f"{surface_mapping}\n\n"
                    "Write recon notes (attack surfaces, what existing "
                    f"tests cover, what's most likely to break) to `{recon_path}`.\n\n"
                    "Spend no more than 20% of total effort here. Get tools "
                    "working fast, then move on to attacks."
                ),
                acceptance_criteria=(
                    "Attack tools built and working. "
                    f"Attack surfaces in {surface_file}. "
                    f"Recon at {recon_path}."
                ),
                verification=[
                    QuickCheck(
                        path=recon_path,
                        description="Recon file exists",
                        error_message=f"Expected recon at {recon_path}",
                    ),
                ],
            ),
            GoalStage(
                index=2,
                name="Fault Injection & Error Paths",
                persist_changes=False,
                parallel_group=1,
                description=(
                    "Use your tools to inject faults and force error paths. "
                    "Kill processes mid-operation, remove files during reads, "
                    "send signals at critical moments, force every error handler. "
                    f"Document findings with repro steps.\n\n"
                    f"Write findings to `{findings_injection}`.\n\n"
                    f"{TEST_FINDING_FORMAT}"
                ),
                acceptance_criteria=(
                    f"Findings with repro steps at {findings_injection}, "
                    "or detailed explanation of attacks tried and why they found nothing."
                ),
                verification=[
                    QuickCheck(
                        path=findings_injection,
                        description="Fault injection findings file exists",
                        error_message=f"Expected findings at {findings_injection}",
                    ),
                ],
            ),
            GoalStage(
                index=3,
                name="State Corruption & Boundaries",
                persist_changes=False,
                parallel_group=1,
                description=(
                    "Use your tools to corrupt state and probe boundaries. "
                    "Start with invalid state — half-written files, stale locks, "
                    "empty inputs, massive inputs, unicode edge cases, corrupt configs. "
                    "Run multiple instances simultaneously. "
                    f"Document findings with repro steps.\n\n"
                    f"Write findings to `{findings_corruption}`.\n\n"
                    f"{TEST_FINDING_FORMAT}"
                ),
                acceptance_criteria=(
                    f"Findings with repro steps at {findings_corruption}, "
                    "or detailed explanation of attacks tried and why they found nothing."
                ),
                verification=[
                    QuickCheck(
                        path=findings_corruption,
                        description="State corruption findings file exists",
                        error_message=f"Expected findings at {findings_corruption}",
                    ),
                ],
            ),
            GoalStage(
                index=4,
                name="Triage & Regression Tests",
                persist_changes=True,
                description=(
                    "For each confirmed bug from "
                    f"`{findings_injection}` and `{findings_corruption}`:\n"
                    "1. Write a test that reproduces the bug — verify it fails\n"
                    "2. Fix the code\n"
                    "3. Verify the test now passes\n\n"
                    "Commit test and fix separately:\n"
                    '- "test: add regression test for F<n> (kodo test)"\n'
                    '- "fix: <description> (kodo test)"\n\n'
                    f"Update attack surface status in `{surface_file}`. "
                    "Run the full test suite.\n\n"
                    f"Write report to `{report_path}`:\n\n"
                    f"{TEST_REPORT_FORMAT}"
                ),
                acceptance_criteria=(
                    f"Report at {report_path} with findings, attack surface coverage, "
                    "and self-critique. Regression tests committed. Suite passes."
                ),
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Prior-run awareness — avoid re-generating the same tests
# ---------------------------------------------------------------------------


def _collect_prior_test_work(current_run_dir: "RunDir") -> str:
    """Collect findings from previous ``kodo test`` runs."""
    return collect_prior_report_items(
        current_run_id=current_run_dir.run_id,
        report_glob="*/test-report.md",
        sections={
            # New section names first
            "Regression Tests & Fixes": (
                "\n## Previously Generated Tests\n"
                "Previous runs already added these. Focus on new gaps:\n\n{items}\n"
            ),
            "Unreachable Attack Surfaces": (
                "\n## Prior Remaining Gaps\n"
                "Previous runs couldn't cover these. Try to address them "
                "or carry forward:\n\n{items}\n"
            ),
            # Old section names for backward compat
            "Untestable Gaps": (
                "\n## Prior Remaining Gaps\n"
                "Previous runs couldn't cover these. Try to address them "
                "or carry forward:\n\n{items}\n"
            ),
        },
    )


def parse_test_report_summary(report_content: str) -> dict:
    """Parse a test report into structured summary data.

    Returns a dict with keys: findings_count, bugs_confirmed,
    usability_gaps, regression_tests, critical_count.
    """
    summary = extract_test_section(report_content, "Summary")

    # New format: flat Findings section
    findings = extract_test_section(report_content, "Findings")
    # Old format: separate sections
    critical = extract_test_section(report_content, "Critical Findings")
    integration = extract_test_section(
        report_content, "Integration & Workflow Findings"
    )
    usability = extract_test_section(report_content, "Usability Gaps")

    regression = extract_test_section(report_content, "Regression Tests & Fixes")
    if not regression.strip():
        regression = extract_test_section(report_content, "Regression Tests Added")

    # New format: Unreachable Attack Surfaces; old: Untestable Gaps
    unreachable = extract_test_section(report_content, "Unreachable Attack Surfaces")
    if not unreachable.strip():
        unreachable = extract_test_section(report_content, "Untestable Gaps")

    result: dict = {}

    # Parse summary metrics
    for line in summary.splitlines():
        line = line.strip().lstrip("- *")
        if "findings" in line.lower():
            m = re.search(r"(\d+)", line)
            if m:
                result["findings_count"] = int(m.group(1))
        elif "bugs confirmed" in line.lower():
            m = re.search(r"(\d+)", line)
            if m:
                result["bugs_confirmed"] = int(m.group(1))
        elif "usability" in line.lower():
            m = re.search(r"(\d+)", line)
            if m:
                result["usability_gaps"] = int(m.group(1))
        elif "regression" in line.lower():
            m = re.search(r"(\d+)", line)
            if m:
                result["regression_tests"] = int(m.group(1))

    # Count items from sections (match "- **F1:" or "**F1:")
    # New format: flat findings section
    result["findings_item_count"] = len(re.findall(r"\*\*F\d+", findings))
    # Old format: separate sections
    result["critical_count"] = len(
        re.findall(r"\*\*F\d+", critical),
    )
    result["integration_count"] = len(
        re.findall(r"\*\*F\d+", integration),
    )
    result["usability_count"] = len(
        re.findall(r"\*\*F\d+", usability),
    )
    result["regression_count"] = len(
        re.findall(r"^- .+$", regression, re.MULTILINE),
    )
    result["untestable_count"] = len(
        re.findall(r"^- .+$", unreachable, re.MULTILINE),
    )

    blocked = extract_test_section(report_content, "Blocked Stories")
    blocked_lines = [
        ln.strip() for ln in blocked.strip().splitlines() if ln.strip().startswith("- ")
    ]
    result["blocked_count"] = len(blocked_lines)
    result["blocked_details"] = blocked_lines

    return result
