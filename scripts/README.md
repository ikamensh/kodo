# Scripts

Utility scripts for development and analysis. Not part of the `kodo` package.

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
