"""Improve mode prompts — code review, simplification, usability, architecture."""

IMPROVE_REPORT_FORMAT = """\
```markdown
# Improve Report

## Auto-fixed
- <file>:<line> — <description>

## Needs decision
- <file>:<line> — <description + proposed change + tradeoff>

## Skipped by triage
- <finding title> — <reason>
```"""

IMPROVE_GOAL = """\
Review this codebase for significant improvements. Focus on simplification, \
usability, and architecture — not on running tests or finding runtime bugs \
(use `kodo test` for that).

Look for things a senior developer joining the project would notice: \
unnecessary complexity, confusing interfaces, duplicated concepts, \
missing abstractions, poor defaults. Be ambitious — propose changes \
that meaningfully improve the experience of working with or using this software.

Report at `{report_path}`.

{report_format}

Commit auto-fixes: "chore: auto-fix issues found by kodo improve".
"""

IMPROVE_TIME_GUIDANCE = """\
Focus on high-impact findings. A single "this entire module could be \
replaced by X" is worth more than twenty lint fixes."""

TRIAGE_FINDINGS_FORMAT = """\
### F<n>: <title>
- **File:** <file>:<line>
- **Category:** simplification | usability | architecture | dead-code | security | performance
- **Impact:** <who benefits and how — users, contributors, or both>
- **Evidence:** <concrete proof: code snippet, example, or comparison>
- **Proposed change:** <what to do, with enough detail to act on>"""

TRIAGE_STAGE_DESCRIPTION = """\
Skeptically verify each finding. Read the actual code at the cited location. \
Default to `skip` — most findings don't survive scrutiny.

For each finding, ask:
- Is this actually a problem, or does it serve a purpose I'm missing?
- Would the proposed change make things genuinely better, or just different?
- Is the impact worth the churn?

Write `{triage_path}`:

### F<n>: <title>
- **Verdict:** fix | skip | needs-decision
- **Reason:** <1-2 sentences>"""


# ---------------------------------------------------------------------------
# Discovery prompt
# ---------------------------------------------------------------------------

DISCOVERY_PROMPT = """\
You're reviewing a software project to find significant improvements — \
simplifications, usability wins, architectural cleanup, and dead weight removal.

This is a code review, not a test run. You're reading the code critically \
as a senior developer joining the project. Think big: what would you change \
in your first week to make this codebase meaningfully better?

## Tasks
1. Read the project: README, source structure, config files, public API surface
2. Understand: what does this do? Who uses it? What's the interface \
(CLI, library API, web UI, service)?
3. Identify the most impactful improvements
4. Write a GoalPlan JSON to `{output_path}`

{methodologies}

## Environment
{environment}

## Plan structure (3-6 stages)

Stages that can run independently should share a `parallel_group` integer. \
Parallel stages don't modify code (`persist_changes` false). \
Each analysis stage writes findings to `{run_dir}/findings-<slug>.md`.

The plan must end with these two stages:

**Triage & Verify** (second-to-last):
{triage_description}

Reference all findings files from earlier stages.

**Fix & Report** (last):
Act only on `fix` and `needs-decision` from triage. Ignore `skip`.
Auto-fix safe issues, flag ambiguous ones.
Write report to `{report_path}`:

{report_format}

Commit auto-fixes: "chore: auto-fix issues found by kodo improve".

## Findings format

{findings_format}

{time_guidance}

## Output format

Write valid JSON:

{{
  "context": "Stack, key files, conventions, project type",
  "stages": [
    {{
      "index": 1,
      "name": "Short label",
      "description": "What this stage does",
      "acceptance_criteria": "Definition of done",
      "browser_testing": false,
      "parallel_group": null,
      "persist_changes": false
    }}
  ]
}}

Non-interactive — inspect the project and write the plan."""


METHODOLOGY_LIBRARY = """\
## Approaches

Think like a senior developer reviewing the project for the first time. \
What would you change to make it simpler, cleaner, and easier to use?

### Simplification
Look for code that could be simpler without losing functionality:
- Abstractions that don't pay for themselves (wrapper classes that just \
delegate, factory patterns with one product, config systems for three settings)
- Duplicated logic that should be one function
- Dead code paths, unused parameters, vestigial features
- Indirection that obscures rather than clarifies
- Code that reimplements something available in the standard library or \
an existing dependency

### Usability
Review the public interface — whatever users or consumers interact with:
- **For CLIs**: redundant flags, confusing flag names, missing defaults, \
unclear help text, flags that could be inferred, inconsistent naming
- **For libraries**: confusing API naming, too many required parameters, \
missing convenience methods, poor error messages, implicit ordering requirements
- **For services**: inconsistent endpoints, missing validation feedback, \
confusing error responses
- **For all**: is the README accurate? Can someone start using this from \
the docs alone? Are error messages actionable?

### Architecture
Step back and look at the structure:
- Module boundaries: do they match the domain, or are they historical accidents?
- Dependency direction: do high-level modules depend on low-level details?
- Circular dependencies, god modules, scattered responsibilities
- Configuration: is it centralized or spread across the codebase?

### Dead weight
Find things that should be removed:
- Unused dependencies in the manifest
- Unreachable code, commented-out code, TODO comments older than 6 months
- Test infrastructure that tests nothing useful (mocked-to-death tests, \
tests that assert implementation details)
- Documentation that contradicts the code

### Security (lightweight)
Scan system boundaries — not a full security audit, just obvious issues:
- Hardcoded secrets, credentials in source
- SQL injection, command injection, path traversal at input boundaries
- Missing input validation on external data

### Performance (lightweight)
Only flag things that are clearly wasteful:
- Quadratic algorithms on potentially large inputs
- Resource leaks (unclosed files, connections, clients)
- N+1 query patterns
- Unnecessary I/O in hot paths"""
