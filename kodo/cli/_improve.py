"""Improve mode: AI-driven discovery + staged plans for --improve."""

import re
from pathlib import Path
from typing import TYPE_CHECKING

from kodo.orchestrators.base import GoalPlan, GoalStage, QuickCheck
from kodo.prompts.improve import (
    DISCOVERY_PROMPT,
    IMPROVE_GOAL,
    IMPROVE_REPORT_FORMAT,
    IMPROVE_TIME_GUIDANCE,
    METHODOLOGY_LIBRARY,
    TRIAGE_FINDINGS_FORMAT,
    TRIAGE_STAGE_DESCRIPTION,
    detect_docker,
)

if TYPE_CHECKING:
    from kodo.log import RunDir


# ---------------------------------------------------------------------------
# Discovery runner
# ---------------------------------------------------------------------------


def run_improve_discovery(
    run_dir, report_path: str, prior_needs_decision: str = "",
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

    env_lines = []
    if detect_docker():
        env_lines.append(
            "- **Docker**: available. You can build/run containers for isolated "
            "testing if the project has a Dockerfile or you want a clean environment.",
        )
    else:
        env_lines.append("- **Docker**: not available.")
    environment = "\n".join(env_lines)

    prompt = DISCOVERY_PROMPT.format(
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

    plan = run_single_turn_plan(
        run_dir,
        system_prompt=prompt,
        initial_message="Analyze this project and create an improvement plan.",
        spinner_text="Planning improvements",
    )

    if isinstance(plan, GoalPlan) and plan.stages:
        return _validate_improve_plan(
            plan, report_path, run_dir_str, prior_needs_decision,
        )
    return None


# ---------------------------------------------------------------------------
# Plan validation — safety net for AI-generated plans
# ---------------------------------------------------------------------------


def _slugify(name: str) -> str:
    """Turn a stage name into a filesystem-safe slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "stage"


def _is_triage_stage(name: str) -> bool:
    n = name.lower()
    return "triage" in n or "verify" in n


def _is_fix_stage(name: str) -> bool:
    n = name.lower()
    return "fix" in n or "report" in n


def _validate_improve_plan(
    plan: GoalPlan, report_path: str, run_dir: str, prior_needs_decision: str = "",
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


def _build_fallback_plan(report_path: str, prior_needs_decision: str = "") -> GoalPlan:
    """Build a generic hardcoded improve plan (fallback when discovery fails).

    Baseline → three parallel explorations (happy path, adversarial,
    architecture) → triage → fix & report.
    """
    run_dir = str(Path(report_path).parent)
    forge_findings = f"{run_dir}/findings-test-tool-forge.md"
    happy_findings = f"{run_dir}/findings-happy-path.md"
    adversarial_findings = f"{run_dir}/findings-adversarial.md"
    architecture_findings = f"{run_dir}/findings-architecture.md"
    triage_path = f"{run_dir}/triage-results.md"

    return GoalPlan(
        context=(
            "Find real bugs and simplification opportunities by RUNNING the "
            "software and reading the code critically.\n\n"
            f"{IMPROVE_TIME_GUIDANCE}"
        ),
        stages=[
            GoalStage(
                index=1,
                name="Test Tool Forge",
                persist_changes=True,
                description=(
                    "Audit the project's existing test infrastructure: fixtures, "
                    "helpers, conftest plugins, test scripts, Docker test setups, "
                    "CI test jobs. Map what categories of bugs each tool can catch.\n\n"
                    "Identify the single highest-impact testing gap — a class of "
                    "bugs or failure modes that existing tools don't cover well. "
                    "Consider: integration contract violations, state machine "
                    "invariants, configuration drift, cross-module interaction "
                    "bugs, data flow corruption, regression traps in recently-"
                    "changed code.\n\n"
                    "For mature projects with good test infrastructure, prefer "
                    "extending an existing tool to cover the gap (e.g. adding new "
                    "test cases to an existing module, enhancing a fixture, "
                    "expanding a script's scope). For projects with sparse testing, "
                    "create a new reusable tool (test module, script, conftest "
                    "plugin, or fixture). Either way, the result must be runnable "
                    "standalone and produce clear pass/fail output.\n\n"
                    "Run the new or enhanced tool against the codebase immediately. "
                    "Report any bugs discovered — these are findings that were "
                    "previously invisible or untested.\n\n"
                    f"Write findings to `{forge_findings}`.\n\n"
                    f"{TRIAGE_FINDINGS_FORMAT}"
                ),
                acceptance_criteria=(
                    "Test tool created or enhanced, committed, and executed. "
                    f"Findings file written to {forge_findings} with any "
                    "discovered bugs."
                ),
                verification=[
                    QuickCheck(
                        path=forge_findings,
                        description="Findings file exists",
                        error_message=f"Expected findings file at {forge_findings}",
                    ),
                ],
            ),
            GoalStage(
                index=2,
                name="Baseline & Static Analysis",
                description=(
                    "Run test suite, linters, type-checkers. Flag obvious bugs, "
                    "dead code, security concerns, performance hot-spots. "
                    "Quick analytical sweep — one pass.\n\n"
                    f"{TRIAGE_FINDINGS_FORMAT}"
                ),
                acceptance_criteria=(
                    "Test/lint/type-check results documented. Issues listed with "
                    "file:line. Structured findings format used."
                ),
                verification="skip",
            ),
            GoalStage(
                index=3,
                name="Happy Path Integration Testing",
                parallel_group=1,
                description=(
                    "Run 3-5 core user scenarios end-to-end. Read entry points, "
                    "set up realistic inputs, verify outputs. Write integration "
                    "tests for uncovered scenarios.\n\n"
                    f"{IMPROVE_TIME_GUIDANCE}\n\n"
                    "Mock or stub external services. Use temp dirs. Exercise real "
                    "code paths, not real API calls.\n\n"
                    "Do NOT modify source code. Write findings to "
                    f"`{happy_findings}`.\n\n"
                    f"{TRIAGE_FINDINGS_FORMAT}"
                ),
                acceptance_criteria=(
                    "Core workflows tested end-to-end. Bugs documented. "
                    f"Structured findings written to {happy_findings}."
                ),
                verification=[
                    QuickCheck(
                        path=happy_findings,
                        description="Findings file exists",
                        error_message=f"Expected findings file at {happy_findings}",
                    ),
                ],
            ),
            GoalStage(
                index=4,
                name="Exploratory & Adversarial Testing",
                parallel_group=1,
                description=(
                    "Break it. Edge-case inputs (empty, None, zero, huge, unicode), "
                    "invalid configs, missing dependencies, wrong permissions, "
                    "undocumented flag combos. Focus on areas Stage 1 flagged.\n\n"
                    f"{IMPROVE_TIME_GUIDANCE}\n\n"
                    "Do NOT modify source code. Write findings to "
                    f"`{adversarial_findings}`.\n\n"
                    f"{TRIAGE_FINDINGS_FORMAT}"
                ),
                acceptance_criteria=(
                    "Edge cases and error paths tested. Bugs documented with "
                    f"repro steps. Structured findings written to {adversarial_findings}."
                ),
                verification=[
                    QuickCheck(
                        path=adversarial_findings,
                        description="Findings file exists",
                        error_message=f"Expected findings file at {adversarial_findings}",
                    ),
                ],
            ),
            GoalStage(
                index=5,
                name="Architecture & Simplification Audit",
                parallel_group=1,
                description=(
                    "Review the codebase for unnecessary complexity and "
                    "simplification opportunities. Focus on things that make "
                    "the code harder to work with today, not theoretical "
                    "concerns.\n\n"
                    "For each finding, explain what's wrong, what it should "
                    "look like instead, and why the change is worth making.\n\n"
                    "Do NOT modify source code. Write findings to "
                    f"`{architecture_findings}`.\n\n"
                    f"{TRIAGE_FINDINGS_FORMAT}"
                ),
                acceptance_criteria=(
                    "Simplification opportunities identified with concrete "
                    f"proposals. Written to {architecture_findings}."
                ),
                verification=[
                    QuickCheck(
                        path=architecture_findings,
                        description="Findings file exists",
                        error_message=f"Expected findings file at {architecture_findings}",
                    ),
                ],
            ),
            GoalStage(
                index=6,
                name="Triage & Verify",
                description=TRIAGE_STAGE_DESCRIPTION.format(
                    triage_path=triage_path,
                )
                + (
                    f"\n\nFindings files: `{forge_findings}`, "
                    f"`{happy_findings}`, "
                    f"`{adversarial_findings}`, "
                    f"`{architecture_findings}`. "
                    "Also include Stage 2 findings from prior context."
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
                index=7,
                name="Fix & Report",
                description=(
                    f"Act only on `fix` and `needs-decision` from `{triage_path}`. "
                    "Ignore `skip`.\n\n"
                    f"Original findings: `{forge_findings}`, "
                    f"`{happy_findings}`, "
                    f"`{adversarial_findings}`, "
                    f"`{architecture_findings}`.\n\n"
                    "Auto-fix safe issues, flag ambiguous ones. "
                    "For architecture simplifications marked `fix`, apply them. "
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


def _extract_section(text: str, heading: str) -> str:
    """Extract the body of a markdown section (## heading) from *text*."""
    pattern = rf"^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)"
    m = re.search(pattern, text, re.MULTILINE | re.DOTALL)
    return m.group(1) if m else ""


def _collect_prior_needs_decision(current_run_dir: "RunDir") -> str:
    """Collect 'Needs decision' items from previous improve reports.

    Scans all ``~/.kodo/runs/*/improve-report.md`` files except the current
    run and returns a prompt fragment listing unresolved items.
    """
    from kodo.log import _runs_root

    runs_dir = _runs_root()
    if not runs_dir.exists():
        return ""

    current_id = current_run_dir.run_id
    items: list[str] = []

    for report_file in sorted(runs_dir.glob("*/improve-report.md")):
        run_id = report_file.parent.name
        if run_id == current_id:
            continue
        try:
            content = report_file.read_text(encoding="utf-8")
        except OSError:
            continue
        section = _extract_section(content, "Needs decision")
        for line in section.strip().splitlines():
            line = line.strip()
            if line.startswith("- "):
                items.append(line)

    if not items:
        return ""

    block = "\n".join(items)
    return (
        f"\n## Prior unresolved items\n"
        f"Previous --improve runs flagged these as 'Needs decision'. "
        f"Re-evaluate each one:\n"
        f"- If the code has been fixed or the concern is no longer valid, drop it.\n"
        f"- If you can now auto-fix it safely without human input, fix it and "
        f"list it under 'Auto-fixed'.\n"
        f"- Otherwise carry it forward into 'Needs decision'.\n\n{block}\n"
    )
