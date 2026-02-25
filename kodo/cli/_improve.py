"""Improve mode: staged plans for --improve."""

import enum
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


class ProjectType(enum.Enum):
    APP = "app"
    LIBRARY = "library"


def _detect_project_type(project_dir: Path) -> ProjectType:
    """Heuristic detection of project type from project metadata files.

    Returns LIBRARY when the project looks like a reusable package/SDK,
    APP otherwise (safe fallback — preserves existing behavior).
    """
    # Python: pyproject.toml with [project] but no [project.scripts]
    pyproject = project_dir / "pyproject.toml"
    if pyproject.exists():
        try:
            content = pyproject.read_text(encoding="utf-8")
            has_project = bool(re.search(r"^\[project\]", content, re.MULTILINE))
            has_scripts = bool(
                re.search(r"^\[project\.scripts\]", content, re.MULTILINE)
            )
            if has_project and not has_scripts:
                return ProjectType.LIBRARY
        except (OSError, UnicodeDecodeError):
            pass

    # JavaScript: package.json with main/exports but no bin
    package_json = project_dir / "package.json"
    if package_json.exists():
        try:
            pkg = json.loads(package_json.read_text(encoding="utf-8"))
            has_entry = "main" in pkg or "exports" in pkg
            has_bin = "bin" in pkg
            if has_entry and not has_bin:
                return ProjectType.LIBRARY
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            pass

    # Rust: Cargo.toml with [lib] but no [[bin]]
    cargo_toml = project_dir / "Cargo.toml"
    if cargo_toml.exists():
        try:
            content = cargo_toml.read_text(encoding="utf-8")
            has_lib = bool(re.search(r"^\[lib\]", content, re.MULTILINE))
            has_bin = bool(re.search(r"^\[\[bin\]\]", content, re.MULTILINE))
            if has_lib and not has_bin:
                return ProjectType.LIBRARY
        except (OSError, UnicodeDecodeError):
            pass

    # Go: go.mod exists but no main.go or cmd/
    go_mod = project_dir / "go.mod"
    if go_mod.exists():
        has_main = (project_dir / "main.go").exists()
        has_cmd = (project_dir / "cmd").is_dir()
        if not has_main and not has_cmd:
            return ProjectType.LIBRARY

    # Python (no metadata): __init__.py + examples/ or docs/ but no CLI entry
    # Only applies when no pyproject.toml/setup.py already handled detection
    init_files = list(project_dir.glob("*/__init__.py"))
    if init_files and not pyproject.exists():
        has_examples_or_docs = (project_dir / "examples").is_dir() or (
            project_dir / "docs"
        ).is_dir()
        # Check for CLI entry points in setup.py or obvious cli modules
        setup_py = project_dir / "setup.py"
        has_cli = False
        if setup_py.exists():
            try:
                has_cli = "console_scripts" in setup_py.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                pass
        if has_examples_or_docs and not has_cli:
            return ProjectType.LIBRARY

    return ProjectType.APP


def _build_improve_plan(
    report_path: str,
    project_type: ProjectType = ProjectType.APP,
    project_dir: Path | None = None,
) -> GoalPlan:
    """Build a staged plan for --improve mode, dispatching by project type."""
    if project_type == ProjectType.LIBRARY:
        return _build_improve_plan_library(report_path, project_dir)
    return _build_improve_plan_app(report_path)


def _build_improve_plan_app(report_path: str) -> GoalPlan:
    """Build a hardcoded staged plan for --improve mode.

    Stages 2 and 3 run in parallel (``parallel_group=1``) and write findings
    to separate files.  Stage 4 triages findings with skeptical review.
    Stage 5 reads triage results to produce the consolidated fix & report.
    Parallel stages run in git worktrees for isolation.
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


def _build_improve_plan_library(
    report_path: str,
    project_dir: Path | None = None,
) -> GoalPlan:
    """Build a staged plan for --improve mode targeting library/SDK projects.

    Focuses on API surface quality, developer experience, and consumer-side
    testing rather than app-centric happy-path/adversarial testing.
    """
    if project_dir is None:
        raise ValueError("project_dir required for library improve plan")
    run_dir = str(Path(report_path).parent)
    api_findings = f"{run_dir}/findings-api-audit.md"
    consumer_findings = f"{run_dir}/findings-consumer-project.md"
    misuse_findings = f"{run_dir}/findings-api-misuse.md"
    triage_path = f"{run_dir}/triage-results.md"
    install_path = str(project_dir)

    return GoalPlan(
        context=(
            "Evaluate this **library/SDK** as a consumer would. Focus on API "
            "ergonomics, docs accuracy, error quality, and DX.\n\n"
            f"{_IMPROVE_TIME_GUIDANCE}"
        ),
        stages=[
            GoalStage(
                index=1,
                name="Baseline & API Surface Audit",
                description=(
                    "Run tests and linters. Audit public API: naming consistency, "
                    "type annotations, docstrings vs actual signatures, "
                    "error/exception types. Spot-check docs/ examples.\n\n"
                    f"Write findings to `{api_findings}`.\n\n"
                    f"{_TRIAGE_FINDINGS_FORMAT}"
                ),
                acceptance_criteria=(
                    "Test/lint results documented. API inventory written to "
                    f"{api_findings}. Structured findings format used."
                ),
            ),
            GoalStage(
                index=2,
                name="Consumer Project Testing",
                parallel_group=1,
                description=(
                    "Create a fresh consumer project in a temp dir. "
                    f"`pip install -e {install_path}`, write a realistic script "
                    "exercising main API paths, run it. Note import friction, "
                    "missing re-exports, confusing defaults, unhelpful errors. "
                    "Could a developer start from the README alone?\n\n"
                    f"{_IMPROVE_TIME_GUIDANCE}\n\n"
                    "Do NOT modify source code. Write findings to "
                    f"`{consumer_findings}`.\n\n"
                    f"{_TRIAGE_FINDINGS_FORMAT}"
                ),
                acceptance_criteria=(
                    "Consumer project tested. DX friction documented. "
                    f"Structured findings written to {consumer_findings}."
                ),
            ),
            GoalStage(
                index=3,
                name="API Misuse & Error Quality",
                parallel_group=1,
                description=(
                    "Misuse the public API: wrong types, missing args, wrong call "
                    "order, edge values. Grade each error message: does it say what "
                    "went wrong, how to fix it, and point to the right location? "
                    "Are exception types appropriate?\n\n"
                    f"{_IMPROVE_TIME_GUIDANCE}\n\n"
                    "Do NOT modify source code. Write findings to "
                    f"`{misuse_findings}`.\n\n"
                    f"{_TRIAGE_FINDINGS_FORMAT}"
                ),
                acceptance_criteria=(
                    "API misuse tested. Error messages graded. "
                    f"Structured findings written to {misuse_findings}."
                ),
            ),
            GoalStage(
                index=4,
                name="Triage & Verify",
                description=_TRIAGE_STAGE_DESCRIPTION.format(
                    triage_path=triage_path,
                )
                + (
                    f"\n\nFindings files: `{api_findings}`, "
                    f"`{consumer_findings}`, `{misuse_findings}`."
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
                    f"Original findings: `{api_findings}`, "
                    f"`{consumer_findings}`, `{misuse_findings}`.\n\n"
                    "Auto-fix safe issues, flag ambiguous ones. "
                    f"Write report to `{report_path}`:\n\n"
                    "```markdown\n"
                    "# Improve Report\n\n"
                    "## Auto-fixed\n"
                    "- <file>:<line> — <description>\n\n"
                    "## Needs decision\n"
                    "- <file>:<line> — <description + suggested fix>\n\n"
                    "## Developer Experience Notes\n"
                    "- <DX observation>\n\n"
                    "## Skipped by triage\n"
                    "- <finding title> — <reason>\n"
                    "```\n\n"
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
    import re

    pattern = rf"^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)"
    m = re.search(pattern, text, re.MULTILINE | re.DOTALL)
    return m.group(1) if m else ""
