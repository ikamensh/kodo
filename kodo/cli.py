"""kodo interactive CLI — guided project setup and launch."""

import argparse
import enum
import json
import os
import re
import sys
import tempfile
import threading
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import questionary

from kodo import log, make_session, __version__
from kodo.factory import (
    MODES,
    get_mode,
    build_orchestrator,
    has_claude,
    has_cursor,
    has_gemini_cli,
    check_api_key,
    preflight_check_backends,
)
from kodo.log import RunDir
from kodo.orchestrators.base import GoalPlan, GoalStage, ResumeState
from kodo.team_config import load_team_config, build_team_from_json
from kodo.user_config import get_user_default


def _atomic_write(path: Path, content: str, encoding: str = "utf-8") -> None:
    """Write content to path atomically (temp + rename) to avoid corruption on crash."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    tmp_path = Path(tmp)
    try:
        os.write(fd, content.encode(encoding))
        os.close(fd)
        fd = -1
        os.replace(tmp, path)
        tmp_path = None
    finally:
        if fd >= 0:
            os.close(fd)
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink()


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


_BACKEND_LABELS = {
    "ClaudeSession": "claude code",
    "CursorSession": "cursor",
    "CodexSession": "codex",
    "GeminiCliSession": "gemini cli",
}


def _backend_label(agent) -> str:
    return _BACKEND_LABELS.get(type(agent.session).__name__, "?")


def _print_banner() -> None:
    print(f"\n  kodo v{__version__} — autonomous multi-agent coding")
    print("  https://github.com/ikamen/kodo\n")


_INTAKE_PREAMBLE = """\
You are refining a software project goal{purpose}.

Ask 2-3 clarifying questions about constraints, tech choices, and scope.
When clear enough, write {output}."""

_INTAKE_STAGES_SUFFIX = """

JSON format:
{{
  "context": "Shared context — tech stack, key files, conventions",
  "stages": [
    {{
      "index": 1,
      "name": "Short label",
      "description": "What this stage accomplishes",
      "acceptance_criteria": "Verifiable definition of done",
      "browser_testing": false
    }}
  ]
}}

Set browser_testing=true only for stages with web UI to verify in a browser.
Break into 2-5 independently verifiable stages, ordered by dependency."""


def _build_intake_prompt(output_path: str, staged: bool) -> str:
    """Build intake prompt with the correct output file path."""
    if staged:
        prompt = _INTAKE_PREAMBLE.format(
            purpose=" into an ordered list of stages",
            output=f"a structured goal plan to {output_path}",
        )
        return prompt + _INTAKE_STAGES_SUFFIX
    else:
        return _INTAKE_PREAMBLE.format(
            purpose="",
            output=f"a refined, detailed goal to {output_path}",
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_section(text: str, heading: str) -> str:
    """Extract the body of a markdown section (## heading) from *text*."""
    pattern = rf"^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)"
    m = re.search(pattern, text, re.MULTILINE | re.DOTALL)
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# ANSI helpers
# ---------------------------------------------------------------------------

_BOLD = "\033[1m"
_DIM = "\033[2m"
_CYAN = "\033[36m"
_GREEN = "\033[32m"
_RESET = "\033[0m"


def _print_agent(text: str) -> None:
    """Print an agent response with a visible left-border."""
    if not text.strip():
        print(f"\n  {_DIM}(no text response){_RESET}\n")
        return
    lines = text.rstrip().splitlines()
    print()
    for line in lines:
        print(f"  {_DIM}{_CYAN}│{_RESET} {line}")
    print()


def _print_separator() -> None:
    print(f"  {_DIM}{'─' * 60}{_RESET}")


class _Spinner:
    """Simple elapsed-time spinner for long-running operations."""

    FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self, message: str = "Thinking"):
        self._message = message
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread:
            self._thread.join()
        # Clear the spinner line
        print(f"\r{' ' * 60}\r", end="", flush=True)

    def _run(self):
        start = time.monotonic()
        i = 0
        while not self._stop.wait(0.1):
            elapsed = int(time.monotonic() - start)
            frame = self.FRAMES[i % len(self.FRAMES)]
            print(f"\r  {frame} {self._message}... {elapsed}s", end="", flush=True)
            i += 1


def get_goal() -> str:
    """Prompt user for a multiline goal. Empty line finishes input."""
    print("\nWhat's your goal? (Enter an empty line to finish)")
    print("-" * 40)
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line == "":
            break
        lines.append(line)
    text = "\n".join(lines).strip()
    if not text:
        print("No goal provided. Exiting.")
        sys.exit(1)
    return text


def run_intake_chat(
    backend: str,
    run_dir: RunDir,
    goal_text: str,
    staged: bool = False,
) -> GoalPlan | str | None:
    """Interactive intake chat using the Session abstraction.

    Returns GoalPlan if staged + file written, refined goal string if
    single + file written, or None if user bailed.
    """
    run_dir.root.mkdir(parents=True, exist_ok=True)
    log.init(run_dir)

    goal_path = run_dir.goal_file
    _atomic_write(goal_path, goal_text)
    print(f"\nGoal saved to {goal_path}")

    output_file = run_dir.goal_plan_file if staged else run_dir.goal_refined_file
    prompt = _build_intake_prompt(str(output_file), staged)
    _intake_models = {
        "claude": "opus",
        "cursor": "composer-1.5",
        "gemini-cli": "gemini-2.5-flash",
    }
    model = _intake_models.get(backend, "composer-1.5")
    session = make_session(backend, model, system_prompt=prompt)

    print(
        f"\n  {_DIM}Intake interview — type {_BOLD}/done{_RESET}{_DIM} or empty line to finish{_RESET}"
    )
    _print_separator()

    # First message — agent explores the project and asks clarifying questions
    project_dir = run_dir.project_dir
    initial = f"Here's my project goal:\n\n{goal_text}"
    with _Spinner("Reviewing project"):
        result = session.query(initial, project_dir, max_turns=10)
    log.emit(
        "intake_response",
        text=result.text,
        is_error=result.is_error,
        turns=result.turns,
    )
    _print_agent(result.text)

    # If the agent already wrote the output file on the first turn,
    # skip the conversation loop — no need to make the user type /done.
    if output_file.exists():
        _print_separator()
        return _read_intake_output(output_file, staged)

    # Conversation loop
    while True:
        try:
            user_input = input(f"  {_GREEN}{_BOLD}>{_RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_input or user_input == "/done":
            break

        with _Spinner("Thinking"):
            result = session.query(user_input, project_dir, max_turns=10)
        log.emit(
            "intake_response",
            text=result.text,
            is_error=result.is_error,
            turns=result.turns,
        )
        _print_agent(result.text)

    _print_separator()

    # Check if output was written during the conversation
    if output_file.exists():
        return _read_intake_output(output_file, staged)

    # User ended the interview — ask Claude to finalize and write the output
    finalize_msg = (
        "The user has ended the interview. Based on everything discussed, "
        "please write the output file now."
    )
    with _Spinner("Finalizing"):
        result = session.query(finalize_msg, project_dir, max_turns=10)
    _print_agent(result.text)

    if output_file.exists():
        return _read_intake_output(output_file, staged)

    print("\nNo output file written; using original goal.")
    return None


def _read_intake_output(output_file: Path, staged: bool) -> GoalPlan | str | None:
    """Read the intake output file and return the appropriate type."""
    if staged:
        try:
            raw = json.loads(output_file.read_text(encoding="utf-8"))
            plan = _parse_goal_plan(raw)
            if plan.stages:
                print(f"\nGoal plan read from {output_file}")
                print(f"  {len(plan.stages)} stage(s):")
                for s in plan.stages:
                    print(f"    {s.index}. {s.name}")
                return plan
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            print(f"\nWarning: could not read {output_file}: {exc}")
        return None
    else:
        try:
            refined = output_file.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            return None
        if refined:
            print(f"\nRefined goal read from {output_file}")
            return refined
        return None


_AUTO_REFINE_PROMPT = """\
Review this goal before implementation:

{goal}

Concisely answer (2-3 sentences each):
1. **Implicit constraints** — what does this goal imply that isn't stated?
2. **Simplest architecture** — one specific approach, not options.
3. **Common traps** — most likely over-engineering mistake?

Then write a refined goal to {output_path} incorporating the original intent \
plus implicit constraints. Keep it concise — an autonomous agent will read it.
"""

# TODO: The canned questions above are a starting point. Experiment with whether
# letting the LLM ask its own probing questions (rather than canned ones) produces
# better refinement. The hypothesis is that canned "is this the simplest
# architecture" almost never hurts, but LLM-generated questions might catch
# domain-specific traps that canned questions miss.


def run_intake_auto(
    backend: str,
    run_dir: RunDir,
    goal_text: str,
) -> str | None:
    """Automated goal refinement — no human in the loop.

    Uses the same session as interactive intake but sends a single structured
    prompt instead of a conversation. Returns the refined goal string, or None
    if refinement failed.
    """
    run_dir.root.mkdir(parents=True, exist_ok=True)

    goal_path = run_dir.goal_file
    _atomic_write(goal_path, goal_text)

    output_file = run_dir.goal_refined_file
    prompt = _AUTO_REFINE_PROMPT.format(goal=goal_text, output_path=str(output_file))
    _refine_models = {
        "claude": "opus",
        "cursor": "composer-1.5",
        "gemini-cli": "gemini-2.5-flash",
    }
    model = _refine_models.get(backend, "composer-1.5")
    session = make_session(backend, model, system_prompt=prompt)

    project_dir = run_dir.project_dir
    print("\n--- Auto-refining goal (no human input) ---")

    with _Spinner("Analyzing goal"):
        result = session.query(
            f"Here's the project goal to analyze:\n\n{goal_text}",
            project_dir,
            max_turns=10,
        )

    print(f"\n{result.text}\n")

    if output_file.exists():
        try:
            refined = output_file.read_text(encoding="utf-8").strip()
        except OSError:
            refined = ""
        if refined:
            print(f"Refined goal written to {output_file}")
            return refined

    # LLM didn't write the file — use its response as the refinement
    analysis = (result.text or "").strip()
    if analysis:
        refined = f"{goal_text}\n\n# Pre-implementation analysis\n\n{analysis}"
        _atomic_write(output_file, refined)
        print(f"Refined goal written to {output_file}")
        return refined

    print("Auto-refinement produced no output; using original goal.")
    return None


def _looks_staged(goal_text: str) -> bool:
    """Heuristic: detect if goal text has numbered steps or bullet lists."""
    numbered = re.findall(r"^\s*\d+[\.\)]\s+", goal_text, re.MULTILINE)
    return len(numbered) >= 2


def _parse_goal_plan(raw: dict) -> GoalPlan:
    """Convert a raw dict (from JSON) into a GoalPlan dataclass.

    Skips stages that are missing required fields rather than crashing.
    """
    context = raw.get("context")
    if not context:
        return GoalPlan(context="", stages=[])  # malformed input, return empty
    stages = []
    for s in raw.get("stages", []):
        if not isinstance(s, dict):
            continue
        index = s.get("index")
        name = s.get("name")
        description = s.get("description")
        acceptance_criteria = s.get("acceptance_criteria")
        if not index or not name or not description or acceptance_criteria is None:
            continue
        stages.append(
            GoalStage(
                index=index,
                name=name,
                description=description,
                acceptance_criteria=acceptance_criteria,
                browser_testing=bool(s.get("browser_testing", False)),
            )
        )
    return GoalPlan(context=context, stages=stages)


def _load_goal_plan(run_dir: RunDir) -> GoalPlan | None:
    """Load an existing goal-plan.json from the run directory."""
    plan_path = run_dir.goal_plan_file
    if not plan_path.exists():
        return None
    try:
        raw = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    plan = _parse_goal_plan(raw)
    return plan if plan.stages else None


def _labeled_choices(
    options: list[str], default_index: int
) -> list[questionary.Choice]:
    """Build Choice objects, appending '(default)' to the default item's label."""
    choices = []
    for i, opt in enumerate(options):
        label = f"{opt} (default)" if i == default_index else opt
        choices.append(questionary.Choice(title=label, value=opt))
    return choices


def _select_one(title: str, options: list[str], default_index: int = 0) -> str:
    """Arrow-key single selection. Returns the chosen string."""
    choices = _labeled_choices(options, default_index)
    result = questionary.select(title, choices=choices).ask()
    if result is None:
        print("Cancelled.")
        sys.exit(1)
    return result


def _select_numeric(
    title: str, presets: list[str], default_index: int = 0, type_fn: type = int
) -> str:
    """Arrow-key selection with a 'Custom...' option for numeric values."""
    all_options = presets + ["Custom..."]
    choices = _labeled_choices(all_options, default_index)
    result = questionary.select(title, choices=choices).ask()
    if result is None:
        print("Cancelled.")
        sys.exit(1)
    if result != "Custom...":
        return result
    while True:
        raw = questionary.text("  Enter value:").ask()
        if raw is None:
            print("Cancelled.")
            sys.exit(1)
        raw = raw.strip()
        try:
            type_fn(raw)
            return raw
        except (ValueError, TypeError):
            print(f"  Invalid input. Expected {type_fn.__name__}.")


def select_params() -> dict:
    """Interactive arrow-key parameter selection. Returns config dict."""
    print("\n--- Configuration ---\n")

    # Show available backends
    _claude = has_claude()
    _cursor = has_cursor()
    _gemini = has_gemini_cli()
    if not _claude and not _cursor and not _gemini:
        print("Error: no worker backends found.", file=sys.stderr)
        print("  Install at least one of:", file=sys.stderr)
        print(
            "    Claude Code CLI  — https://docs.anthropic.com/en/docs/claude-code",
            file=sys.stderr,
        )
        print("    Cursor CLI       — https://docs.cursor.com/agent", file=sys.stderr)
        print(
            "    Gemini CLI       — https://github.com/google-gemini/gemini-cli",
            file=sys.stderr,
        )
        sys.exit(1)
    parts = []
    parts.append(f"Claude Code: {'yes' if _claude else 'not found'}")
    parts.append(f"Cursor: {'yes' if _cursor else 'not found'}")
    parts.append(f"Gemini CLI: {'yes' if _gemini else 'not found'}")
    print(f"  Backends: {' | '.join(parts)}\n")

    # Mode selection
    mode_options = [f"{name} — {m.description}" for name, m in MODES.items()]
    mode_choice = _select_one("Mode:", mode_options)
    mode_name = mode_choice.split(" — ")[0]
    mode = get_mode(mode_name)

    orch_model = _select_one(
        "Orchestrator model:", ["opus", "sonnet", "gemini-pro", "gemini-flash"]
    )
    if orch_model.startswith("gemini"):
        orchestrator = "api"
    elif not has_claude():
        # claude-code orchestrator requires the claude CLI
        orchestrator = "api"
        print("  (Using API orchestrator — Claude Code CLI not found)")
    else:
        orchestrator = _select_one(
            "Orchestrator:",
            [
                "claude-code (free on Max subscription)",
                "api (pay-per-token)",
            ],
        ).split(" (")[0]

    # Validate API key early
    key_err = check_api_key(orchestrator, orch_model)
    if key_err:
        print(f"\n  Error: {key_err}")
        print("  Set the key in your environment or .env file and try again.")
        sys.exit(1)

    print(
        "\n  An exchange = one orchestrator turn: think, delegate to agent, read result."
    )
    exchange_presets = ["20", "30", "50"]
    default_ex = str(mode.default_max_exchanges)
    ex_default_idx = (
        exchange_presets.index(default_ex) if default_ex in exchange_presets else 1
    )
    max_exchanges = _select_numeric(
        "Max exchanges per cycle:", exchange_presets, default_index=ex_default_idx
    )

    print("\n  A cycle = one full orchestrator session. If it doesn't finish,")
    print("  a new cycle starts with a summary of prior progress.")
    cycle_presets = ["1", "3", "5", "10"]
    default_cy = str(mode.default_max_cycles)
    cy_default_idx = (
        cycle_presets.index(default_cy) if default_cy in cycle_presets else 2
    )
    max_cycles = _select_numeric(
        "Max cycles:", cycle_presets, default_index=cy_default_idx
    )

    return {
        "mode": mode_name,
        "orchestrator": orchestrator,
        "orchestrator_model": orch_model,
        "max_exchanges": int(max_exchanges),
        "max_cycles": int(max_cycles),
    }


def _config_path(project_dir: Path) -> Path:
    return project_dir / ".kodo" / "config.json"


def _save_config(project_dir: Path, params: dict) -> None:
    path = _config_path(project_dir)
    _atomic_write(path, json.dumps(params, indent=2))


def _load_or_select_params(project_dir: Path) -> dict:
    """Offer to reuse previous config, or run interactive selection."""
    cfg_path = _config_path(project_dir)
    # Legacy fallback
    if not cfg_path.exists():
        legacy = project_dir / ".kodo" / "last-config.json"
        if legacy.exists():
            cfg_path = legacy
    required_keys = {
        "mode",
        "orchestrator",
        "orchestrator_model",
        "max_exchanges",
        "max_cycles",
    }
    if cfg_path.exists():
        try:
            prev = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            prev = None
        if isinstance(prev, dict) and required_keys <= prev.keys():
            mode = get_mode(prev["mode"])
            print("\n  Previous config found:")
            print(f"    Mode:         {mode.name} — {mode.description}")
            print(
                f"    Orchestrator: {prev['orchestrator']} ({prev['orchestrator_model']})"
            )
            print(
                f"    Exchanges:    {prev['max_exchanges']}/cycle, {prev['max_cycles']} cycles"
            )
            reuse = input("\n  Reuse this config? [Y/n] ").strip().lower()
            if not reuse or reuse == "y":
                return prev

    params = select_params()
    try:
        _save_config(project_dir, params)
    except PermissionError:
        _fail(f"Cannot write config to {project_dir / '.kodo'} (permission denied)")
    return params


def launch_run(
    run_dir: RunDir,
    goal_text: str,
    params: dict,
    plan: GoalPlan | None = None,
    json_mode: bool = False,
):
    """Build team + orchestrator and run. Returns the RunResult."""
    # Snapshot config and goal into the run directory
    _atomic_write(run_dir.config_file, json.dumps(params, indent=2))
    if not run_dir.goal_file.exists():
        _atomic_write(run_dir.goal_file, goal_text)

    log_path = log.init(run_dir)
    log.emit("cli_args", **params, goal_text=goal_text, has_plan=plan is not None)

    project_dir = run_dir.project_dir

    mode = get_mode(params["mode"])
    verifiers = None

    # Try loading a team JSON config; fall back to hardcoded mode
    try:
        team_config = load_team_config(params["mode"], project_dir)
        if team_config:
            team = build_team_from_json(team_config)
            system_prompt = team_config.get("orchestrator_prompt") or mode.system_prompt
            verifiers = team_config.get("verifiers")
            max_exchanges = team_config.get("max_exchanges", params["max_exchanges"])
            max_cycles = team_config.get("max_cycles", params["max_cycles"])
        else:
            team = mode.build_team()
            system_prompt = mode.system_prompt
            max_exchanges = params["max_exchanges"]
            max_cycles = params["max_cycles"]
    except (ValueError, KeyError, RuntimeError, OSError) as exc:
        _fail(f"Invalid team config: {exc}")

    orchestrator = build_orchestrator(
        params["orchestrator"],
        params["orchestrator_model"],
        system_prompt=system_prompt,
    )

    # Preflight: smoke-test backends before committing to a long run
    preflight_warnings = preflight_check_backends(team)
    if preflight_warnings:
        if len(preflight_warnings) == len(team):
            # ALL backends failed — abort
            _fail(
                "All backends failed preflight checks:\n"
                + "\n".join(preflight_warnings)
                + "\nFix the issues above or install a working backend."
            )
        if not json_mode:
            print("\n⚠ Backend preflight warnings:")
            for w in preflight_warnings:
                print(w)
            print("  (Continuing — some backends may fail at runtime)\n")
        log.emit("preflight_warnings", warnings=preflight_warnings)

    if not json_mode:
        print(f"\nMode: {mode.name} — {mode.description}")
        if team_config:
            team_name = team_config.get("name", "custom")
            print(f"Team config: {team_name}")
        print(f"Orchestrator: {params['orchestrator']} ({orchestrator.model})")
        print("Team:")
        for k, a in team.items():
            print(f"  {k} ({_backend_label(a)} / {a.session.model})")
        print(f"Project dir: {project_dir}")
        print(f"Max: {max_exchanges} exchanges/cycle, {max_cycles} cycles")
        if plan:
            print(f"Stages: {len(plan.stages)}")
        print(f"Log: {log_path}")
        print()

    result = orchestrator.run(
        goal_text,
        project_dir,
        team,
        max_exchanges=max_exchanges,
        max_cycles=max_cycles,
        plan=plan,
        verifiers=verifiers,
        auto_commit=params.get("auto_commit", True),
    )

    if not json_mode:
        print(f"\n{'=' * 50}")
        if result.stage_results:
            completed = sum(1 for sr in result.stage_results if sr.finished)
            print(
                f"Done: {completed}/{len(result.stage_results)} stage(s) completed, "
                f"{len(result.cycles)} cycle(s), {result.total_exchanges} exchanges, "
                f"${result.total_cost_usd:.4f}"
            )
        else:
            print(
                f"Done: {len(result.cycles)} cycle(s), {result.total_exchanges} exchanges, ${result.total_cost_usd:.4f}"
            )
        if result.summary:
            print(f"  {result.summary[:300]}")

    return result


def launch_resume(run_dir: RunDir, state: log.RunState):
    """Resume an interrupted run from its parsed RunState. Returns the RunResult."""
    log.init_append(state.log_file)

    project_dir = run_dir.project_dir

    # Load params from run config if available; otherwise reconstruct from RunState
    required_keys = {
        "mode",
        "orchestrator",
        "orchestrator_model",
        "max_exchanges",
        "max_cycles",
    }
    params = {}
    if run_dir.config_file.exists():
        try:
            loaded = json.loads(run_dir.config_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and required_keys <= loaded.keys():
                params = loaded
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            pass
    if not params:
        params = {
            "mode": state.mode or "saga",
            "orchestrator": "api" if state.orchestrator == "api" else "claude-code",
            "orchestrator_model": state.model,
            "max_exchanges": state.max_exchanges,
            "max_cycles": state.max_cycles,
        }

    mode = get_mode(params["mode"])
    verifiers = None

    try:
        team_config = load_team_config(params["mode"], project_dir)
        if team_config:
            team = build_team_from_json(team_config)
            system_prompt = team_config.get("orchestrator_prompt") or mode.system_prompt
            verifiers = team_config.get("verifiers")
        else:
            team = mode.build_team()
            system_prompt = mode.system_prompt
    except (ValueError, KeyError, RuntimeError, OSError) as exc:
        _fail(f"Invalid team config: {exc}")

    orchestrator = build_orchestrator(
        params["orchestrator"],
        params["orchestrator_model"],
        system_prompt=system_prompt,
    )

    resume = ResumeState(
        completed_cycles=state.completed_cycles,
        prior_summary=state.last_summary,
        agent_session_ids=state.agent_session_ids,
        completed_stages=state.completed_stages,
        stage_summaries=state.stage_summaries,
        current_stage_cycles=state.current_stage_cycles,
        pending_exchanges=state.pending_exchanges,
    )

    # Load goal plan if this was a staged run
    plan: GoalPlan | None = None
    if state.has_stages:
        plan = _load_goal_plan(run_dir)
        if plan is None:
            _fail("Cannot resume: staged run but goal-plan.json not found or invalid.")

    print(f"\nResuming run: {state.run_id}")
    print(f"Mode: {mode.name} — {mode.description}")
    print(f"Orchestrator: {params['orchestrator']} ({orchestrator.model})")
    print("Team:")
    for k, a in team.items():
        print(f"  {k} ({_backend_label(a)} / {a.session.model})")
    print(f"Completed cycles: {state.completed_cycles}/{state.max_cycles}")
    if state.has_stages:
        print(
            f"Completed stages: {len(state.completed_stages)}"
            + (f"/{plan and len(plan.stages)}" if plan else "")
        )
    if state.agent_session_ids:
        print(f"Resuming sessions: {', '.join(state.agent_session_ids.keys())}")
    if state.pending_exchanges:
        print(
            f"Resuming mid-cycle: {len(state.pending_exchanges)} exchange(s) to restore"
        )
    print(f"Log: {state.log_file}")
    print()

    result = orchestrator.run(
        state.goal,
        Path(state.project_dir),
        team,
        max_exchanges=params["max_exchanges"],
        max_cycles=params["max_cycles"],
        resume=resume,
        plan=plan,
        verifiers=verifiers,
        auto_commit=params.get("auto_commit", True),
    )

    total_cycles = state.completed_cycles + len(result.cycles)
    print(f"\n{'=' * 50}")
    print(
        f"Done: {total_cycles} total cycle(s), {result.total_exchanges} exchanges (this session), "
        f"${result.total_cost_usd:.4f}"
    )
    if result.summary:
        print(f"  {result.summary[:300]}")

    return result


# ---------------------------------------------------------------------------
# JSON output helpers
# ---------------------------------------------------------------------------

EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_PARTIAL = 2

# Will be set to the real stdout when --json redirects sys.stdout to stderr
_original_stdout = None


def _fail(msg: str, code: int = 1) -> None:
    """Print error and exit. In JSON mode, outputs JSON to original stdout."""
    if _original_stdout is not None:
        sys.stdout = _original_stdout
        print(json.dumps(_format_json_output(error=msg)))
        sys.exit(EXIT_ERROR)
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(code)


def _emit_json_and_exit(args, result, improve_report: str | None = None) -> None:
    """If --json, emit result JSON to stdout and exit. Otherwise no-op."""
    if not args.json:
        return
    sys.stdout = _original_stdout
    print(
        json.dumps(_format_json_output(result, improve_report=improve_report), indent=2)
    )
    sys.exit(EXIT_SUCCESS if result.finished else EXIT_PARTIAL)


def _format_json_output(
    result=None, error: str | None = None, improve_report: str | None = None
) -> dict:
    """Build the structured JSON output dict."""
    if error is not None:
        return {"status": "error", "error": error}

    if result.finished:
        status = "completed"
    elif result.cycles:
        status = "partial"
    else:
        status = "failed"

    output = {
        "status": status,
        "finished": result.finished,
        "cycles": len(result.cycles),
        "exchanges": result.total_exchanges,
        "cost_usd": round(result.total_cost_usd, 4),
        "summary": result.summary,
    }

    if result.stage_results:
        output["stages"] = [
            {
                "index": sr.stage_index,
                "name": sr.stage_name,
                "finished": sr.finished,
                "summary": sr.summary,
                "cycles": len(sr.cycles),
            }
            for sr in result.stage_results
        ]

    if improve_report is not None:
        output["improve_report"] = improve_report

    return output


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    json_mode = "--json" in sys.argv
    try:
        _main_inner()
    except KeyboardInterrupt:
        if json_mode:
            print(json.dumps({"status": "error", "error": "Interrupted"}))
        else:
            print("\nInterrupted.")
        sys.exit(130)
    except SystemExit:
        raise
    except Exception as exc:
        if json_mode:
            print(json.dumps({"status": "error", "error": str(exc)}))
            sys.exit(EXIT_ERROR)
        raise


def _first_backend() -> str | None:
    """Return the first available backend key, or None."""
    if has_claude():
        return "claude"
    if has_cursor():
        return "cursor"
    if has_gemini_cli():
        return "gemini-cli"
    return None


def _offer_intake(run_dir: RunDir, goal_text: str) -> GoalPlan | str | None:
    """Offer goal refinement before launch. Returns refined goal/plan or None."""
    backend = _first_backend()
    if not backend:
        print("\nSkipping refinement (no backends available).")
        return None

    options = [
        "Quick refine — surfaces implicit constraints, no conversation",
        "Interview — interactive Q&A, optionally break into stages",
        "Skip",
    ]
    choice = _select_one("\nRefine goal before launch?", options)
    if choice.startswith("Skip"):
        return None

    if choice.startswith("Quick"):
        refined = run_intake_auto(backend, run_dir, goal_text)
        return refined

    # Interview — let user pick backend if multiple are available
    backends: list[str] = []
    if has_claude():
        backends.append("Claude")
    if has_cursor():
        backends.append("Cursor")
    if has_gemini_cli():
        backends.append("Gemini CLI")

    if len(backends) > 1:
        _backend_map = {
            "Claude": "claude",
            "Cursor": "cursor",
            "Gemini CLI": "gemini-cli",
        }
        backend = _backend_map[_select_one("Interview backend:", backends)]

    staged = False
    if _looks_staged(goal_text):
        print("This goal looks like it has multiple steps.")
        stage_choice = input("Break into stages? [Y/n] ").strip().lower()
        staged = not stage_choice or stage_choice == "y"
    else:
        stage_choice = input("Break into stages? [y/N] ").strip().lower()
        staged = stage_choice == "y"

    return run_intake_chat(backend, run_dir, goal_text, staged=staged)


def _build_params_from_flags(args, project_dir: Path) -> dict:
    """Build config dict from CLI flags, falling back to mode defaults."""
    mode_name = args.mode or "saga"
    mode = get_mode(mode_name)

    orch_model = args.orchestrator_model or "gemini-flash"

    if args.orchestrator:
        orchestrator = args.orchestrator
    elif orch_model.startswith("gemini"):
        orchestrator = "api"
    elif not has_claude():
        orchestrator = "api"
    else:
        orchestrator = "claude-code"

    key_err = check_api_key(orchestrator, orch_model)
    if key_err:
        _fail(key_err)

    params = {
        "mode": mode_name,
        "orchestrator": orchestrator,
        "orchestrator_model": orch_model,
        "max_exchanges": args.exchanges
        if args.exchanges and args.exchanges > 0
        else mode.default_max_exchanges,
        "max_cycles": args.cycles
        if args.cycles and args.cycles > 0
        else mode.default_max_cycles,
    }

    # Auto-commit: on by default, disabled with --no-auto-commit or user config
    auto_commit = get_user_default("auto_commit", True)
    if getattr(args, "no_auto_commit", False):
        auto_commit = False
    params["auto_commit"] = auto_commit

    try:
        _save_config(project_dir, params)
    except PermissionError:
        _fail(f"Cannot write config to {project_dir / '.kodo'} (permission denied)")
    return params


def run_intake_noninteractive(
    run_dir: RunDir,
    goal_text: str,
) -> GoalPlan | None:
    """Non-interactive intake: send goal, get staged plan back, no conversation."""
    run_dir.root.mkdir(parents=True, exist_ok=True)

    goal_path = run_dir.goal_file
    _atomic_write(goal_path, goal_text)

    if has_claude():
        backend, model = "claude", "opus"
    elif has_cursor():
        backend, model = "cursor", "composer-1.5"
    elif has_gemini_cli():
        backend, model = "gemini-cli", "gemini-2.5-flash"
    else:
        print("Skipping intake (no backends available).")
        return None

    output_file = run_dir.goal_plan_file
    prompt = _build_intake_prompt(str(output_file), staged=True) + (
        "\n\nIMPORTANT: This is a non-interactive session. "
        "Do NOT ask clarifying questions. Analyze the project and goal, "
        "make reasonable assumptions, and write the goal-plan.json file immediately."
    )
    session = make_session(backend, model, system_prompt=prompt)

    project_dir = run_dir.project_dir
    initial = f"Here's my project goal:\n\n{goal_text}"
    print("Running intake (non-interactive)...")
    with _Spinner("Analyzing project and creating plan"):
        result = session.query(initial, project_dir, max_turns=10)
    print(f"\n{result.text}\n")

    if not output_file.exists():
        with _Spinner("Finalizing plan"):
            result = session.query(
                "Please write the goal-plan.json file now based on your analysis.",
                project_dir,
                max_turns=10,
            )
        print(f"\n{result.text}\n")

    if output_file.exists():
        return _read_intake_output(output_file, staged=True)

    print("Warning: intake did not produce a plan. Proceeding without stages.")
    return None


def _cmd_runs() -> None:
    """List all known runs from ~/.kodo/runs/."""
    parser = argparse.ArgumentParser(description="List kodo runs")
    parser.add_argument(
        "project_dir",
        nargs="?",
        default=None,
        help="Filter to runs for this project directory",
    )
    args = parser.parse_args(sys.argv[2:])

    project = Path(args.project_dir).resolve() if args.project_dir else None
    runs = log.list_runs(project)

    if not runs:
        print("No runs found.")
        return

    # Column widths
    id_w = max(len(r.run_id) for r in runs)
    dir_w = max(len(r.project_dir) for r in runs)

    header = f"  {'RUN ID':<{id_w}}  {'STATUS':<10}  {'PROJECT':<{dir_w}}  GOAL"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for r in runs:
        status = "done" if r.finished else f"cycle {r.completed_cycles}/{r.max_cycles}"
        goal_snippet = r.goal[:60].replace("\n", " ")
        if len(r.goal) > 60:
            goal_snippet += "..."
        print(
            f"  {r.run_id:<{id_w}}  {status:<10}  {r.project_dir:<{dir_w}}  {goal_snippet}"
        )


def _cmd_backends() -> None:
    """List available backends (CLI agents and API orchestrator models)."""
    import os
    import subprocess

    from kodo.factory import (
        available_backends,
        _PREFLIGHT_CMDS,
        _MODEL_ALIASES,
        check_api_key,
    )

    available_backends.cache_clear()
    backends = available_backends()

    _INSTALL_LINKS: dict[str, str] = {
        "claude": "https://docs.anthropic.com/en/docs/claude-code",
        "codex": "https://github.com/openai/codex",
        "cursor": "https://docs.cursor.com/agent",
        "gemini-cli": "https://github.com/google-gemini/gemini-cli",
    }

    # --- CLI backends (agents) ---
    print("CLI backends (agents):")
    for name, present in backends.items():
        if not present:
            link = _INSTALL_LINKS.get(name, "")
            print(f"  {name:<12}  not found  {link}")
            continue

        # Get version
        cmd = _PREFLIGHT_CMDS.get(name)
        version = "?"
        if cmd:
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if proc.returncode == 0:
                    version = proc.stdout.strip().split("\n")[0]
                else:
                    version = f"error (exit {proc.returncode})"
            except (subprocess.TimeoutExpired, OSError):
                version = "error"

        print(f"  {name:<12}  {version}")

    # --- API orchestrator models ---
    print("\nOrchestrator models (API):")
    for alias, full_id in sorted(_MODEL_ALIASES.items()):
        key_err = check_api_key("api", alias)
        status = "ready" if key_err is None else "no key"
        provider = "Gemini" if full_id.startswith("gemini") else "Anthropic"
        print(f"  {alias:<14}  {full_id:<35}  {provider:<10}  {status}")

    # --- API key status ---
    print("\nAPI keys:")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    gemini_key = os.environ.get("GEMINI_API_KEY")
    google_key = os.environ.get("GOOGLE_API_KEY")

    def _masked(val: str) -> str:
        return f"{val[:4]}...{val[-4:]}" if len(val) > 12 else "***"

    if anthropic_key:
        print(f"  ANTHROPIC_API_KEY       set ({_masked(anthropic_key)})")
    else:
        print(
            "  ANTHROPIC_API_KEY       not set  https://console.anthropic.com/settings/keys"
        )

    # GEMINI_API_KEY and GOOGLE_API_KEY are interchangeable for Gemini
    gkey = gemini_key or google_key
    if gkey:
        source = "GEMINI_API_KEY" if gemini_key else "GOOGLE_API_KEY"
        print(f"  Gemini                  set via {source} ({_masked(gkey)})")
    else:
        print("  Gemini                  not set  https://aistudio.google.com/apikey")


def _cmd_teams() -> None:
    """Dispatch `kodo teams [add|edit] [name]`."""
    args = sys.argv[2:]

    if not args:
        _cmd_teams_list()
        return

    subcmd = args[0]
    if subcmd == "add":
        if len(args) < 2:
            print("Usage: kodo teams add <name>")
            sys.exit(1)
        _cmd_teams_add(args[1])
    elif subcmd == "edit":
        if len(args) < 2:
            print("Usage: kodo teams edit <name>")
            sys.exit(1)
        _cmd_teams_edit(args[1])
    elif subcmd == "auto":
        mode_name = args[1] if len(args) >= 2 else "saga"
        _cmd_teams_auto(mode_name)
    else:
        print(f"Unknown teams subcommand: {subcmd}")
        print("Usage: kodo teams [add <name> | edit <name> | auto [mode]]")
        sys.exit(1)


def _cmd_teams_list() -> None:
    """List all available teams (built-in and user-defined)."""
    from kodo.team_config import list_available_teams, _BACKEND_MAP

    from kodo.factory import available_backends

    available_backends.cache_clear()
    backends = available_backends()

    teams = list_available_teams()
    if not teams:
        print("No teams found.")
        return

    for name, source, cfg in teams:
        desc = cfg.get("description", "")
        agents = cfg.get("agents", {})
        exchanges = cfg.get("max_exchanges", "?")
        cycles = cfg.get("max_cycles", "?")
        tag = "(built-in)" if source == "built-in" else "(user)"

        # Count available agents
        available_count = sum(
            1
            for acfg in agents.values()
            if backends.get(_BACKEND_MAP.get(acfg.get("backend", ""), ""), False)
        )
        avail_str = f"{available_count}/{len(agents)} available"

        print(f"{name}  {tag}")
        if desc:
            print(f"  {desc}")
        print(f"  {len(agents)} agents ({avail_str}), {exchanges} exchanges, {cycles} cycles")

        for akey, acfg in agents.items():
            backend = acfg.get("backend", "?")
            model = acfg.get("model", "?")
            adesc = acfg.get("description", "").split("\n")[0][:60]
            backend_key = _BACKEND_MAP.get(backend, "")
            ok = backends.get(backend_key, False)
            status = "ok" if ok else "missing"
            print(
                f"    {akey:<20}  {backend:<12}  {model:<20}  [{status}]  {adesc}"
            )
        print()


def _cmd_teams_auto(mode_name: str) -> None:
    """Generate a viable team config from available backends."""
    from kodo.team_config import list_available_teams, _BACKEND_MAP

    from kodo.factory import available_backends

    available_backends.cache_clear()
    backends = available_backends()

    has = {name: present for name, present in backends.items()}
    any_available = any(has.values())

    if not any_available:
        print("No backends available. Install at least one of:")
        print("  claude, cursor, codex, gemini-cli")
        print("Run 'kodo backends' for install links.")
        sys.exit(1)

    # Find the base template (built-in or user team matching mode_name)
    base_config = None
    for tname, _tsource, tcfg in list_available_teams():
        if tname == mode_name:
            base_config = tcfg
            break

    if base_config is None:
        print(f"No template found for mode {mode_name!r}.")
        print("Available templates:", ", ".join(t[0] for t in list_available_teams()))
        sys.exit(1)

    # Filter agents to only those with available backends
    src_agents = base_config.get("agents", {})
    agents = {}
    skipped = []
    for akey, acfg in src_agents.items():
        backend = acfg.get("backend", "")
        backend_key = _BACKEND_MAP.get(backend, "")
        if backends.get(backend_key, False):
            agents[akey] = acfg
        else:
            skipped.append((akey, backend))

    # Try to fill essential roles with fallback backends
    # Priority for fast worker: cursor > codex > gemini-cli > claude
    # Priority for smart worker: claude > gemini-cli
    _FAST_FALLBACKS = [
        ("cursor", "composer-1.5"),
        ("codex", "gpt-5.2-codex"),
        ("gemini-cli", "gemini-2.5-flash"),
        ("claude", "sonnet"),
    ]
    _SMART_FALLBACKS = [
        ("claude", "opus"),
        ("gemini-cli", "gemini-2.5-pro"),
        ("cursor", "composer-1.5"),
    ]

    def _find_fallback(
        fallbacks: list[tuple[str, str]],
    ) -> tuple[str, str] | None:
        for fb_backend, fb_model in fallbacks:
            if backends.get(fb_backend, False):
                return fb_backend, fb_model
        return None

    if "worker_fast" not in agents and "worker_fast" in src_agents:
        fb = _find_fallback(_FAST_FALLBACKS)
        if fb:
            agents["worker_fast"] = {
                **src_agents["worker_fast"],
                "backend": fb[0],
                "model": fb[1],
            }

    if "worker_smart" not in agents and "worker_smart" in src_agents:
        fb = _find_fallback(_SMART_FALLBACKS)
        if fb:
            agents["worker_smart"] = {
                **src_agents["worker_smart"],
                "backend": fb[0],
                "model": fb[1],
            }
            if fb[0] != "claude":
                # Remove claude-specific fields
                agents["worker_smart"].pop("fallback_model", None)

    # For tester/architect, try to fill with available backends
    for role, fallbacks in [
        ("tester", _FAST_FALLBACKS),
        ("architect", _SMART_FALLBACKS),
    ]:
        if role not in agents and role in src_agents:
            fb = _find_fallback(fallbacks)
            if fb:
                agents[role] = {
                    **src_agents[role],
                    "backend": fb[0],
                    "model": fb[1],
                }
                if fb[0] != "claude":
                    agents[role].pop("fallback_model", None)

    if not agents:
        print("Could not create any agents with available backends.")
        sys.exit(1)

    # Build verifiers from agents that are actually present
    src_verifiers = base_config.get("verifiers", {})
    verifiers = {}
    for role, agent_keys in src_verifiers.items():
        verifiers[role] = [k for k in agent_keys if k in agents]

    config = {
        "name": mode_name,
        "description": base_config.get("description", ""),
        "max_exchanges": base_config.get("max_exchanges", 20),
        "max_cycles": base_config.get("max_cycles", 1),
        "verifiers": verifiers,
        "agents": agents,
    }
    if "orchestrator_prompt" in base_config:
        config["orchestrator_prompt"] = base_config["orchestrator_prompt"]

    # Show what we generated
    print(f"Generated team {mode_name!r} for your setup:\n")
    for akey, acfg in agents.items():
        print(f"  {akey:<20}  {acfg['backend']:<12}  {acfg['model']}")
    if skipped:
        print(f"\n  Skipped (backend missing): {', '.join(f'{a} ({b})' for a, b in skipped)}")
    print()

    _save_team(mode_name, config)
    print(f"\nUse with: kodo --mode {mode_name}")


def _teams_dir() -> Path:
    """User teams directory, created on demand."""
    d = Path.home() / ".kodo" / "teams"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ask_agent_fields(
    defaults: dict | None = None,
) -> dict:
    """Interactively collect fields for one agent definition."""
    from kodo.team_config import _BACKEND_MAP, _AGENT_DEFAULTS

    d = defaults or {}
    backends = list(_BACKEND_MAP.keys())

    backend = questionary.select(
        "Backend:",
        choices=backends,
        default=d.get("backend", backends[0]),
    ).ask()
    if backend is None:
        print("Cancelled.")
        sys.exit(1)

    model = questionary.text(
        "Model:", default=d.get("model", "")
    ).ask()
    if model is None:
        print("Cancelled.")
        sys.exit(1)

    description = questionary.text(
        "Description (tool description for orchestrator):",
        default=d.get("description", _AGENT_DEFAULTS["description"]),
    ).ask()
    if description is None:
        print("Cancelled.")
        sys.exit(1)

    system_prompt = questionary.text(
        "System prompt (Enter to skip):",
        default=d.get("system_prompt") or "",
    ).ask()
    if system_prompt is None:
        print("Cancelled.")
        sys.exit(1)

    max_turns = questionary.text(
        "Max turns:", default=str(d.get("max_turns", _AGENT_DEFAULTS["max_turns"]))
    ).ask()
    if max_turns is None:
        print("Cancelled.")
        sys.exit(1)

    timeout_raw = questionary.text(
        "Timeout (seconds, empty for none):",
        default=str(d.get("timeout_s") or ""),
    ).ask()
    if timeout_raw is None:
        print("Cancelled.")
        sys.exit(1)

    agent: dict = {
        "backend": backend,
        "model": model.strip(),
        "description": description,
        "max_turns": int(max_turns),
    }
    if system_prompt.strip():
        agent["system_prompt"] = system_prompt
    if timeout_raw.strip():
        agent["timeout_s"] = int(timeout_raw)

    # Optional fields
    if backend == "claude":
        fallback = questionary.text(
            "Fallback model (Enter to skip):",
            default=d.get("fallback_model") or "",
        ).ask()
        if fallback and fallback.strip():
            agent["fallback_model"] = fallback.strip()

    chrome_q = questionary.confirm(
        "Enable Chrome/browser access?",
        default=d.get("chrome", False),
    ).ask()
    if chrome_q:
        agent["chrome"] = True

    return agent


def _save_team(name: str, config: dict) -> Path:
    """Write team config to ~/.kodo/teams/{name}.json."""
    path = _teams_dir() / f"{name}.json"
    path.write_text(json.dumps(config, indent=2) + "\n")
    print(f"Saved to {path}")
    return path


def _cmd_teams_add(name: str) -> None:
    """Interactive wizard to create a new team."""
    path = _teams_dir() / f"{name}.json"
    if path.exists():
        print(f"Team {name!r} already exists at {path}")
        print(f"Use 'kodo teams edit {name}' to modify it.")
        sys.exit(1)

    print(f"Creating team: {name}\n")

    description = questionary.text("Team description:").ask()
    if description is None:
        print("Cancelled.")
        sys.exit(1)

    max_exchanges = questionary.text("Max exchanges:", default="20").ask()
    if max_exchanges is None:
        print("Cancelled.")
        sys.exit(1)

    max_cycles = questionary.text("Max cycles:", default="1").ask()
    if max_cycles is None:
        print("Cancelled.")
        sys.exit(1)

    orch_prompt = questionary.text(
        "Orchestrator prompt (Enter to use default):", default=""
    ).ask()
    if orch_prompt is None:
        print("Cancelled.")
        sys.exit(1)

    agents: dict = {}
    while True:
        print(f"\n--- Add agent ({len(agents)} so far) ---")
        agent_key = questionary.text("Agent key name (empty to finish):").ask()
        if agent_key is None:
            print("Cancelled.")
            sys.exit(1)
        if not agent_key.strip():
            if not agents:
                print("A team needs at least one agent.")
                continue
            break
        agent_key = agent_key.strip()
        if agent_key in agents:
            print(f"Agent {agent_key!r} already exists. Pick a different name.")
            continue
        agents[agent_key] = _ask_agent_fields()

    # Assign verifiers
    agent_keys = list(agents.keys())
    verifiers: dict = {"testers": [], "browser_testers": [], "reviewers": []}

    if len(agent_keys) > 1:
        print("\n--- Verifier assignment ---")
        testers = questionary.checkbox(
            "Select testers (non-browser):", choices=agent_keys
        ).ask()
        if testers is not None:
            verifiers["testers"] = testers

        browser_testers = questionary.checkbox(
            "Select browser testers:", choices=agent_keys
        ).ask()
        if browser_testers is not None:
            verifiers["browser_testers"] = browser_testers

        reviewers = questionary.checkbox(
            "Select reviewers (architects):", choices=agent_keys
        ).ask()
        if reviewers is not None:
            verifiers["reviewers"] = reviewers

    config = {
        "name": name,
        "description": description,
        "max_exchanges": int(max_exchanges),
        "max_cycles": int(max_cycles),
        "verifiers": verifiers,
        "agents": agents,
    }
    if orch_prompt.strip():
        config["orchestrator_prompt"] = orch_prompt

    _save_team(name, config)


def _cmd_teams_edit(name: str) -> None:
    """Interactive editor for an existing team."""
    from kodo.team_config import list_available_teams

    # Find the team
    config = None
    source = None
    for tname, tsource, tcfg in list_available_teams():
        if tname == name:
            config = tcfg
            source = tsource
            break

    if config is None:
        print(f"Team {name!r} not found.")
        print("Available teams:")
        for tname, tsource, _ in list_available_teams():
            print(f"  {tname} ({tsource})")
        sys.exit(1)

    if source == "built-in":
        print(f"Copying built-in team {name!r} to user directory for editing.")

    agents = config.get("agents", {})
    verifiers = config.get("verifiers", {"testers": [], "browser_testers": [], "reviewers": []})

    while True:
        # Show current state
        print(f"\nTeam: {name}")
        print(f"  Description: {config.get('description', '')}")
        print(f"  Max exchanges: {config.get('max_exchanges', '?')}")
        print(f"  Max cycles: {config.get('max_cycles', '?')}")
        orch = config.get("orchestrator_prompt", "")
        if orch:
            snippet = orch[:80].replace("\n", " ")
            print(f"  Orchestrator prompt: {snippet}...")
        else:
            print("  Orchestrator prompt: (default)")
        print(f"  Agents ({len(agents)}):")
        for akey, acfg in agents.items():
            print(f"    {akey}: {acfg.get('backend', '?')} / {acfg.get('model', '?')}")
        print()

        actions = [
            "Add agent",
            "Edit agent",
            "Remove agent",
            "Edit team settings",
            "Edit verifiers",
            "Save & exit",
        ]
        action = questionary.select("Action:", choices=actions).ask()
        if action is None:
            print("Cancelled (changes not saved).")
            return

        if action == "Add agent":
            agent_key = questionary.text("Agent key name:").ask()
            if agent_key and agent_key.strip():
                agent_key = agent_key.strip()
                if agent_key in agents:
                    print(f"Agent {agent_key!r} already exists.")
                else:
                    agents[agent_key] = _ask_agent_fields()

        elif action == "Edit agent":
            if not agents:
                print("No agents to edit.")
                continue
            agent_key = questionary.select(
                "Which agent?", choices=list(agents.keys())
            ).ask()
            if agent_key:
                print(f"\nEditing {agent_key} (Enter to keep current value)")
                agents[agent_key] = _ask_agent_fields(defaults=agents[agent_key])

        elif action == "Remove agent":
            if not agents:
                print("No agents to remove.")
                continue
            agent_key = questionary.select(
                "Remove which agent?", choices=list(agents.keys())
            ).ask()
            if agent_key:
                confirm = questionary.confirm(f"Remove {agent_key}?", default=False).ask()
                if confirm:
                    del agents[agent_key]
                    # Clean from verifiers
                    for role in verifiers:
                        if agent_key in verifiers[role]:
                            verifiers[role].remove(agent_key)

        elif action == "Edit team settings":
            desc = questionary.text(
                "Description:", default=config.get("description", "")
            ).ask()
            if desc is not None:
                config["description"] = desc

            exc = questionary.text(
                "Max exchanges:",
                default=str(config.get("max_exchanges", 20)),
            ).ask()
            if exc is not None:
                config["max_exchanges"] = int(exc)

            cyc = questionary.text(
                "Max cycles:",
                default=str(config.get("max_cycles", 1)),
            ).ask()
            if cyc is not None:
                config["max_cycles"] = int(cyc)

            orch_prompt = questionary.text(
                "Orchestrator prompt (Enter to keep default):",
                default=config.get("orchestrator_prompt", ""),
            ).ask()
            if orch_prompt is not None:
                if orch_prompt.strip():
                    config["orchestrator_prompt"] = orch_prompt
                else:
                    config.pop("orchestrator_prompt", None)

        elif action == "Edit verifiers":
            agent_keys = list(agents.keys())
            if not agent_keys:
                print("No agents to assign as verifiers.")
                continue

            testers = questionary.checkbox(
                "Testers (non-browser):",
                choices=agent_keys,
                default=[k for k in verifiers.get("testers", []) if k in agent_keys],
            ).ask()
            if testers is not None:
                verifiers["testers"] = testers

            browser_testers = questionary.checkbox(
                "Browser testers:",
                choices=agent_keys,
                default=[k for k in verifiers.get("browser_testers", []) if k in agent_keys],
            ).ask()
            if browser_testers is not None:
                verifiers["browser_testers"] = browser_testers

            reviewers = questionary.checkbox(
                "Reviewers (architects):",
                choices=agent_keys,
                default=[k for k in verifiers.get("reviewers", []) if k in agent_keys],
            ).ask()
            if reviewers is not None:
                verifiers["reviewers"] = reviewers

        elif action == "Save & exit":
            config["name"] = name
            config["agents"] = agents
            config["verifiers"] = verifiers
            _save_team(name, config)
            return


def _main_inner() -> None:
    # Handle subcommands before argparse
    if len(sys.argv) > 1 and sys.argv[1] == "runs":
        _cmd_runs()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "backends":
        _cmd_backends()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "teams":
        _cmd_teams()
        return

    parser = argparse.ArgumentParser(
        description="kodo — autonomous multi-agent coding",
        epilog="subcommands:\n  kodo runs [PROJECT_DIR]  List all known runs\n  kodo backends            List available backends and API keys\n  kodo teams               List, add, or edit team configurations",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"kodo {__version__}")
    parser.add_argument(
        "--resume",
        nargs="?",
        const="__latest__",
        default=None,
        metavar="RUN_ID",
        help="Resume an interrupted run. No value = latest incomplete run.",
    )

    # Non-interactive goal input
    goal_group = parser.add_mutually_exclusive_group()
    goal_group.add_argument(
        "--goal",
        type=str,
        default=None,
        help="Goal text (inline). Enables non-interactive mode.",
    )
    goal_group.add_argument(
        "--goal-file",
        type=str,
        default=None,
        help="Path to a file containing the goal text. Enables non-interactive mode.",
    )
    goal_group.add_argument(
        "--improve",
        action="store_true",
        default=False,
        help="Analyze codebase, auto-fix safe issues, and produce an improvement report.",
    )

    parser.add_argument(
        "--improve-type",
        type=str,
        default="auto",
        choices=["auto", "app", "library"],
        help="Project type for --improve (default: auto-detect).",
    )

    # Non-interactive config flags
    parser.add_argument(
        "--mode",
        type=str,
        default=None,
        choices=["saga", "mission", "quick"],
        help="Run mode (default: saga).",
    )
    parser.add_argument(
        "--exchanges", type=int, default=None, help="Max exchanges per cycle."
    )
    parser.add_argument("--cycles", type=int, default=None, help="Max cycles.")
    parser.add_argument(
        "--orchestrator",
        type=str,
        default=None,
        choices=["api", "claude-code"],
        help="Orchestrator backend.",
    )
    parser.add_argument(
        "--orchestrator-model",
        type=str,
        default=None,
        choices=["opus", "sonnet", "gemini-pro", "gemini-flash"],
        help="Model for the orchestrator LLM.",
    )
    parser.add_argument(
        "--skip-intake",
        action="store_true",
        default=False,
        help="Skip intake interview, use goal as-is",
    )
    parser.add_argument(
        "--auto-refine",
        action="store_true",
        default=False,
        help="Auto-refine goal before implementation (surfaces implicit constraints). "
        "Useful for unattended/overnight runs when no human is available for intake.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output structured JSON to stdout. Implies --yes.",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        default=False,
        help="Skip all confirmation prompts.",
    )
    parser.add_argument(
        "--no-auto-commit",
        action="store_true",
        default=False,
        help="Disable auto-commit after completed stages/goals.",
    )

    parser.add_argument(
        "project_dir",
        nargs="?",
        default=".",
        help="Project directory (default: current dir)",
    )
    args = parser.parse_args()

    # ── Early input validation ───────────────────────────────────────────
    if args.goal is not None and not args.goal.strip():
        _fail("--goal must not be empty or whitespace-only.")
    if args.exchanges is not None and args.exchanges <= 0:
        _fail("--exchanges must be a positive integer.")
    if args.cycles is not None and args.cycles <= 0:
        _fail("--cycles must be a positive integer.")

    # --json and --auto-refine imply --yes
    if args.json or args.auto_refine:
        args.yes = True

    # --improve forces non-interactive, skip-intake, yes, and defaults mode to saga
    if args.improve:
        args.skip_intake = True
        args.yes = True
        if args.mode is None:
            args.mode = "saga"

    non_interactive = (
        args.goal is not None or args.goal_file is not None or args.improve
    )
    skip_prompts = non_interactive or args.yes

    # In JSON mode, redirect prints to stderr so stdout stays clean for JSON
    global _original_stdout
    _original_stdout = None
    if args.json:
        _original_stdout = sys.stdout
        sys.stdout = sys.stderr
        os.environ["KODO_NO_VIEWER"] = "1"

    if not args.json:
        _print_banner()

    if non_interactive and args.resume is not None:
        _fail("--resume cannot be used with --goal/--goal-file/--improve")

    project_dir = Path(args.project_dir)
    try:
        project_dir.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        _fail(f"Cannot create project directory (permission denied): {project_dir}")
    project_dir = project_dir.resolve()
    if not args.json:
        print(f"  Project: {project_dir}")

    # Handle --resume
    if args.resume is not None:
        if args.resume == "__latest__":
            runs = log.find_incomplete_runs(project_dir)
            if not runs:
                _fail("No incomplete runs found.")
            state = runs[0]
        else:
            run_log = log._runs_root() / args.resume / "run.jsonl"
            if run_log.exists():
                log_file = run_log
            else:
                _fail(f"Run not found: {args.resume}")
            state = log.parse_run(log_file)
            if state is None:
                _fail(f"Could not parse run from {log_file}")

        print(f"  Goal: {state.goal[:80]}{'...' if len(state.goal) > 80 else ''}")
        print(f"  Cycles completed: {state.completed_cycles}/{state.max_cycles}")
        if not skip_prompts:
            confirm = input("\nResume this run? [Y/n] ").strip().lower()
            if confirm in ("n", "no"):
                print("Aborted.")
                sys.exit(0)

        run_dir = RunDir.from_log_file(state.log_file, project_dir)
        result = launch_resume(run_dir, state)
        _emit_json_and_exit(args, result)
        return

    # 1. Get goal
    if non_interactive:
        if args.improve:
            goal_text = None  # constructed after run_dir is created
        elif args.goal is not None:
            goal_text = args.goal.strip()
            if not goal_text:
                _fail("Goal text is empty.")
        elif args.goal_file is not None:
            goal_path = Path(args.goal_file).resolve()
            if not goal_path.is_file():
                _fail(
                    f"Goal file not found or not a file: {goal_path}"
                    if goal_path.exists()
                    else f"Goal file not found: {goal_path}"
                )
            try:
                goal_text = goal_path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                _fail(f"Cannot read goal file: {goal_path} — {exc}")
            if not goal_text:
                _fail("Goal file is empty.")
        else:
            _fail("No goal provided. Use --goal, --goal-file, or --improve.")
    else:
        goal_file = next(
            (p for p in project_dir.iterdir() if p.name.lower() == "goal.md"), None
        )
        if goal_file is not None:
            try:
                goal_text = goal_file.read_text(encoding="utf-8").strip()
            except OSError:
                goal_text = get_goal()
            else:
                print(f"\nFound existing goal in {goal_file}:")
                print("-" * 40)
                print(goal_text[:500])
                if len(goal_text) > 500:
                    print("...")
                print("-" * 40)
                use_existing = input("Use this goal? [Y/n] ").strip().lower()
                if use_existing in ("n", "no"):
                    goal_text = get_goal()
        else:
            goal_text = get_goal()

    # 2. Select parameters
    if non_interactive:
        params = _build_params_from_flags(args, project_dir)
    else:
        params = _load_or_select_params(project_dir)

    # 3. Create run directory
    run_dir = RunDir.create(project_dir)

    # Construct --improve goal and staged plan now that we have a run_dir
    # 4. Intake / goal plan
    plan: GoalPlan | None = None

    if args.improve:
        report_path = run_dir.root / "improve-report.md"
        goal_text = _IMPROVE_GOAL.format(report_path=report_path)
        improve_type = getattr(args, "improve_type", "auto")
        if improve_type == "auto":
            project_type = _detect_project_type(project_dir)
        else:
            project_type = ProjectType(improve_type)
        if not args.json:
            print(f"  Improve type: {project_type.value}")
        plan = _build_improve_plan(
            str(report_path),
            project_type=project_type,
            project_dir=project_dir,
        )
    elif non_interactive:
        existing_plan = _load_goal_plan(run_dir)
        if existing_plan:
            plan = existing_plan
            print(f"Using existing goal plan ({len(plan.stages)} stages)")
        elif args.auto_refine:
            backend = (
                "claude"
                if has_claude()
                else ("cursor" if has_cursor() else "gemini-cli")
            )
            refined = run_intake_auto(backend, run_dir, goal_text)
            if refined:
                goal_text = refined
        elif not args.skip_intake:
            plan = run_intake_noninteractive(run_dir, goal_text)
    else:
        # Check for existing goal plan first
        existing_plan = _load_goal_plan(run_dir)
        if existing_plan:
            print(f"\nFound existing goal plan ({len(existing_plan.stages)} stages):")
            print("-" * 40)
            for s in existing_plan.stages:
                print(f"  {s.index}. {s.name}")
                if s.acceptance_criteria:
                    print(f"     Done when: {s.acceptance_criteria[:100]}")
            print("-" * 40)
            use_plan = input("Use this goal plan? [Y/n] ").strip().lower()
            if not use_plan or use_plan == "y":
                plan = existing_plan

        if plan is None and not args.skip_intake:
            if args.auto_refine:
                backend = (
                    "claude"
                    if has_claude()
                    else ("cursor" if has_cursor() else "gemini-cli")
                )
                refined = run_intake_auto(backend, run_dir, goal_text)
                if refined:
                    goal_text = refined
            else:
                intake_result = _offer_intake(run_dir, goal_text)
                if isinstance(intake_result, GoalPlan):
                    plan = intake_result
                elif isinstance(intake_result, str):
                    goal_text = intake_result

    # 5. Summary and confirm
    if not args.json:
        mode = get_mode(params["mode"])
        print("\n" + "=" * 60)
        print("  READY TO LAUNCH")
        print("=" * 60)
        print(f"  Project:      {project_dir}")
        print(f"  Goal:         {goal_text[:80]}{'...' if len(goal_text) > 80 else ''}")
        if plan:
            print(f"  Stages:       {len(plan.stages)}")
            for s in plan.stages:
                print(f"                  {s.index}. {s.name}")
        print(f"  Mode:         {mode.name} — {mode.description}")
        print(
            f"  Orchestrator: {params['orchestrator']} ({params['orchestrator_model']})"
        )
        print(
            f"  Exchanges:    {params['max_exchanges']}/cycle, {params['max_cycles']} cycles"
        )
        print()

    if not skip_prompts:
        print("  WARNING: Agents run with full permissions (bypass mode).")
        print("  They will create, modify, and delete files — primarily in")
        print(f"  {project_dir}")
        print("  but they CAN access any file on your system (install deps,")
        print("  edit configs, etc). Make sure you have a git commit or backup.")
        print()
        confirm = input("Proceed? [Y/n] ").strip().lower()
        if confirm in ("n", "no"):
            print("Aborted.")
            sys.exit(0)

    # 6. Launch
    result = launch_run(run_dir, goal_text, params, plan=plan, json_mode=args.json)

    # 7. --improve post-run: report summary
    if args.improve:
        report_path = run_dir.root / "improve-report.md"
        if report_path.exists():
            try:
                report_content = report_path.read_text(encoding="utf-8")
            except OSError:
                report_content = ""
            auto_fixed = len(
                re.findall(
                    r"^- .+$",
                    _extract_section(report_content, "Auto-fixed"),
                    re.MULTILINE,
                )
            )
            needs_decision = len(
                re.findall(
                    r"^- .+$",
                    _extract_section(report_content, "Needs decision"),
                    re.MULTILINE,
                )
            )
            print(f"\n{'=' * 50}")
            print(f"Improve report: {report_path}")
            print(f"  Auto-fixed:     {auto_fixed}")
            print(f"  Needs decision: {needs_decision}")

            if args.json:
                _emit_json_and_exit(args, result, improve_report=report_content)
                return
        _emit_json_and_exit(args, result)
    else:
        _emit_json_and_exit(args, result)


if __name__ == "__main__":
    main()
