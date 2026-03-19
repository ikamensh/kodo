"""Test mode: user-experience-first testing with edge case and adversarial probing."""

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
    DISCOVERY_PROMPT,
    FEATURE_COVERAGE_FILE,
    FEATURE_COVERAGE_FORMAT,
    FEATURE_MAPPING_GUIDANCE,
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
    """Run AI discovery to build a dynamic test plan.

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
            f"The user wants you to focus on: **{focus}**\n"
            f"Prioritize features and workflows for this area. "
            f"Other areas can still be tested but should be secondary."
        )

    target_section = ""
    if targets:
        target_list = ", ".join(f"`{t}`" for t in targets)
        target_section = (
            f"\n\n## Target Scope\n"
            f"Focus testing on these files/directories: {target_list}\n"
            f"Test the features and workflows involving these paths."
        )

    coverage_file = FEATURE_COVERAGE_FILE
    feature_mapping = FEATURE_MAPPING_GUIDANCE.format(
        coverage_file=coverage_file,
        coverage_format=FEATURE_COVERAGE_FORMAT,
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
            feature_mapping_guidance=feature_mapping,
            coverage_file=coverage_file,
        )
        + focus_section
        + target_section
        + prior_test_work
    )

    initial_message = "Analyze this project and test it like a real user. Exercise every feature, then probe edge cases."
    if focus:
        initial_message += f" Focus on: {focus}"
    if targets:
        initial_message += f" Target: {', '.join(targets)}"

    plan = run_single_turn_plan(
        run_dir,
        system_prompt=prompt,
        initial_message=initial_message,
        spinner_text="Planning test approach",
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
        or "setup" in n
        or "discover" in n
        or "install" in n
        or "tool forge" in n
        or "feature map" in n
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
                name="Setup & Discovery",
                description=(
                    "Install the software. Read the README, run --help, check examples. "
                    "Map all user-facing features and workflows. Run the existing test suite. "
                    "Build whatever tools you need to interact with the software.\n\n"
                    f"Write discovery notes to `{recon_path}`.\n\n"
                    "Spend no more than 15% of total effort here.\n\n"
                    f"{TEST_FINDING_FORMAT}"
                ),
                acceptance_criteria=(
                    f"Discovery notes at {recon_path} with feature map and test plan."
                ),
                persist_changes=False,
                verification=[
                    QuickCheck(
                        path=recon_path,
                        description="Discovery notes file exists",
                        error_message=f"Expected discovery notes at {recon_path}",
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

    Setup & Discovery → two parallel feature walkthrough stages → triage & regression.
    """
    run_dir = str(Path(report_path).parent)
    focus_ctx = f"\n\n**Focus area:** {focus}" if focus else ""
    target_ctx = ""
    if targets:
        target_list = ", ".join(f"`{t}`" for t in targets)
        target_ctx = f"\n\n**Target scope:** {target_list} — focus testing on these files/directories."
    recon_path = f"{run_dir}/test-recon.md"
    coverage_file = FEATURE_COVERAGE_FILE
    findings_workflows = f"{run_dir}/findings-workflows.md"
    findings_edge_cases = f"{run_dir}/findings-edge-cases.md"

    feature_mapping = FEATURE_MAPPING_GUIDANCE.format(
        coverage_file=coverage_file,
        coverage_format=FEATURE_COVERAGE_FORMAT,
    )

    return GoalPlan(
        context=(
            "Test this software like a real user. Install it, exercise every "
            "feature, try realistic workflows, then probe edge cases. "
            "Find bugs users would actually hit.\n\n"
            f"{TEST_TIME_GUIDANCE}"
            f"{focus_ctx}"
            f"{target_ctx}"
            f"{prior_test_work}"
        ),
        stages=[
            GoalStage(
                index=1,
                name="Setup & Discovery",
                persist_changes=True,
                description=(
                    f"{TOOL_FORGE_GUIDANCE}\n\n"
                    f"{feature_mapping}\n\n"
                    "Install the software following the documented steps. "
                    "Write discovery notes (features found, what existing "
                    f"tests cover, what's most likely to break) to `{recon_path}`.\n\n"
                    "Spend no more than 15% of total effort here. Get the "
                    "software running, then start testing."
                ),
                acceptance_criteria=(
                    "Software installed and running. "
                    f"Feature map in {coverage_file}. "
                    f"Discovery notes at {recon_path}."
                ),
                verification=[
                    QuickCheck(
                        path=recon_path,
                        description="Discovery notes file exists",
                        error_message=f"Expected discovery notes at {recon_path}",
                    ),
                ],
            ),
            GoalStage(
                index=2,
                name="Feature Walkthroughs",
                persist_changes=False,
                parallel_group=1,
                description=(
                    "Exercise every user-facing feature end-to-end. "
                    "Follow documented workflows, try the examples from "
                    "the README, use every CLI command and flag. "
                    "Test both happy paths and common error cases.\n\n"
                    f"Write findings to `{findings_workflows}`.\n\n"
                    f"{TEST_FINDING_FORMAT}"
                ),
                acceptance_criteria=(
                    f"Findings with repro steps at {findings_workflows}, "
                    "or detailed explanation of features tested and why they passed."
                ),
                verification=[
                    QuickCheck(
                        path=findings_workflows,
                        description="Workflow findings file exists",
                        error_message=f"Expected findings at {findings_workflows}",
                    ),
                ],
            ),
            GoalStage(
                index=3,
                name="Edge Cases & Error Paths",
                persist_changes=False,
                parallel_group=1,
                description=(
                    "Probe edge cases and error handling across all features. "
                    "Try empty inputs, huge inputs, invalid types, missing files, "
                    "bad configs, unicode edge cases, concurrent usage. "
                    "Test every error path — are messages helpful? Does it recover "
                    "or corrupt state?\n\n"
                    f"Write findings to `{findings_edge_cases}`.\n\n"
                    f"{TEST_FINDING_FORMAT}"
                ),
                acceptance_criteria=(
                    f"Findings with repro steps at {findings_edge_cases}, "
                    "or detailed explanation of edge cases tested and why they passed."
                ),
                verification=[
                    QuickCheck(
                        path=findings_edge_cases,
                        description="Edge case findings file exists",
                        error_message=f"Expected findings at {findings_edge_cases}",
                    ),
                ],
            ),
            GoalStage(
                index=4,
                name="Triage & Regression Tests",
                persist_changes=True,
                description=(
                    "For each confirmed bug from "
                    f"`{findings_workflows}` and `{findings_edge_cases}`:\n"
                    "1. Write a test that reproduces the bug — verify it fails\n"
                    "2. Fix the code\n"
                    "3. Verify the test now passes\n\n"
                    "Commit test and fix separately:\n"
                    '- "test: add regression test for F<n> (kodo test)"\n'
                    '- "fix: <description> (kodo test)"\n\n'
                    f"Update feature coverage in `{coverage_file}`. "
                    "Run the full test suite.\n\n"
                    f"Write report to `{report_path}`:\n\n"
                    f"{TEST_REPORT_FORMAT}"
                ),
                acceptance_criteria=(
                    f"Report at {report_path} with findings, feature coverage, "
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
            "Regression Tests & Fixes": (
                "\n## Previously Generated Tests\n"
                "Previous runs already added these. Focus on new gaps:\n\n{items}\n"
            ),
            "Blocked Workflows": (
                "\n## Prior Remaining Gaps\n"
                "Previous runs couldn't cover these. Try to address them "
                "or carry forward:\n\n{items}\n"
            ),
        },
    )


def parse_test_report_summary(report_content: str) -> dict:
    """Parse a test report into structured summary data.

    Returns a dict with keys: findings_count, findings_item_count,
    regression_tests, regression_count, blocked_count, blocked_details.
    """
    summary = extract_test_section(report_content, "Summary")
    findings = extract_test_section(report_content, "Findings")
    regression = extract_test_section(report_content, "Regression Tests & Fixes")
    blocked = extract_test_section(report_content, "Blocked Workflows")

    result: dict = {}

    # Parse summary metrics
    for line in summary.splitlines():
        line = line.strip().lstrip("- *")
        if "findings" in line.lower():
            m = re.search(r"(\d+)", line)
            if m:
                result["findings_count"] = int(m.group(1))
        elif "regression" in line.lower():
            m = re.search(r"(\d+)", line)
            if m:
                result["regression_tests"] = int(m.group(1))

    result["findings_item_count"] = len(re.findall(r"\*\*F\d+", findings))
    result["regression_count"] = len(
        re.findall(r"^- .+$", regression, re.MULTILINE),
    )

    blocked_lines = [
        ln.strip() for ln in blocked.strip().splitlines() if ln.strip().startswith("- ")
    ]
    result["blocked_count"] = len(blocked_lines)
    result["blocked_details"] = blocked_lines

    return result
