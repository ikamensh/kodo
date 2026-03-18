"""Test mode: tool forge, user story tracking, exploratory testing for --test."""

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
    METHODOLOGY_LIBRARY,
    TEST_EXPLORATION_GUIDANCE,
    TEST_FINDING_FORMAT,
    TEST_REPORT_FORMAT,
    TEST_TIME_GUIDANCE,
    TOOL_FORGE_GUIDANCE,
    USER_STORY_FILE,
    USER_STORY_FORMAT,
    USER_STORY_MAPPING_GUIDANCE,
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
            f"The user wants you to focus testing on: **{focus}**\n"
            f"Prioritize exploration and testing for this area. "
            f"Other areas can still be tested but should be secondary."
        )

    target_section = ""
    if targets:
        target_list = ", ".join(f"`{t}`" for t in targets)
        target_section = (
            f"\n\n## Target Scope\n"
            f"Focus testing on these files/directories: {target_list}\n"
            f"Test the workflows and integration points involving these paths."
        )

    story_file = USER_STORY_FILE
    story_mapping = USER_STORY_MAPPING_GUIDANCE.format(
        story_file=story_file,
        story_format=USER_STORY_FORMAT,
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
            story_mapping_guidance=story_mapping,
            story_file=story_file,
        )
        + focus_section
        + target_section
        + prior_test_work
    )

    initial_message = "Analyze this project and create a thorough testing plan to find real bugs and gaps."
    if focus:
        initial_message += f" Focus on: {focus}"
    if targets:
        initial_message += f" Target: {', '.join(targets)}"

    plan = run_single_turn_plan(
        run_dir,
        system_prompt=prompt,
        initial_message=initial_message,
        spinner_text="Planning test improvements",
    )

    if isinstance(plan, GoalPlan) and plan.stages:
        return _validate_test_plan(plan, report_path, run_dir_str)
    return None


# ---------------------------------------------------------------------------
# Plan validation
# ---------------------------------------------------------------------------


def _is_recon_stage(name: str) -> bool:
    n = name.lower()
    return "recon" in n or "audit" in n or "baseline" in n or "tool forge" in n


def _is_report_stage(name: str) -> bool:
    n = name.lower()
    return "report" in n or "regress" in n


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
                name="Reconnaissance",
                description=(
                    "Understand what the software does and how users interact with it. "
                    "Read the README, run --help, check examples. Run the existing test suite. "
                    "Map key user workflows and identify what current tests DON'T cover.\n\n"
                    f"Write recon to `{recon_path}`.\n\n"
                    f"{TEST_FINDING_FORMAT}"
                ),
                acceptance_criteria=(
                    f"Recon documented at {recon_path} with workflows, gaps, and integration points."
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
                name="Regression Tests & Report",
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

    Tool Forge → two parallel exploration stages → regression tests & report.
    """
    run_dir = str(Path(report_path).parent)
    focus_ctx = f"\n\n**Focus area:** {focus}" if focus else ""
    target_ctx = ""
    if targets:
        target_list = ", ".join(f"`{t}`" for t in targets)
        target_ctx = f"\n\n**Target scope:** {target_list} — focus testing on these files/directories."
    recon_path = f"{run_dir}/test-recon.md"
    story_file = USER_STORY_FILE
    findings_integration = f"{run_dir}/findings-integration.md"
    findings_exploratory = f"{run_dir}/findings-exploratory.md"

    story_mapping = USER_STORY_MAPPING_GUIDANCE.format(
        story_file=story_file,
        story_format=USER_STORY_FORMAT,
    )

    return GoalPlan(
        context=(
            "Test this software like a real user. Build tools to interact with "
            "it properly, map user stories, work through them. Deliverable is "
            "findings with repro steps.\n\n"
            f"{TEST_TIME_GUIDANCE}"
            f"{focus_ctx}"
            f"{target_ctx}"
            f"{prior_test_work}"
        ),
        stages=[
            GoalStage(
                index=1,
                name="Tool Forge & Story Mapping",
                persist_changes=True,
                description=(
                    f"{TOOL_FORGE_GUIDANCE}\n\n"
                    f"{story_mapping}\n\n"
                    "Write recon notes (what the software does, what existing "
                    f"tests cover) to `{recon_path}`."
                ),
                acceptance_criteria=(
                    "Testing tools built and working. "
                    f"User stories in {story_file}. "
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
                name="Integration & Workflow Testing",
                persist_changes=False,
                parallel_group=1,
                description=(
                    "Use your tools to test key user workflows end-to-end. "
                    f"Work through testable stories from `{story_file}`. "
                    "Document findings with repro steps. "
                    f"Update story status in `{story_file}`.\n\n"
                    f"Write findings to `{findings_integration}`.\n\n"
                    f"{TEST_FINDING_FORMAT}"
                ),
                acceptance_criteria=(
                    f"Findings with repro steps at {findings_integration}. "
                    f"Story statuses updated in {story_file}."
                ),
                verification=[
                    QuickCheck(
                        path=findings_integration,
                        description="Integration findings file exists",
                        error_message=f"Expected findings at {findings_integration}",
                    ),
                ],
            ),
            GoalStage(
                index=3,
                name="Exploratory & Adversarial Testing",
                persist_changes=False,
                parallel_group=1,
                description=(
                    "Use your tools to try to break the software. Invalid inputs, "
                    "missing files, interrupted workflows, concurrent usage, "
                    "corrupt configs. Document findings with repro steps.\n\n"
                    f"Write findings to `{findings_exploratory}`.\n\n"
                    f"{TEST_FINDING_FORMAT}"
                ),
                acceptance_criteria=(
                    f"Findings with repro steps at {findings_exploratory}."
                ),
                verification=[
                    QuickCheck(
                        path=findings_exploratory,
                        description="Exploratory findings file exists",
                        error_message=f"Expected findings at {findings_exploratory}",
                    ),
                ],
            ),
            GoalStage(
                index=4,
                name="Regression Tests, Fixes & Report",
                persist_changes=True,
                description=(
                    "For each confirmed bug from "
                    f"`{findings_integration}` and `{findings_exploratory}`:\n"
                    "1. Write a test that reproduces the bug — verify it fails\n"
                    "2. Fix the code\n"
                    "3. Verify the test now passes\n\n"
                    "Commit test and fix separately:\n"
                    '- "test: add regression test for F<n> (kodo test)"\n'
                    '- "fix: <description> (kodo test)"\n\n'
                    f"Update story statuses in `{story_file}`. "
                    "Run the full test suite.\n\n"
                    f"Write report to `{report_path}`:\n\n"
                    f"{TEST_REPORT_FORMAT}"
                ),
                acceptance_criteria=(
                    f"Report at {report_path} with findings, user story status, "
                    "and blocked stories. Regression tests committed. Suite passes."
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
    critical = extract_test_section(report_content, "Critical Findings")
    integration = extract_test_section(
        report_content, "Integration & Workflow Findings"
    )
    usability = extract_test_section(report_content, "Usability Gaps")
    regression = extract_test_section(report_content, "Regression Tests & Fixes")
    if not regression.strip():
        regression = extract_test_section(report_content, "Regression Tests Added")
    untestable = extract_test_section(report_content, "Untestable Gaps")

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
        re.findall(r"^- .+$", untestable, re.MULTILINE),
    )

    blocked = extract_test_section(report_content, "Blocked Stories")
    blocked_lines = [
        ln.strip() for ln in blocked.strip().splitlines() if ln.strip().startswith("- ")
    ]
    result["blocked_count"] = len(blocked_lines)
    result["blocked_details"] = blocked_lines

    return result
