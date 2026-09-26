# Kodo maintenance review — September 2026

Kodo already offers enough breadth. Its next priority should be trustworthy completion and reliable control of unattended runs.

## Maintenance changes

- Consolidated backend discovery, model defaults, orchestrator eligibility, preflight commands, and session identification in `kodo/backends.py`. This fixes OpenCode-only selection, Kiro being selected as a nonexistent orchestrator, Kiro-only teams, and Kimi team snapshot reloads.
- Consolidated model metadata lookup so aliases, bare IDs, and provider-qualified IDs share pricing and display information. Explicit provider-qualified models previously lost pricing information.
- Refreshed model choices, including retired Kimi and DeepSeek IDs, and removed duplicate version constants. Explicit model overrides remain available. See the [model audit](model-audit-2026-09-08.md) for exact versions, current provider sources, and pricing limits.
- Corrected the runtime package version and outdated README commands. The README now describes the actual default verification behavior.
- Bounded incompatible dependency families and enabled slow/integration tests in CI. Updated PII cleaning, dotenv, and charset detection requirements.
- Fixed cleanup of partially created worktrees after interruption. Generated run status no longer blocks merging completed parallel work or gets added to commits.
- Let MCP servers finish their shutdown lifecycle before closing the event loop. Kimi now shares the CLI orchestrator lifecycle and uses native ACP sessions, with tested resume, cancellation, and bounded tool dispatch.

## Dependency findings

A clean environment using the existing local lockfile exposed a preexisting package-version mismatch. Installing the unrestricted latest dependencies separately produced 68 failures: Pydantic AI v2, MCP v2, changed summarization behavior, and Anthropic v1's move to `httpx2` are not drop-in upgrades for this implementation. See the [Pydantic AI upgrade guide](https://pydantic.dev/docs/ai/project/changelog/) and [Anthropic migration guide](https://github.com/anthropics/anthropic-sdk-python/blob/main/MIGRATION.md).

The package now constrains those incompatible families while allowing compatible releases. Pydantic AI uses its smaller provider package, with a minimum version that supports current model protocols. The local lockfile is ignored by the repository, so CI tests a fresh installation of the declared requirements.

The optional `kimi-agent-sdk` 0.0.5 depended on Kimi CLI 1.12.x and pinned OpenAI to 2.14.x, as well as old MCP dependencies. Kimi now uses the installed native CLI's agent protocol to remove this conflict. The resolved shared stack includes Claude Agent SDK 0.2.152, OpenAI 3.8.0, Pydantic AI Slim 1.107.5, and MCP 1.30.0. Kimi uses the native CLI's authentication/configuration instead of the old SDK's API-key bootstrap; see [provider setup](providers.md#kimi-smart-workers).

## Features worth keeping

| Capability | Value for local unattended coding |
| --- | --- |
| Goal intake, refinement, adaptive stages | Turns a broad request into actionable work and adjusts the plan |
| Context summaries, run resume, agent notes | Keeps long tasks progressing across limited context windows |
| Parallel worktrees and commits | Supports independent work and reviewable checkpoints |
| Custom teams and multiple providers | Reuses subscriptions and enables different implementation/review perspectives |
| Test, improve, and fix-from workflows | Exercises software and carries findings into focused repairs |
| Steering and optional coach | Lets a human or observer redirect an active run |
| JSON output, logs, viewer, dashboard | Makes runs inspectable during and after execution |

## Missing or incomplete behavior, in priority order

1. **Completion needs a clear verification contract.** `CycleConfig.done_mode` defaults to `new`. Its `goal_done` handler marks success immediately; it merely instructs the orchestrator to consult testers. Configured full verification and stage file checks are bypassed. Tests explicitly endorse this, so this review preserves the policy and corrects the documentation. Keep the useful `goal_done`, `end_cycle`, and `raise_issue` distinctions, define what evidence completion requires, and delete the legacy/new split. Evidence: `kodo/orchestrators/types.py`, `kodo/orchestrators/tools.py`, `tests/orchestrators/test_done_signal.py`.

2. **Stopping should be deterministic and resumable.** `/stop` and the first Ctrl+C enqueue advice to call `goal_done` or `end_cycle`. The latter continues to another cycle; the former reports success. Introduce a stopped state checked before new dispatches and at cycle boundaries, with a resumable checkpoint and an honest outcome. Evidence: `kodo/cli/_interactive.py`, `kodo/orchestrators/tools.py`.

3. **Unattended runs need run-wide limits and notification hooks.** Exchange/cycle caps and per-call timeouts exist, but no total runtime/API-spend ceiling was found. Adaptive execution can raise the default cycle limit to 50. Add a deadline and actual API-spend limit that stop at a checkpoint, plus an opt-in local completion/failure hook. Keep continuous portfolio scheduling in Hive. Evidence: `kodo/cli/_main.py`, `kodo/cli/_launch.py`.

4. **Produce a compact durable result for morning review.** Normal goal completion prints a shortened summary, while detailed records are spread across logs. Assemble achieved/unmet criteria, changed commits, verification commands/results, blockers, and a resume command from those existing records. Evidence: `kodo/cli/_launch.py`, `kodo/orchestrators/run_status.py`, `kodo/log.py`.

5. **Dashboard controls are unfinished.** The feedback endpoint validates JSON and returns success, but its advisory-queue connection is still a TODO. The stop endpoint also returns success without signaling the run. Wire these controls to the active run or remove the misleading success responses until they work. Evidence: `kodo/dashboard/server.py`, `do_POST` feedback and stop branches.

Cost totals also need an explicit completeness indicator before they can support reliable spend limits. Native Kimi leaves unreported usage/cost unset, but aggregate code currently turns missing costs into zero. Reported totals therefore exclude that usage; they are not a proof that the run was free.

## What to simplify or reconsider

- **Unify completion handling first.** The legacy/new split duplicates substantial logic and gives verification settings inconsistent meaning.
- **Share viewer/dashboard rendering.** Portable HTML export and live run monitoring are both useful; they need not maintain separate event presentation logic.
- **Decide whether `kodo.knowledge` belongs here.** It is a separate research/convergence workflow with its own CLI and team designer, absent from the main command map. Extract it if actively used; otherwise remove it from the coding product.
- **Measure before removing providers or coach.** This audit found no usage evidence proving them useless. Compare completion rate, API spend, elapsed time, and human intervention before pruning them.
- **Recheck the orchestration benefit with current models.** The existing benchmark harness is useful, but the historical Composer 1.5 comparison does not establish the gain for the refreshed defaults. Keep the historical results labeled as such and use the harness for the next evaluation.

## Validation

- Full non-live suite on macOS/Python 3.13: **1,739 passed, 1 skipped, 21 deselected** (`uv run --locked python -m pytest tests/ -q -m 'not live'`). This includes slow and integration workflows.
- Final Kimi budget regression: **3 passed**, covering exact limits and already-queued tool starts without hiding actual work. Worker, MCP, resume, and backend lifecycle coverage also passed together (**50 tests**).
- Source distribution and wheel build successfully. CLI help/version, focused Ruff error checks, and `git diff --check` pass.
- Six warnings remain in the full run: two Uvicorn/websockets deprecations, three Claude SDK permission-callback warnings, and one asyncio subprocess finalizer warning during Claude transport tests. These were not suppressed. Windows/Linux execution remains for CI.

Provider interactions are tested through mocked transports and local backend processes; this is not a live benchmark of model quality or account access. No paid inference calls were made.

The second-opinion CLI was attempted with OpenAI and Gemini; both configured API keys were rejected. Independent local agent review and integration tests were used instead.
