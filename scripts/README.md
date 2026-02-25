# Scripts

Utility scripts for development and analysis. Not part of the `kodo` package.

## `run_improve_mocked.py`

Run `kodo --improve` on buggy_project with all AI backends mocked. No API keys or real backends required. Verifies project type detection, improve plan structure, and launch flow.

```bash
uv run python scripts/run_improve_mocked.py
```

**End-to-end experience (run output):**

```
Project type: app
Improve plan stages: 4
  1. Baseline & Static Analysis
  2. Happy Path Integration Testing
  3. Exploratory & Adversarial Testing
  4. Fix & Report

  🦉 kodo v0.4.57 — autonomous multi-agent coding
  Project: tests/fixtures/buggy_project
  Improve type: app

============================================================
  READY TO LAUNCH
============================================================
  Project:      tests/fixtures/buggy_project
  Goal:         Thoroughly test and improve this codebase using a structured sequence...
  Stages:       4
                  1. Baseline & Static Analysis
                  2. Happy Path Integration Testing
                  3. Exploratory & Adversarial Testing
                  4. Fix & Report
  Team:         saga — Full team (Cursor + Codex + Gemini CLI + Claude Code)
  Orchestrator: api (gemini-flash)
  Exchanges:    30/cycle, 5 cycles

Team: saga — ... Orchestrator: api (mock)
Team:
  worker_fast (? / fake-model)
  tester (? / fake-model)
  tester_browser (? / fake-model)
  worker_smart (? / fake-model)
  architect (? / fake-model)
Project dir: tests/fixtures/buggy_project
Max: 30 exchanges/cycle, 5 cycles
Stages: 4
Log: ~/.kodo/runs/<run_id>/run.jsonl

==================================================
Done: 1 cycle(s), 1 exchanges, $0.0000
  Done.

OK: kodo --improve completed with mocked AI
```

**Log output (run.jsonl):** run_init → cli_args (goal, plan, stages) → run_start (orchestrator mock, 4 stages) → cycle_end → run_end.

**Issues encountered:** None. The script uses `--yes` to skip confirmation prompts. With mocks, the orchestrator returns immediately without running real stages.

## `analyze_run.py`

Parse kodo JSONL run logs and print a human-readable report: costs, tokens, timeline, per-agent breakdown, and final outcome.

```bash
uv run python scripts/analyze_run.py ~/.kodo/runs/<run_id>/run.jsonl
```

## `harness.py`

Reusable test harness for observing kodo component interactions. Wraps agents with instrumentation that prints every orchestrator ↔ worker exchange in real-time.

```python
from scripts.harness import instrument_team, print_banner
team = instrument_team({"worker": my_agent})
# Now run orchestrator.cycle() — every exchange is printed
```

## `test_plan_interaction.py`

Live integration test: Gemini Flash orchestrator + Claude Code worker. Observes whether the "ask for 3 options, then choose" flow works through plan mode.

```bash
uv run python scripts/test_plan_interaction.py
uv run python scripts/test_plan_interaction.py "your custom goal"
```

Requires: `GOOGLE_API_KEY` (or `GEMINI_API_KEY`), `claude` CLI on PATH.

## `test_architecture_decisions.py`

Runs goals with deliberate architecture "traps" through the harness, then checks the output files for signs of correct vs incorrect choices. Prints a pass/fail scorecard.

Scenarios:
- **offline-timer** — must avoid CDN/external URLs
- **tiny-bio-card** — must stay under 4KB
- **csv-parser** — should use stdlib `csv`, not hand-rolled parsing
- **bar-chart** — CSS transitions, no canvas

```bash
# All scenarios
uv run python scripts/test_architecture_decisions.py

# Single scenario
uv run python scripts/test_architecture_decisions.py csv-parser
```

Requires: `GOOGLE_API_KEY` (or `GEMINI_API_KEY`), `claude` CLI on PATH.

## `experiments/`

One-off SDK research scripts exploring Claude Code agent behavior (plan mode, callbacks, permission modes). Useful as reference for understanding SDK edge cases — not tests.
