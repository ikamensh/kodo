"""Test mode prompts — tool forge, user story mapping, exploratory testing."""

# ---------------------------------------------------------------------------
# Report format
# ---------------------------------------------------------------------------

TEST_REPORT_FORMAT = """\
```markdown
# Test Report

## Summary
- **User stories tested:** <tested>/<total>
- **Findings:** <count> (<critical>/<medium>/<low>)
- **Bugs confirmed:** <count>
- **Usability gaps:** <count>
- **Regression tests written:** <count>
- **Tools built:** <list>

## Testing Tools Built
- <tool name> — <what it does, how to run it>

## User Stories Tested
| # | Story | Status | Findings | Notes |
|---|-------|--------|----------|-------|
| US1 | <description> | pass/fail/partial | F1,F2 | |
| US2 | <description> | blocked | — | needs <tool/infra> |

## Critical Findings
- **F<n>:** <title>
  - **Story:** US<n>
  - **What:** <description>
  - **Repro:** <exact steps>
  - **Impact:** <what breaks for the user>
  - **Regression test:** <test file:name, or "none — requires <reason>">

## Integration & Workflow Findings
- **F<n>:** <title>
  - **Workflow tested:** <scenario>
  - **Expected vs actual:** <comparison>

## Usability Gaps
- **F<n>:** <title>
  - **Scenario:** <what the user tried>
  - **Problem:** <what went wrong>
  - **Suggestion:** <improvement>

## Regression Tests & Fixes
- **F<n>:** <file>:<test_name> — test fails before fix, passes after
  - Fix: <file>:<line> — <what was changed>

## Blocked Stories
- US<n>: <story> — needs <tool/capability>

## Untestable Gaps
- <description> — <why>
```"""

# ---------------------------------------------------------------------------
# Goal text
# ---------------------------------------------------------------------------

TEST_GOAL = """\
Test this codebase like a real user would. Build whatever tools you need \
to interact with it properly, map the key user stories, and work through them.

The deliverable is findings with repro steps — not coverage numbers. \
If you find bugs, write a regression test that fails, then fix the code \
to make it pass.

If you need tools you can't build (Docker, VPS, browser), say so explicitly.

Report at `{report_path}`.

{report_format}
"""

# ---------------------------------------------------------------------------
# User story file format (persisted across runs)
# ---------------------------------------------------------------------------

USER_STORY_FILE = ".kodo/test-stories.md"

USER_STORY_FORMAT = """\
# User Stories for Testing

Tracked across `kodo test` runs.

| # | Story | Last tested | Status | Findings | Notes |
|---|-------|-------------|--------|----------|-------|
"""

# ---------------------------------------------------------------------------
# Guidance
# ---------------------------------------------------------------------------

TEST_TIME_GUIDANCE = """\
Be thorough, not fast. Spend time building proper tooling and testing \
realistic workflows. Each finding needs exact reproduction steps."""

TOOL_FORGE_GUIDANCE = """\
Before testing anything, figure out how a real user interacts with this \
software and build the tools you need to do the same.

For a CLI: a wrapper script that runs commands, captures output, checks \
exit codes. For a library: a small consumer project that exercises the API. \
For a game/UI: screenshot capture and visual inspection. For any project: \
a clean-room install script.

If you need something you can't build yourself — Docker, a VPS, browser \
automation, GPU — say so in the Blocked Stories section. Be specific about \
what you need and why."""

USER_STORY_MAPPING_GUIDANCE = """\
Map the ways a user interacts with this software across the full lifecycle: \
discovery, install, first use, core workflows, configuration, error recovery, \
edge cases, upgrades, integration with other tools.

For each story, decide: can you test it now, or is it blocked on tooling \
you don't have?

Write stories to `{story_file}`:
{story_format}"""

TEST_EXPLORATION_GUIDANCE = """\
Use the tools you built. Work through the user stories systematically. \
Try to break things — invalid inputs, missing files, interrupted workflows, \
concurrent usage. Test what happens at module boundaries with real components.

Document findings, don't fix them yet. Write clear repro steps. \
Regression tests and fixes come last."""

# ---------------------------------------------------------------------------
# Findings format
# ---------------------------------------------------------------------------

TEST_FINDING_FORMAT = """\
### F<n>: <title>
- **Story:** US<n>
- **Severity:** critical | medium | low
- **Category:** bug | integration-gap | usability | edge-case | environment
- **Tested with:** <tool used>
- **Repro steps:**
  1. <step>
  2. <step>
  3. <what happens vs what should happen>
- **Root cause:** <if known>
- **Suggested fix:** <if obvious>"""

# ---------------------------------------------------------------------------
# Discovery prompt
# ---------------------------------------------------------------------------

DISCOVERY_PROMPT = """\
You're testing a software project to find real bugs and usability gaps.

Look at the project — what does it do, how do users interact with it? \
Then design a plan to test it the way a user would, not at the unit test level.

## Tasks
1. Read the project: README, source structure, test setup, config files
2. Understand: what does this do for users? CLI? API? UI? Library?
3. Figure out what tools you'd need to test it realistically
4. Write a GoalPlan JSON to `{output_path}`

{methodologies}

## Environment
{environment}

## Plan structure (4-6 stages)

**Stage 1: Tool Forge & User Story Mapping** (`persist_changes` true)

{tool_forge_guidance}

{story_mapping_guidance}

Write user stories to `{story_file}`.
Write recon notes to `{run_dir}/test-recon.md`.

**Middle stages (1-4, parallel where independent):**

Each stage tests a group of user stories using the tools from Stage 1. \
Work through stories, document findings with repro steps, update story \
status in `{story_file}`.

{finding_format}

**Last stage: Regression Tests, Fixes & Report** (`persist_changes` true)

For each confirmed bug:
1. Write a test that reproduces the bug — verify it **fails**
2. Fix the code
3. Verify the test now **passes**

This ensures the test actually catches the real issue, not just the current behavior.

Update story status. Run the full suite. Write report to `{report_path}`:

{report_format}

Commit tests and fixes separately:
- "test: add regression test for F<n> (kodo test)"
- "fix: <description> (kodo test)"

{time_guidance}

## Output format

Write valid JSON:

{{
  "context": "What the software does, how users interact, testing approach",
  "stages": [
    {{
      "index": 1,
      "name": "Short label",
      "description": "What this stage does",
      "acceptance_criteria": "Definition of done",
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
## Approaches

Build tools first, then use them to test. Prioritize what unit tests can't catch.

**Tool-first testing**: identify the interaction surface (CLI, API, UI, library), \
build a harness that simulates real usage. A CLI wrapper, a consumer project, \
a screenshot tool — whatever fits.

**User story-driven**: map the user lifecycle, test each story end-to-end, \
track which pass, fail, or are blocked.

**Exploratory**: follow the README, try the happy path, then try to break things. \
Wrong arguments, missing configs, interrupted operations, permission errors.

**Integration**: test module boundaries with real components. Real subprocess \
calls, real file operations, real config loading. Signal handling, cleanup.

**Environment**: install from scratch in a clean environment. Missing deps, \
wrong versions, implicit assumptions about PATH or working directory.

**Regression tests + fixes** (last step): for each confirmed bug, write a test \
that fails reproducing the issue, then fix the code so the test passes. \
Commit the test and fix separately. Use the project's existing test framework."""
