"""Improve mode: AI-driven discovery + staged plans for --improve."""

import json
import re
from pathlib import Path

from kodo.orchestrators.base import GoalPlan, GoalStage

_IMPROVE_REPORT_FORMAT = """\
```markdown
# Improve Report

## Auto-fixed
- <file>:<line> — <description>

## Needs decision
- <file>:<line> — <description + suggested fix>

## Skipped by triage
- <finding title> — <reason>
```"""

_IMPROVE_GOAL = """\
Test and improve this codebase. Report at `{report_path}`.

{report_format}

Commit auto-fixes: "chore: auto-fix issues found by kodo improve".
"""

_IMPROVE_TIME_GUIDANCE = """\
**Be fast.** Mock or stub external calls (APIs, databases, network). \
Use targeted tests, not exhaustive sweeps. Abort anything over 30 seconds. \
In-memory fixtures, lightweight fakes, skip heavy init."""


_TRIAGE_FINDINGS_FORMAT = """\
Format each finding as:

### F<n>: <title>
- **File:** <file>:<line>
- **Severity:** bug | hardening | style | performance
- **Evidence:** <proof: test output, error message, or code path — not just assertions>
- **Proposed fix:** <concrete change>"""

_TRIAGE_STAGE_DESCRIPTION = """\
Skeptically verify each finding from previous stages. Read the actual code \
at the cited location. Most findings are phantoms — default to `skip`.

For each finding, ask:
- Does the evidence hold when you read the actual code?
- Is there already a guard (exception handler, early return, default, \
framework guarantee like `exist_ok=True`)?
- Can the claimed state actually occur? Trace callers.
- Would the fix be net-negative (more code for an impossible case)?

Write `{triage_path}`:

### F<n>: <title>
- **Verdict:** fix | skip | needs-decision
- **Reason:** <1-2 sentences>"""


# ---------------------------------------------------------------------------
# Discovery prompt — sent to an AI session to build a dynamic improve plan
# ---------------------------------------------------------------------------

_DISCOVERY_PROMPT = """\
You are analyzing a software project to create a tailored improvement plan.

## Your Task
1. Inspect the project: read README, config files (pyproject.toml, package.json, \
Cargo.toml, go.mod, etc.), source structure, test setup, CI config
2. Determine: language/stack, available tools (test runners, linters, formatters, \
audit tools), project type (app, library, service, monorepo)
3. Design an improvement plan using the recommendations below as a starting point. \
You may adapt them, combine them, or add your own strategies based on what you \
discover about the project.
4. Write a GoalPlan JSON to `{output_path}`

{methodologies}

## Host Environment
{environment}

## Mandatory Constraints

### Stage structure
- Total stages: 3-6 (inclusive)
- Stages that can run independently SHOULD share a `parallel_group` integer
- Parallel stages MUST set `persist_changes` to false (they explore/test, \
they do NOT modify source code)
- Each testing/analysis stage must write findings to a separate file under \
the run directory: `{run_dir}/findings-<slug>.md`

### Required final stages
The plan MUST end with these two stages (adapt descriptions to the project):

**Triage & Verify** (second-to-last):
{triage_description}

Reference all findings files from earlier stages.

**Fix & Report** (last):
Act only on `fix` and `needs-decision` from triage. Ignore `skip`.
Auto-fix safe issues, flag ambiguous ones.
Write report to `{report_path}`:

{report_format}

Commit auto-fixes: "chore: auto-fix issues found by kodo improve".

### Findings format
All analysis/testing stages must use this format:

{findings_format}

### Time guidance
{time_guidance}

## JSON Output Format
Write the file as valid JSON:

{{
  "context": "Shared context — discovered stack, key files, conventions, tools",
  "stages": [
    {{
      "index": 1,
      "name": "Short label",
      "description": "What this stage accomplishes — full prose for the agent",
      "acceptance_criteria": "Verifiable definition of done",
      "browser_testing": false,
      "parallel_group": null,
      "persist_changes": false
    }}
  ]
}}

IMPORTANT: This is non-interactive. Do NOT ask questions. Inspect the project, \
make reasonable assumptions, and write the JSON file immediately."""


# ---------------------------------------------------------------------------
# Discovery runner
# ---------------------------------------------------------------------------


def run_improve_discovery(run_dir, report_path: str) -> GoalPlan | None:
    """Run AI discovery to build a dynamic improve plan for --improve.

    Uses the shared single-turn plan mechanism from intake to inspect the
    project and produce a ``GoalPlan`` tailored to the stack.  Falls back
    to ``None`` when no backend is available or parsing fails.
    """
    from kodo.cli._intake import run_single_turn_plan
    from kodo.cli._methodologies import METHODOLOGY_LIBRARY, _detect_docker

    output_file = run_dir.goal_plan_file
    run_dir_str = str(run_dir.root)
    triage_path = f"{run_dir_str}/triage-results.md"

    env_lines = []
    if _detect_docker():
        env_lines.append(
            "- **Docker**: available. You can build/run containers for isolated "
            "testing if the project has a Dockerfile or you want a clean environment."
        )
    else:
        env_lines.append("- **Docker**: not available.")
    environment = "\n".join(env_lines)

    prompt = _DISCOVERY_PROMPT.format(
        output_path=str(output_file),
        methodologies=METHODOLOGY_LIBRARY,
        environment=environment,
        run_dir=run_dir_str,
        report_path=report_path,
        triage_description=_TRIAGE_STAGE_DESCRIPTION.format(triage_path=triage_path),
        report_format=_IMPROVE_REPORT_FORMAT,
        findings_format=_TRIAGE_FINDINGS_FORMAT,
        time_guidance=_IMPROVE_TIME_GUIDANCE,
    )

    plan = run_single_turn_plan(
        run_dir,
        system_prompt=prompt,
        initial_message="Analyze this project and create an improvement plan.",
        spinner_text="Discovering project and building improve plan",
    )

    if isinstance(plan, GoalPlan) and plan.stages:
        return _validate_improve_plan(plan, report_path, run_dir_str)
    return None


# ---------------------------------------------------------------------------
# Plan validation — safety net for AI-generated plans
# ---------------------------------------------------------------------------


def _validate_improve_plan(
    plan: GoalPlan, report_path: str, run_dir: str
) -> GoalPlan:
    """Ensure the plan has triage and fix/report as final stages.

    If the AI omitted them, append hardcoded fallback stages.
    """
    stages = list(plan.stages)
    names_lower = [s.name.lower() for s in stages]

    has_triage = any("triage" in n or "verify" in n for n in names_lower)
    has_fix = any("fix" in n or "report" in n for n in names_lower)

    if not has_triage:
        triage_path = f"{run_dir}/triage-results.md"
        stages.append(
            GoalStage(
                index=len(stages) + 1,
                name="Triage & Verify",
                description=_TRIAGE_STAGE_DESCRIPTION.format(triage_path=triage_path)
                + "\n\nReview all findings files from previous stages.",
                acceptance_criteria=f"Every finding has a verdict in {triage_path}.",
            )
        )

    if not has_fix:
        triage_path = f"{run_dir}/triage-results.md"
        stages.append(
            GoalStage(
                index=len(stages) + 1,
                name="Fix & Report",
                description=(
                    f"Act only on `fix` and `needs-decision` from `{triage_path}`. "
                    "Ignore `skip`.\n\n"
                    "Auto-fix safe issues, flag ambiguous ones. "
                    f"Write report to `{report_path}`:\n\n"
                    f"{_IMPROVE_REPORT_FORMAT}\n\n"
                    "Commit auto-fixes: "
                    '"chore: auto-fix issues found by kodo improve".'
                ),
                acceptance_criteria=(
                    f"Report at {report_path}. Auto-fixes committed. "
                    "Only triage-approved findings acted on."
                ),
            )
        )

    return GoalPlan(context=plan.context, stages=stages)


# ---------------------------------------------------------------------------
# Fallback plan — used when discovery is unavailable
# ---------------------------------------------------------------------------


def _build_fallback_plan(report_path: str) -> GoalPlan:
    """Build a generic hardcoded improve plan (fallback when discovery fails).

    This is the original APP plan: baseline, happy path + adversarial in
    parallel, triage, fix & report.
    """
    run_dir = str(Path(report_path).parent)
    happy_findings = f"{run_dir}/findings-happy-path.md"
    adversarial_findings = f"{run_dir}/findings-adversarial.md"
    triage_path = f"{run_dir}/triage-results.md"

    return GoalPlan(
        context=(
            "Find real bugs by RUNNING the software, not just reading code.\n\n"
            f"{_IMPROVE_TIME_GUIDANCE}"
        ),
        stages=[
            GoalStage(
                index=1,
                name="Baseline & Static Analysis",
                description=(
                    "Run test suite, linters, type-checkers. Flag obvious bugs, "
                    "dead code, security concerns, performance hot-spots. "
                    "Quick analytical sweep — one pass.\n\n"
                    f"{_TRIAGE_FINDINGS_FORMAT}"
                ),
                acceptance_criteria=(
                    "Test/lint/type-check results documented. Issues listed with "
                    "file:line. Structured findings format used."
                ),
            ),
            GoalStage(
                index=2,
                name="Happy Path Integration Testing",
                parallel_group=1,
                description=(
                    "Run 3-5 core user scenarios end-to-end. Read entry points, "
                    "set up realistic inputs, verify outputs. Write integration "
                    "tests for uncovered scenarios.\n\n"
                    f"{_IMPROVE_TIME_GUIDANCE}\n\n"
                    "Mock or stub external services. Use temp dirs. Exercise real "
                    "code paths, not real API calls.\n\n"
                    "Do NOT modify source code. Write findings to "
                    f"`{happy_findings}`.\n\n"
                    f"{_TRIAGE_FINDINGS_FORMAT}"
                ),
                acceptance_criteria=(
                    "Core workflows tested end-to-end. Bugs documented. "
                    f"Structured findings written to {happy_findings}."
                ),
            ),
            GoalStage(
                index=3,
                name="Exploratory & Adversarial Testing",
                parallel_group=1,
                description=(
                    "Break it. Edge-case inputs (empty, None, zero, huge, unicode), "
                    "invalid configs, missing dependencies, wrong permissions, "
                    "undocumented flag combos. Focus on areas Stage 1 flagged.\n\n"
                    f"{_IMPROVE_TIME_GUIDANCE}\n\n"
                    "Do NOT modify source code. Write findings to "
                    f"`{adversarial_findings}`.\n\n"
                    f"{_TRIAGE_FINDINGS_FORMAT}"
                ),
                acceptance_criteria=(
                    "Edge cases and error paths tested. Bugs documented with "
                    f"repro steps. Structured findings written to {adversarial_findings}."
                ),
            ),
            GoalStage(
                index=4,
                name="Triage & Verify",
                description=_TRIAGE_STAGE_DESCRIPTION.format(
                    triage_path=triage_path,
                )
                + (
                    f"\n\nFindings files: `{happy_findings}`, "
                    f"`{adversarial_findings}`. "
                    "Also include Stage 1 findings from prior context."
                ),
                acceptance_criteria=(
                    f"Every finding has a verdict in {triage_path}."
                ),
            ),
            GoalStage(
                index=5,
                name="Fix & Report",
                description=(
                    f"Act only on `fix` and `needs-decision` from `{triage_path}`. "
                    "Ignore `skip`.\n\n"
                    f"Original findings: `{happy_findings}`, "
                    f"`{adversarial_findings}`.\n\n"
                    "Auto-fix safe issues, flag ambiguous ones. "
                    f"Write report to `{report_path}`:\n\n"
                    f"{_IMPROVE_REPORT_FORMAT}\n\n"
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
