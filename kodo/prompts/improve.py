"""Improve mode prompts — discovery, triage, findings, and methodology library."""

import shutil

IMPROVE_REPORT_FORMAT = """\
```markdown
# Improve Report

## Auto-fixed
- <file>:<line> — <description>

## Needs decision
- <file>:<line> — <description + suggested fix>

## Skipped by triage
- <finding title> — <reason>
```"""

IMPROVE_GOAL = """\
Test and improve this codebase. Report at `{report_path}`.

{report_format}

Commit auto-fixes: "chore: auto-fix issues found by kodo improve".
"""

IMPROVE_TIME_GUIDANCE = """\
**Be fast.** Mock or stub external calls (APIs, databases, network). \
Use targeted tests, not exhaustive sweeps. Abort anything over 30 seconds. \
In-memory fixtures, lightweight fakes, skip heavy init."""

TRIAGE_FINDINGS_FORMAT = """\
Format each finding as:

### F<n>: <title>
- **File:** <file>:<line>
- **Severity:** bug | hardening | style | performance
- **Evidence:** <proof: test output, error message, or code path — not just assertions>
- **Proposed fix:** <concrete change>"""

TRIAGE_STAGE_DESCRIPTION = """\
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

DISCOVERY_PROMPT = """\
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
# Methodology library — reference material for discovery stage
# ---------------------------------------------------------------------------

def detect_docker() -> bool:
    """Check whether docker is available on the host."""
    return shutil.which("docker") is not None


METHODOLOGY_LIBRARY = """\
## Recommended Methodologies

These are starting points — adapt, combine, or invent approaches that fit \
the project. You are not limited to this list.

### Test Tool Forge
- **Test Infrastructure Audit**: Inventory the project's existing test tools — \
fixtures, helpers, conftest plugins, test scripts, Docker test setups, \
CI test jobs. Map what categories of bugs each tool can catch.
- **Gap Analysis**: Identify the single highest-impact testing gap — a class of \
bugs or failure modes that existing tools don't cover well. Consider: \
integration contract violations, state machine invariants, configuration \
drift, cross-module interaction bugs, data flow corruption, regression traps \
in recently-changed code.
- **Build or Enhance**: For mature projects with good test infrastructure, \
prefer extending an existing tool to cover the gap (e.g. adding new test \
cases to an existing module, enhancing a fixture, expanding a script's \
scope). For projects with sparse testing, create a new reusable tool \
(test module, script, conftest plugin, or fixture). Either way, the result \
must be runnable standalone and produce clear pass/fail output.
- **Immediate Application**: Run the new or enhanced tool against the codebase \
and report any bugs discovered. These are findings that were previously \
invisible or untested.

### Static Analysis & Baseline
- **Lint & Type Check**: Run the project's configured linters and type checkers \
(mypy, pyright, eslint, tsc --noEmit, clippy, golangci-lint, etc.)
- **Dependency Audit**: Check for known vulnerabilities \
(pip-audit, npm audit, cargo audit, govulncheck, bundler-audit)
- **Dead Code / Unused Deps**: Find unused imports, unreachable code, \
dependencies in manifests that nothing imports, use dedicated tools like vulture

### Functional Testing
- **Happy Path Integration**: Run 3-5 core user scenarios end-to-end with \
realistic inputs. Mock or stub external services.
- **Adversarial / Edge Cases**: Empty inputs, None/null, zero, huge values, \
unicode, invalid configs, missing dependencies, wrong permissions
- **Property-Based Testing**: Generate random inputs to find invariant \
violations. Tools: Hypothesis (Python), fast-check (JS/TS), proptest (Rust), \
gopter (Go), jqwik (Java/Kotlin). Write properties for pure functions and \
data transformations.
- **Concurrency Testing**: Race conditions, deadlocks, thread safety. Relevant \
when the project uses async, threading, multiprocessing, or concurrent data \
structures.
- **Recent-Change Focus**: Use `git diff main...HEAD` or recent commits to \
identify recently changed code and concentrate testing effort there.

### Library / SDK-Specific
- **API Surface Audit**: Naming consistency, type annotations, docstring \
accuracy vs actual signatures, error/exception types
- **Consumer Project Testing**: Install as a dependency in a temp dir, exercise \
from a consumer's perspective. Can a developer start from the README alone?
- **API Misuse Testing**: Wrong types, missing args, wrong call order, edge \
values. Grade each error message: does it say what went wrong and how to fix it?

### Security
- **Input Validation**: SQL injection, path traversal, command injection, XSS \
at system boundaries (user input, external APIs, file uploads)
- **Secret Scanning**: Hardcoded credentials, API keys, tokens in source or \
config files
- **Permission / Auth Boundaries**: Verify access controls, privilege \
escalation paths (relevant for web apps, APIs with auth)

### Performance & Resources
- **Resource Leak Detection**: Unclosed files, DB connections, HTTP clients \
without context managers / defer / try-with-resources
- **Hot Path Profiling**: N+1 queries, unbounded loops, quadratic algorithms \
in hot paths

### Isolated Environment Testing
- **Docker-Based Testing**: Build and run the project inside a container to \
test in a clean environment — catches missing dependencies, implicit host \
assumptions, and install/build issues. Especially useful for projects with \
a Dockerfile or docker-compose setup.

### Architecture & Simplification
- **Unnecessary Complexity**: Code that could be simpler without losing \
functionality. Abstractions that don't pay for themselves, indirection \
that obscures rather than clarifies, dead code paths.
- **Structural Issues**: Poor module boundaries, circular dependencies, \
responsibilities in the wrong place.

### Infrastructure
- **Dockerfile Review**: Multi-stage builds, security (running as root, \
secrets in layers), layer optimization
- **CI/CD Config Audit**: Pipeline correctness, missing steps, caching, \
flaky test handling
"""
