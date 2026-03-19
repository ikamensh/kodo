"""Test mode prompts — user-experience-first testing with edge case and adversarial probing."""

# ---------------------------------------------------------------------------
# Report format
# ---------------------------------------------------------------------------

TEST_REPORT_FORMAT = """\
```markdown
# Test Report

## Summary
- **Features tested:** <count>
- **Findings:** <count>
- **Regression tests written:** <count>

## Feature Coverage
| Feature / Workflow | Status | Findings | Notes |
|--------------------|--------|----------|-------|
| <feature> | pass / fail / partial | F1,F2 | <what wasn't tested and why> |

## Findings
- **F<n>:** <title>
  - **Workflow:** <which feature or user workflow>
  - **Category:** crash | data-loss | silent-wrong | hang | race | leak | misleading-output | install-failure | usability
  - **Repro steps:**
    1. <step>
    2. <what happens vs what should happen>
  - **Root cause:** <if known>
  - **Severity:** critical | medium | low

## Regression Tests & Fixes
- **F<n>:** <file>:<test_name> — test fails before fix, passes after

## Self-Critique
- What features weren't tested? What assumptions went unchallenged?
- If zero findings: what gives you confidence this is actually correct?

## Blocked Workflows
- <workflow> — <why it couldn't be tested>
```"""

# ---------------------------------------------------------------------------
# Goal text
# ---------------------------------------------------------------------------

TEST_GOAL = """\
Test this software the way a real user would — start to finish.

Install it, try every feature, exercise realistic workflows, then probe \
edge cases. The goal is to find bugs users would actually hit.

For confirmed bugs, write a regression test that fails, then fix the code.

Report at `{report_path}`.

{report_format}
"""

# ---------------------------------------------------------------------------
# Feature coverage file format (persisted across runs)
# ---------------------------------------------------------------------------

FEATURE_COVERAGE_FILE = ".kodo/test-coverage.md"

FEATURE_COVERAGE_FORMAT = """\
# Feature Coverage

Tracked across `kodo test` runs.

| Feature / Workflow | Last tested | Status | Findings |
|--------------------|-------------|--------|----------|
"""


# ---------------------------------------------------------------------------
# Guidance
# ---------------------------------------------------------------------------

TEST_TIME_GUIDANCE = """\
Time budget: 15% setup and discovery, 60% feature walkthroughs and testing, \
15% edge cases and adversarial probing, 10% triage and regression tests."""

TOOL_FORGE_GUIDANCE = """\
Build whatever you need to actually use the software: install scripts, CLI \
wrappers, test fixtures, sample data. The goal is to interact with it like \
a real user.

If you need something you can't build (Docker, browser, GPU), say so \
in the Blocked Workflows section."""

FEATURE_MAPPING_GUIDANCE = """\
Map all user-facing features and workflows:

Read the README, run --help, check examples and docs. What can a user \
actually do with this software? What are the documented workflows?

Write feature coverage to `{coverage_file}`:
{coverage_format}"""


TEST_EXPLORATION_GUIDANCE = """\
For each feature, test the happy path first, then probe edges: empty inputs, \
huge inputs, invalid types, missing files, concurrent usage, interruption \
mid-operation. Prioritize by what users are most likely to hit."""

# ---------------------------------------------------------------------------
# Findings format
# ---------------------------------------------------------------------------

TEST_FINDING_FORMAT = """\
### F<n>: <title>
- **Workflow:** <feature or user workflow>
- **Severity:** critical | medium | low
- **Category:** crash | data-loss | silent-wrong | hang | race | leak | misleading-output | install-failure | usability
- **Repro steps:**
  1. <step>
  2. <what happens vs what should happen>
- **Root cause:** <if known>"""

# ---------------------------------------------------------------------------
# Discovery prompt
# ---------------------------------------------------------------------------

DISCOVERY_PROMPT = """\
You're testing a software project the way a real user would — start to finish.

Your job: install it, exercise every feature, try realistic workflows, then \
probe edge cases. Find bugs users would actually hit.

## Tasks
1. Read the project: README, source structure, tests, config, examples
2. Map all user-facing features and workflows
3. Figure out what tools you need to actually use the software
4. Write a GoalPlan JSON to `{output_path}`

{methodologies}

## Environment
{environment}

## Plan structure (4-6 stages)

**Stage 1: Setup & Discovery** (`persist_changes` true)

{tool_forge_guidance}

{feature_mapping_guidance}

Write feature map to `{coverage_file}`.
Write discovery notes to `{run_dir}/test-recon.md`.

{time_guidance}

**Middle stages (1-4, parallel where independent):**

Each stage tests a group of features or workflows using tools from Stage 1. \
Start with happy paths, then push into edge cases. \
Every stage must produce findings or explain what was tested and why it passed.

Update feature coverage in `{coverage_file}`.

{finding_format}

**Last stage: Triage & Regression Tests** (`persist_changes` true)

For each confirmed bug:
1. Write a test that reproduces it — verify it **fails**
2. Fix the code, verify the test **passes**

Run the full suite. Write report to `{report_path}`:

{report_format}

Commit tests and fixes separately:
- "test: add regression test for F<n> (kodo test)"
- "fix: <description> (kodo test)"

## Output format

Write valid JSON:

{{
  "context": "Features mapped, what's most likely to break for users",
  "stages": [
    {{
      "index": 1,
      "name": "Short label",
      "description": "What this stage tests",
      "acceptance_criteria": "Findings or explanation of what was tested",
      "browser_testing": false,
      "parallel_group": null,
      "persist_changes": true
    }}
  ]
}}

Non-interactive — inspect the project and write the plan."""


# ---------------------------------------------------------------------------
# Methodology library
# ---------------------------------------------------------------------------

METHODOLOGY_LIBRARY = """\
## Testing Approach

Test like a user, then probe like an engineer. In priority order:

**Setup & install** — follow the documented install steps. Does it actually \
work? Are dependencies correct? Do the examples run?

**Feature walkthroughs** — exercise every user-facing feature end-to-end. \
Follow documented workflows. Does the output match what's promised?

**Edge cases & boundaries** — empty, massive, and malformed inputs. Missing \
files, bad configs, unexpected types. What happens at the limits?

**Error handling** — trigger every error path. Are error messages helpful? \
Does the software recover gracefully or corrupt state?

**Concurrency & interruption** — race shared resources, interrupt mid-operation, \
restart. Data corruption? Deadlocks? Stale state?

Regression tests come last: reproduce the bug with a failing test, then fix."""
