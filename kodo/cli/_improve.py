"""Improve mode: AI-driven discovery + staged plans for --improve."""

from pathlib import Path
from typing import TYPE_CHECKING

from kodo.cli._shared import (
    build_environment_section,
    collect_prior_report_items,
    extract_section,
    slugify,
)
from kodo.orchestrators.base import GoalPlan, GoalStage, QuickCheck
from kodo.prompts.improve import (
    DISCOVERY_PROMPT,
    IMPROVE_REPORT_FORMAT,
    IMPROVE_TIME_GUIDANCE,
    METHODOLOGY_LIBRARY,
    TRIAGE_FINDINGS_FORMAT,
    TRIAGE_STAGE_DESCRIPTION,
)

if TYPE_CHECKING:
    from kodo.log import RunDir


# ---------------------------------------------------------------------------
# Discovery runner
# ---------------------------------------------------------------------------


def run_improve_discovery(
    run_dir,
    report_path: str,
    prior_needs_decision: str = "",
    *,
    focus: str | None = None,
) -> GoalPlan | None:
    """Run AI discovery to build a dynamic improve plan for --improve.

    Uses the shared single-turn plan mechanism from intake to inspect the
    project and produce a ``GoalPlan`` tailored to the stack.  Falls back
    to ``None`` when no backend is available or parsing fails.
    """
    from kodo.cli._intake import run_single_turn_plan

    output_file = run_dir.goal_plan_file
    run_dir_str = str(run_dir.root)
    triage_path = f"{run_dir_str}/triage-results.md"

    environment = build_environment_section()

    focus_section = ""
    if focus:
        focus_section = (
            f"\n\n## Focus Area\n"
            f"The user wants you to concentrate on: **{focus}**\n"
            f"Prioritize stages and findings related to this area. "
            f"Other issues can still be reported but should be secondary."
        )

    prompt = (
        DISCOVERY_PROMPT.format(
            output_path=str(output_file),
            methodologies=METHODOLOGY_LIBRARY,
            environment=environment,
            run_dir=run_dir_str,
            report_path=report_path,
            triage_description=TRIAGE_STAGE_DESCRIPTION.format(triage_path=triage_path),
            report_format=IMPROVE_REPORT_FORMAT,
            findings_format=TRIAGE_FINDINGS_FORMAT,
            time_guidance=IMPROVE_TIME_GUIDANCE,
        )
        + focus_section
    )

    initial_message = "Analyze this project and create an improvement plan."
    if focus:
        initial_message += f" Focus on: {focus}"

    plan = run_single_turn_plan(
        run_dir,
        system_prompt=prompt,
        initial_message=initial_message,
        spinner_text="Planning improvements",
    )

    if isinstance(plan, GoalPlan) and plan.stages:
        return _validate_improve_plan(
            plan,
            report_path,
            run_dir_str,
            prior_needs_decision,
        )
    return None


# ---------------------------------------------------------------------------
# Plan validation — safety net for AI-generated plans
# ---------------------------------------------------------------------------


_slugify = slugify  # backward compat alias


def _is_triage_stage(name: str) -> bool:
    n = name.lower()
    return "triage" in n or "verify" in n


def _is_fix_stage(name: str) -> bool:
    n = name.lower()
    return "fix" in n or "report" in n


def _validate_improve_plan(
    plan: GoalPlan,
    report_path: str,
    run_dir: str,
    prior_needs_decision: str = "",
) -> GoalPlan:
    """Post-process an AI-generated plan to ensure correctness.

    1. Append triage and fix/report stages if the AI omitted them.
    2. Assign a findings file path to each analysis/testing stage.
    3. Inject "Do NOT modify source code" into parallel stages.
    4. Wire all findings paths into triage and fix stage descriptions.
    """
    triage_path = f"{run_dir}/triage-results.md"
    stages = list(plan.stages)

    # --- 1. Ensure triage and fix stages exist ---

    has_triage = any(_is_triage_stage(s.name) for s in stages)
    has_fix = any(_is_fix_stage(s.name) for s in stages)

    if not has_triage:
        stages.append(
            GoalStage(
                index=len(stages) + 1,
                name="Triage & Verify",
                description=TRIAGE_STAGE_DESCRIPTION.format(triage_path=triage_path),
                acceptance_criteria=f"Every finding has a verdict in {triage_path}.",
            ),
        )

    if not has_fix:
        stages.append(
            GoalStage(
                index=len(stages) + 1,
                name="Fix & Report",
                description=(
                    f"Act only on `fix` and `needs-decision` from `{triage_path}`. "
                    "Ignore `skip`.\n\n"
                    "Auto-fix safe issues, flag ambiguous ones. "
                    f"Write report to `{report_path}`:\n\n"
                    f"{IMPROVE_REPORT_FORMAT}\n\n"
                    "Commit auto-fixes: "
                    '"chore: auto-fix issues found by kodo improve".'
                ),
                acceptance_criteria=(
                    f"Report at {report_path}. Auto-fixes committed. "
                    "Only triage-approved findings acted on."
                ),
            ),
        )

    # --- 2. Assign findings paths and inject instructions ---

    findings_paths: list[str] = []
    augmented: list[GoalStage] = []

    for stage in stages:
        if _is_triage_stage(stage.name) or _is_fix_stage(stage.name):
            augmented.append(stage)
            continue

        # Assign a findings file
        findings_file = f"{run_dir}/findings-{_slugify(stage.name)}.md"
        findings_paths.append(findings_file)

        extra = f"\n\nWrite findings to `{findings_file}`.\n\n{TRIAGE_FINDINGS_FORMAT}"

        if stage.parallel_group is not None:
            extra = f"\n\nDo NOT modify source code.{extra}"

        augmented.append(
            GoalStage(
                index=stage.index,
                name=stage.name,
                description=stage.description + extra,
                acceptance_criteria=stage.acceptance_criteria,
                browser_testing=stage.browser_testing,
                parallel_group=stage.parallel_group,
                persist_changes=stage.persist_changes,
                verification=stage.verification,
            ),
        )

    # --- 3. Wire findings paths and prior items into triage and fix stages ---

    findings_list = (
        ", ".join(f"`{p}`" for p in findings_paths) if findings_paths else ""
    )
    findings_ref = f"\n\nFindings files: {findings_list}." if findings_list else ""

    final: list[GoalStage] = []
    for stage in augmented:
        extra = ""
        if _is_triage_stage(stage.name):
            extra = findings_ref + prior_needs_decision
        elif _is_fix_stage(stage.name):
            extra = findings_ref
        if extra:
            stage = GoalStage(
                index=stage.index,
                name=stage.name,
                description=stage.description + extra,
                acceptance_criteria=stage.acceptance_criteria,
                browser_testing=stage.browser_testing,
                parallel_group=stage.parallel_group,
                persist_changes=stage.persist_changes,
                verification=stage.verification,
            )
        final.append(stage)

    return GoalPlan(context=plan.context, stages=final)


# ---------------------------------------------------------------------------
# Fallback plan — used when discovery is unavailable
# ---------------------------------------------------------------------------


def _build_fallback_plan(
    report_path: str,
    prior_needs_decision: str = "",
    *,
    focus: str | None = None,
) -> GoalPlan:
    """Build a hardcoded improve plan (fallback when discovery fails).

    Simplification + Usability + Architecture (parallel) → Triage → Fix & Report.
    """
    run_dir = str(Path(report_path).parent)
    focus_ctx = f"\n\n**Focus area:** {focus}" if focus else ""
    simplification_findings = f"{run_dir}/findings-simplification.md"
    usability_findings = f"{run_dir}/findings-usability.md"
    architecture_findings = f"{run_dir}/findings-architecture.md"
    triage_path = f"{run_dir}/triage-results.md"

    return GoalPlan(
        context=(
            "Review this codebase for significant improvements. Think like "
            "a senior developer joining the project — what would you change "
            "in your first week?\n\n"
            f"{IMPROVE_TIME_GUIDANCE}"
            f"{focus_ctx}"
        ),
        stages=[
            GoalStage(
                index=1,
                name="Simplification & Dead Weight",
                parallel_group=1,
                description=(
                    "Read the codebase looking for unnecessary complexity. "
                    "Abstractions that don't pay for themselves, duplicated logic, "
                    "dead code, unused dependencies, things that reimplement "
                    "standard library functionality. For each finding, explain "
                    "what's simpler and why it's worth changing.\n\n"
                    "Run linters and type checkers if configured — include "
                    "any real issues they surface.\n\n"
                    f"Write findings to `{simplification_findings}`.\n\n"
                    f"{TRIAGE_FINDINGS_FORMAT}"
                ),
                acceptance_criteria=(
                    f"Findings at {simplification_findings} with concrete proposals."
                ),
                verification=[
                    QuickCheck(
                        path=simplification_findings,
                        description="Findings file exists",
                        error_message=f"Expected findings at {simplification_findings}",
                    ),
                ],
            ),
            GoalStage(
                index=2,
                name="Usability Review",
                parallel_group=1,
                description=(
                    "Review the public interface — whatever users or consumers "
                    "interact with. Read the README, check CLI help/flags, look "
                    "at the library API surface, examine error messages.\n\n"
                    "Look for: redundant options that could be merged or inferred, "
                    "confusing naming, missing defaults, inconsistent patterns, "
                    "poor error messages, documentation that contradicts the code, "
                    "duplicated functionality that confuses users.\n\n"
                    "Think about the experience of someone using this for the "
                    "first time. What would confuse them? What friction could "
                    "be removed?\n\n"
                    f"Write findings to `{usability_findings}`.\n\n"
                    f"{TRIAGE_FINDINGS_FORMAT}"
                ),
                acceptance_criteria=(
                    f"Findings at {usability_findings} with concrete proposals."
                ),
                verification=[
                    QuickCheck(
                        path=usability_findings,
                        description="Findings file exists",
                        error_message=f"Expected findings at {usability_findings}",
                    ),
                ],
            ),
            GoalStage(
                index=3,
                name="Architecture & Security",
                parallel_group=1,
                description=(
                    "Step back and look at the structure. Module boundaries, "
                    "dependency directions, separation of concerns. Are there "
                    "circular dependencies, god modules, responsibilities in "
                    "the wrong place?\n\n"
                    "Also do a lightweight security scan at system boundaries: "
                    "hardcoded secrets, injection risks on external inputs, "
                    "resource leaks.\n\n"
                    f"Write findings to `{architecture_findings}`.\n\n"
                    f"{TRIAGE_FINDINGS_FORMAT}"
                ),
                acceptance_criteria=(
                    f"Findings at {architecture_findings} with concrete proposals."
                ),
                verification=[
                    QuickCheck(
                        path=architecture_findings,
                        description="Findings file exists",
                        error_message=f"Expected findings at {architecture_findings}",
                    ),
                ],
            ),
            GoalStage(
                index=4,
                name="Triage & Verify",
                description=TRIAGE_STAGE_DESCRIPTION.format(
                    triage_path=triage_path,
                )
                + (
                    f"\n\nFindings files: `{simplification_findings}`, "
                    f"`{usability_findings}`, "
                    f"`{architecture_findings}`."
                )
                + prior_needs_decision,
                acceptance_criteria=(f"Every finding has a verdict in {triage_path}."),
                verification=[
                    QuickCheck(
                        path=triage_path,
                        description="Triage results file exists",
                        error_message=f"Expected triage file at {triage_path}",
                    ),
                ],
            ),
            GoalStage(
                index=5,
                name="Fix & Report",
                description=(
                    f"Act only on `fix` and `needs-decision` from `{triage_path}`. "
                    "Ignore `skip`.\n\n"
                    f"Original findings: `{simplification_findings}`, "
                    f"`{usability_findings}`, "
                    f"`{architecture_findings}`.\n\n"
                    "Auto-fix safe issues, flag ambiguous ones. "
                    f"Write report to `{report_path}`:\n\n"
                    f"{IMPROVE_REPORT_FORMAT}\n\n"
                    "Commit auto-fixes: "
                    '"chore: auto-fix issues found by kodo improve".'
                ),
                acceptance_criteria=(
                    f"Report at {report_path}. Auto-fixes committed. "
                    "Only triage-approved findings acted on."
                ),
            ),
        ],
    )


_extract_section = extract_section  # backward compat alias


def _collect_prior_needs_decision(current_run_dir: "RunDir") -> str:
    """Collect 'Needs decision' items from previous improve reports."""
    return collect_prior_report_items(
        current_run_id=current_run_dir.run_id,
        report_glob="*/improve-report.md",
        sections={
            "Needs decision": (
                "\n## Prior unresolved items\n"
                "Previous --improve runs flagged these as 'Needs decision'. "
                "Re-evaluate each one:\n"
                "- If the code has been fixed or the concern is no longer valid, drop it.\n"
                "- If you can now auto-fix it safely without human input, fix it and "
                "list it under 'Auto-fixed'.\n"
                "- Otherwise carry it forward into 'Needs decision'.\n\n{items}\n"
            ),
        },
    )
