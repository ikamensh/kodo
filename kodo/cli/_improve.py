"""Improve mode: staged plans for --improve."""

import enum
import json
import re
from pathlib import Path

from kodo.orchestrators.base import GoalPlan, GoalStage

_IMPROVE_GOAL = """\
Thoroughly test and improve this codebase using a structured sequence of testing \
methodologies. Produce a concrete improvement report at `{report_path}`.

Write your findings to `{report_path}` in this format:

```markdown
# Improve Report

## Auto-fixed
- <file>:<line> — <description of what was fixed>

## Needs decision
- <file>:<line> — <description + suggested fix>
```

Commit all auto-fixes in a single commit with message \
"chore: auto-fix issues found by kodo improve".
"""

_IMPROVE_TIME_GUIDANCE = """\
**Time efficiency is critical.** This codebase may be large or involve slow \
operations (e.g. AI API calls, network requests, heavy builds). You MUST be \
smart about time:
- Mock or stub expensive external calls (APIs, databases, network) rather than \
calling them for real.
- Use targeted, focused tests — not exhaustive sweeps of every file.
- Set short timeouts on any subprocess you run. Kill anything that hangs.
- Prefer testing a representative sample of critical paths over 100% coverage.
- If a test takes more than 30 seconds, abort it and move on.
- Engineer your test setup for speed: in-memory fixtures, lightweight fakes, \
skip heavy initialization."""


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
    to separate files.  Stage 4 reads those files to produce the consolidated
    fix & report.  Parallel stages run in git worktrees for isolation.
    """
    run_dir = str(Path(report_path).parent)
    happy_findings = f"{run_dir}/findings-happy-path.md"
    adversarial_findings = f"{run_dir}/findings-adversarial.md"

    return GoalPlan(
        context=(
            "You are improving an existing codebase. Your job is to find real bugs "
            "and issues by actually RUNNING the software, not just reading code. "
            "Think like a QA engineer, not a code reviewer.\n\n"
            f"{_IMPROVE_TIME_GUIDANCE}"
        ),
        stages=[
            GoalStage(
                index=1,
                name="Baseline & Static Analysis",
                description=(
                    "Run the existing test suite and note any failures or flaky tests. "
                    "Run linters/type-checkers if configured (ruff, mypy, pyright, "
                    "eslint, tsc, etc.). Read through core modules and flag: obvious "
                    "bugs, dead code, unused imports, missing error handling, security "
                    "concerns (hardcoded secrets, unsanitised input), performance "
                    "hot-spots. Run coverage if available and note critical uncovered "
                    "paths.\n\n"
                    "This is the analytical sweep — do it all in one pass, quickly. "
                    "The real value comes in the next stages where you actually run "
                    "the software."
                ),
                acceptance_criteria=(
                    "Test results documented. Lint/type-check results documented. "
                    "List of statically-identified issues with file:line references. "
                    "Coverage gaps noted for critical code paths."
                ),
            ),
            GoalStage(
                index=2,
                name="Happy Path Integration Testing",
                parallel_group=1,
                description=(
                    "Actually USE the software the way a real user would. Identify "
                    "the primary user workflows and run them end-to-end.\n\n"
                    "**Methodology — Scenario Testing:**\n"
                    "1. Read the README, CLI help, or entry points to understand the "
                    "main user-facing commands/APIs.\n"
                    "2. Identify 3-5 core user scenarios (e.g. 'user runs the main "
                    "command with typical inputs', 'user configures and launches').\n"
                    "3. For each scenario: set up realistic inputs, run the actual "
                    "command/function, verify the output is correct.\n"
                    "4. Write integration tests for any scenario that isn't already "
                    "covered.\n\n"
                    f"{_IMPROVE_TIME_GUIDANCE}\n\n"
                    "**Key:** Engineer fast test setups. Mock external services "
                    "(AI APIs, network calls) with realistic fakes. Use temp dirs "
                    "for file operations. The goal is to exercise real code paths "
                    "quickly, not to make real API calls.\n\n"
                    "**IMPORTANT:** Do NOT modify source code. Write all findings "
                    f"to `{happy_findings}`."
                ),
                acceptance_criteria=(
                    "Core user workflows identified and tested end-to-end. "
                    "Integration tests written or existing gaps documented. "
                    "All happy-path scenarios pass or bugs are documented. "
                    f"Detailed findings written to {happy_findings}."
                ),
            ),
            GoalStage(
                index=3,
                name="Exploratory & Adversarial Testing",
                parallel_group=1,
                description=(
                    "Now break it. Use exploratory and negative testing techniques "
                    "to find bugs that happy-path testing misses.\n\n"
                    "**Methodology — Exploratory Testing:**\n"
                    "Use the software freely with a loose charter. Follow your "
                    "intuition. Try things a user might accidentally do. Try "
                    "unusual combinations.\n\n"
                    "**Methodology — Boundary Value & Negative Testing:**\n"
                    "- Feed edge-case inputs: empty strings, None, zero, negative "
                    "numbers, extremely long strings, special characters, unicode.\n"
                    "- Try invalid configurations: missing config files, partial "
                    "configs, wrong types in config values.\n"
                    "- Test error paths: what happens when a dependency is missing? "
                    "When a file doesn't exist? When permissions are wrong?\n"
                    "- Interrupt operations mid-way if possible.\n"
                    "- Try flag/argument combinations that aren't documented.\n\n"
                    f"{_IMPROVE_TIME_GUIDANCE}\n\n"
                    "**Key:** Focus on areas the static analysis (Stage 1) flagged "
                    "as having weak error handling or missing validation. Write "
                    "test cases for bugs you find.\n\n"
                    "**IMPORTANT:** Do NOT modify source code. Write all findings "
                    f"to `{adversarial_findings}`."
                ),
                acceptance_criteria=(
                    "Edge cases and error paths tested. Bugs found are documented "
                    "with reproduction steps. Test cases written for discovered issues. "
                    f"Detailed findings written to {adversarial_findings}."
                ),
            ),
            GoalStage(
                index=4,
                name="Fix & Report",
                description=(
                    "Consolidate everything found across all stages. Read the detailed "
                    f"findings files:\n"
                    f"- `{happy_findings}`\n"
                    f"- `{adversarial_findings}`\n\n"
                    "For every issue:\n"
                    "a. **Auto-fix** it if the fix is safe and unambiguous, or\n"
                    "b. **Flag it** with a one-line description and suggested fix.\n\n"
                    f"Write the final report to `{report_path}` using this format:\n\n"
                    "```markdown\n"
                    "# Improve Report\n\n"
                    "## Auto-fixed\n"
                    "- <file>:<line> — <description of what was fixed>\n\n"
                    "## Needs decision\n"
                    "- <file>:<line> — <description + suggested fix>\n"
                    "```\n\n"
                    "Commit all auto-fixes in a single commit with message "
                    '"chore: auto-fix issues found by kodo improve".\n\n'
                    "Include issues from ALL stages — static analysis, happy path "
                    "failures, and adversarial/exploratory findings."
                ),
                acceptance_criteria=(
                    f"Report written to {report_path} with Auto-fixed and Needs "
                    "decision sections. All auto-fixes committed. Report covers "
                    "findings from all prior stages."
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
    install_path = str(project_dir)

    return GoalPlan(
        context=(
            "You are improving a **library/SDK** codebase. Your job is to evaluate "
            "it from the perspective of a developer who wants to USE this library. "
            "Focus on API ergonomics, documentation accuracy, error message quality, "
            "and developer experience — not just correctness.\n\n"
            f"{_IMPROVE_TIME_GUIDANCE}"
        ),
        stages=[
            GoalStage(
                index=1,
                name="Baseline & API Surface Audit",
                description=(
                    "Run the existing test suite and linters. Then perform a public "
                    "API inventory:\n\n"
                    "1. Identify all public modules, classes, and functions.\n"
                    "2. Check naming consistency (conventions, prefixes, casing).\n"
                    "3. Verify type annotations are present on public APIs.\n"
                    "4. Check docstrings exist and match actual signatures.\n"
                    "5. If docs/ exist, spot-check that documented examples match "
                    "the actual API signatures and behavior.\n"
                    "6. Review error/exception types for consistency and clarity.\n\n"
                    f"Write all findings to `{api_findings}`."
                ),
                acceptance_criteria=(
                    "Test results documented. Lint/type-check results documented. "
                    "Public API inventory with naming, typing, docstring, and docs "
                    f"accuracy assessment written to {api_findings}."
                ),
            ),
            GoalStage(
                index=2,
                name="Consumer Project Testing",
                parallel_group=1,
                description=(
                    "Create a **fresh consumer project** in a temporary directory "
                    "that uses this library as a dependency. This tests real-world "
                    "developer experience.\n\n"
                    "**Steps:**\n"
                    "1. Create a temp directory (use mktemp -d or equivalent).\n"
                    f"2. Install the library: `pip install -e {install_path}` "
                    "(or the equivalent for the project's language).\n"
                    "3. Write a small but realistic project that exercises the main "
                    "API paths — imports, initialization, core operations.\n"
                    "4. Run it and note: import friction, missing re-exports, "
                    "confusing defaults, poor error messages on wrong usage.\n"
                    "5. Assess: could a developer get started from the README alone?\n\n"
                    f"{_IMPROVE_TIME_GUIDANCE}\n\n"
                    "**IMPORTANT:** Do NOT modify the library source code. Write all "
                    f"findings to `{consumer_findings}`."
                ),
                acceptance_criteria=(
                    "Consumer project created and executed. DX friction points "
                    "documented. Import and initialization experience assessed. "
                    f"Detailed findings written to {consumer_findings}."
                ),
            ),
            GoalStage(
                index=3,
                name="API Misuse & Error Quality",
                parallel_group=1,
                description=(
                    "Systematically misuse the library's public API and grade the "
                    "error messages for helpfulness.\n\n"
                    "**Test categories:**\n"
                    "- Wrong argument types (str instead of int, None where not "
                    "expected, wrong container type)\n"
                    "- Missing required arguments\n"
                    "- Wrong call order (use before init, double-close, etc.)\n"
                    "- Edge values (empty collections, zero, negative numbers, "
                    "extremely long strings, unicode)\n\n"
                    "For each error, grade on:\n"
                    "- Does the error message say WHAT went wrong?\n"
                    "- Does it say HOW to fix it?\n"
                    "- Does it point to the right location (not deep internals)?\n"
                    "- Is the exception type appropriate (not bare Exception)?\n\n"
                    f"{_IMPROVE_TIME_GUIDANCE}\n\n"
                    "**IMPORTANT:** Do NOT modify the library source code. Write all "
                    f"findings to `{misuse_findings}`."
                ),
                acceptance_criteria=(
                    "API misuse scenarios tested. Error messages graded for "
                    "helpfulness. Exception types reviewed. "
                    f"Detailed findings written to {misuse_findings}."
                ),
            ),
            GoalStage(
                index=4,
                name="Fix & Report",
                description=(
                    "Consolidate everything found across all stages. Read the detailed "
                    f"findings files:\n"
                    f"- `{api_findings}`\n"
                    f"- `{consumer_findings}`\n"
                    f"- `{misuse_findings}`\n\n"
                    "For every issue:\n"
                    "a. **Auto-fix** it if the fix is safe and unambiguous, or\n"
                    "b. **Flag it** with a one-line description and suggested fix.\n\n"
                    f"Write the final report to `{report_path}` using this format:\n\n"
                    "```markdown\n"
                    "# Improve Report\n\n"
                    "## Auto-fixed\n"
                    "- <file>:<line> — <description of what was fixed>\n\n"
                    "## Needs decision\n"
                    "- <file>:<line> — <description + suggested fix>\n\n"
                    "## Developer Experience Notes\n"
                    "- <observation about DX, import ergonomics, error clarity, etc.>\n"
                    "```\n\n"
                    "Commit all auto-fixes in a single commit with message "
                    '"chore: auto-fix issues found by kodo improve".\n\n'
                    "Include issues from ALL stages — API audit, consumer project, "
                    "and API misuse findings."
                ),
                acceptance_criteria=(
                    f"Report written to {report_path} with Auto-fixed, Needs "
                    "decision, and Developer Experience Notes sections. All auto-fixes "
                    "committed. Report covers findings from all prior stages."
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
