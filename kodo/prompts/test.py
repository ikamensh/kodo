"""Test mode prompts — attack surface analysis, fault injection, breakage-oriented testing."""

# ---------------------------------------------------------------------------
# Report format
# ---------------------------------------------------------------------------

TEST_REPORT_FORMAT = """\
```markdown
# Fault Report

## Summary
- **Attack surfaces probed:** <count>
- **Findings:** <count>
- **Regression tests written:** <count>

## Attack Surface Coverage
| Surface | Attacks tried | Findings | Residual risk |
|---------|--------------|----------|---------------|
| <surface> | <count> | F1,F2 | <what wasn't tested and why> |

## Findings
- **F<n>:** <title>
  - **Surface:** <attack surface>
  - **Category:** crash | data-loss | silent-wrong | hang | race | leak | misleading-output
  - **Repro steps:**
    1. <step>
    2. <what happens vs what should happen>
  - **Root cause:** <if known>
  - **Severity:** critical | medium | low

## Regression Tests & Fixes
- **F<n>:** <file>:<test_name> — test fails before fix, passes after

## Self-Critique
- What did you skip? What assumptions went unchallenged?
- If zero findings: what gives you confidence this is actually correct?

## Unreachable Attack Surfaces
- <surface> — <why>
```"""

# ---------------------------------------------------------------------------
# Goal text
# ---------------------------------------------------------------------------

TEST_GOAL = """\
Find bugs. Not verify it works — break it.

Assume happy paths work. Hunt for crashes, data corruption, silent wrong \
answers, and hangs.

Zero findings means your testing failed, not that the software is perfect. \
If you find nothing, write a self-critique explaining what you tried.

For confirmed bugs, write a regression test that fails, then fix the code.

Report at `{report_path}`.

{report_format}
"""

# ---------------------------------------------------------------------------
# Attack surface file format (persisted across runs)
# ---------------------------------------------------------------------------

ATTACK_SURFACE_FILE = ".kodo/attack-surfaces.md"

ATTACK_SURFACE_FORMAT = """\
# Attack Surfaces

Tracked across `kodo test` runs.

| Surface | Attacks tried | Findings | Residual risk |
|---------|--------------|----------|---------------|
"""

# ---------------------------------------------------------------------------
# Guidance
# ---------------------------------------------------------------------------

TEST_TIME_GUIDANCE = """\
Time budget: 20% setup and recon, 70%+ attacking, 10% triage. \
Get a basic tool working fast, then start breaking things."""

TOOL_FORGE_GUIDANCE = """\
A CLI wrapper is table stakes — build it fast and move on. Then build \
whatever you need to attack effectively: scenario generators for edge-case \
inputs, state manipulators for invalid preconditions, interrupt injectors, \
concurrency probes.

If you need something you can't build (Docker, browser, GPU), say so \
in the Unreachable Attack Surfaces section."""

ATTACK_SURFACE_MAPPING_GUIDANCE = """\
Identify the attack surfaces — where can this software break?

Think inputs (malformed, huge, empty), state (corrupt, stale, missing), \
external dependencies (failing, slow, lying), and unvalidated assumptions.

Write attack surfaces to `{surface_file}`:
{surface_format}"""

TEST_EXPLORATION_GUIDANCE = """\
Systematically violate assumptions. For each surface, ask what the code \
assumes and what happens when that assumption is false. Prioritize by \
damage potential.

Only document breakage. If you can't break something, note what you tried."""

# ---------------------------------------------------------------------------
# Findings format
# ---------------------------------------------------------------------------

TEST_FINDING_FORMAT = """\
### F<n>: <title>
- **Surface:** <attack surface>
- **Severity:** critical | medium | low
- **Category:** crash | data-loss | silent-wrong | hang | race | leak | misleading-output
- **Repro steps:**
  1. <step>
  2. <what happens vs what should happen>
- **Root cause:** <if known>"""

# ---------------------------------------------------------------------------
# Discovery prompt
# ---------------------------------------------------------------------------

DISCOVERY_PROMPT = """\
You're looking for bugs in a software project. Not verifying it works — breaking it.

Assume happy paths work. Hunt for what breaks.

## Tasks
1. Read the project: README, source structure, tests, config
2. Identify attack surfaces — where can this break?
3. Figure out what tools you need to attack it
4. Write a GoalPlan JSON to `{output_path}`

{methodologies}

## Environment
{environment}

## Plan structure (4-6 stages)

**Stage 1: Attack Surface Analysis** (`persist_changes` true)

{tool_forge_guidance}

{surface_mapping_guidance}

Write attack surfaces to `{surface_file}`.
Write recon notes to `{run_dir}/test-recon.md`.

{time_guidance}

**Middle stages (1-4, parallel where independent):**

Each stage attacks a group of surfaces using tools from Stage 1. \
Every stage must produce findings or explain what attacks were tried \
and why they found nothing.

Update attack surface status in `{surface_file}`.

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
  "context": "Attack surfaces identified, what's most likely to break",
  "stages": [
    {{
      "index": 1,
      "name": "Short label",
      "description": "What this stage attacks",
      "acceptance_criteria": "Findings or explanation of attacks tried",
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
## Attack Methodologies

Prioritize by damage potential. The approaches, in rough order:

**Fault injection** — kill processes mid-write, corrupt configs, send signals \
at critical moments. What happens when things fail halfway?

**State corruption** — start with invalid state. Does the software detect and \
recover, or silently produce wrong results?

**Boundary probing** — empty, massive, and malformed inputs. What happens at \
the limits?

**Assumption hunting** — read the code, find unvalidated assumptions, \
systematically violate them.

**Concurrency** — race shared resources, interrupt and restart. Data corruption? \
Deadlocks?

Regression tests come last: reproduce the bug with a failing test, then fix."""
