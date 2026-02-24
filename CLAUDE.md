# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run all tests (excludes live tests by default)
uv run pytest tests/ -x -q

# Run a single test file
uv run pytest tests/cli/test_intake.py -v

# Run a single test by name
uv run pytest tests/ -k "test_name" -v

# Run CLI with mocked sessions (no API keys)
uv run python scripts/run_cli_mocked.py

# Lint and format
ruff check kodo/ tests/ scripts/
ruff format kodo/ tests/ scripts/
```

## Architecture

kodo is an autonomous multi-agent coding system designed to run overnight on Claude Code Max subscriptions. The orchestrator (configurable LLM via pydantic-ai) delegates work to agents (Claude Code sessions covered by subscription).

### Core flow

**Run → Cycles → Exchanges → Agent queries**

- **Exchange**: One orchestrator turn (think → delegate to agent → read result)
- **Cycle**: A full orchestration session; if incomplete, the next cycle starts with a summary of prior work
- **Run**: Multiple cycles until the goal is met or limits are reached
- **Stage**: An independently verifiable piece of a goal plan; stages execute in sequence with checkpointing

### Key modules

- `kodo/cli.py` — Interactive CLI, goal collection, intake refinement
- `kodo/agent.py` — Agent class wrapping a Session with a directive, timeout, and structured logging
- `kodo/factory.py` — Team preset registry (`saga`/`mission`), team builders, model alias resolution, backend detection
- `kodo/team_config.py` — Loads/builds teams from JSON config (`{project}/.kodo/team.json` or `~/.kodo/teams/{name}.json`)
- `kodo/log.py` — Structured JSONL logging to `~/.kodo/runs/{run_id}/`, run stats, resume support
- `kodo/sessions/` — Session protocol implementations: `ClaudeSession` (SDK), `CursorSession` (CLI), `CodexSession`, `GeminiCliSession`
- `kodo/orchestrators/` — `ApiOrchestrator` (pydantic-ai) and `ClaudeCodeOrchestrator` (MCP-based)

### Teams

- **saga**: Full team (worker_fast, worker_smart, architect, tester, tester_browser). Default: 30 exchanges, 5 cycles.
- **mission** / **quick**: Minimal team (worker_fast, worker_smart). Default: 20 exchanges, 1 cycle.

### Verification

When the orchestrator calls `done()`, tester and architect independently review. They signal `"ALL CHECKS PASS"` or `"MINOR"` to indicate acceptance.

### Cost tracking

Two buckets: "API" (real spend on orchestrator) and "Virtual" (subscription-covered agent work). Tracked at `QueryResult` level and aggregated in `RunStats`.

### Shared prompts

Agent role prompts (`TESTER_PROMPT`, `ARCHITECT_PROMPT`, etc.) live in `kodo/__init__.py`. The orchestrator system prompt is in `kodo/orchestrators/base.py`.

## CLI Verification

- `kodo runs` — Lists runs from ~/.kodo/runs/. With no runs (or filtered project with none): prints "No runs found." and exits 0.
- `kodo --goal "X" --yes --team quick` — Non-interactive run. Without API keys and `--orchestrator api`: fails immediately with "ANTHROPIC_API_KEY not set" (or GEMINI_API_KEY for gemini models). Use `--skip-intake` to skip the intake phase.
- `scripts/run_cli_mocked.py` — Full CLI flow with mocked orchestrator and agents; no API keys or backends required.

## Test Conventions

- All external agents are mocked via `FakeSession` / `FakeRunResult` from `tests/conftest.py`
- All `fake_agent_init` mocks must accept `**kwargs` (for history_processors, etc.)
- Use `make_scripted_session()` for multi-turn conversations with file writes
- Test user intent, not implementation details
- `@pytest.mark('not live')` excludes tests requiring real backends
