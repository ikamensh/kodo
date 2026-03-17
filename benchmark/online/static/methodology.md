# Methodology

> This page compares **{{PRIMARY_ARM_LABEL}}** and **{{SECONDARY_ARM_LABEL}}** on **{{OVERLAP_TASKS}}** tasks from SWE-bench Verified where both agents have been fully evaluated. It is an overlap slice — not the full 500-task Verified leaderboard.

## What is this benchmark?

[SWE-bench](https://www.swebench.com/) is the industry-standard benchmark for evaluating AI coding agents on real-world software engineering tasks. Each task is a genuine GitHub issue from a popular open-source Python project, paired with a human-written test that verifies whether the fix is correct.

This page presents a **head-to-head comparison**: both agents attempt the same tasks under identical conditions, and we report which agent solved each one.

## Evaluation scope

- **Dataset:** SWE-bench Verified (expert-validated subset)
- **Tasks shown:** {{OVERLAP_TASKS}} (both agents attempted and evaluated)
- **Repositories:** {{REPO_COUNT}} open-source projects
- **Snapshot:** frozen at {{SNAPSHOT_CREATED_AT}}
- Excludes tasks only one agent attempted and tasks still awaiting evaluation

## Fair comparison: identical conditions

Both agents receive exactly the same inputs for every task:

- A clean checkout of the repository at the issue's base commit
- The original GitHub issue description
- The same high-level instruction template (shown below)
- Full filesystem and shell access

Neither agent receives any hints: no gold patch, no failing test names, no file lists.

### Shared prompt template

```text
{{SHARED_PROMPT_TEMPLATE}}
```

## How each agent runs

### Cursor

- Model: `composer-1.5`
- Runs as a standalone coding agent with no orchestration layer

### {{PRIMARY_ARM_LABEL}}

- Worker: Cursor on `composer-1.5` (same underlying model as the Cursor arm)
- Adds a Kodo orchestrator layer around the worker
- Orchestrator model: `{{KODO_ORCHESTRATOR_MODEL}}`

Both agents use the same underlying code-editing model. The key difference is that {{PRIMARY_ARM_LABEL}} wraps it with Kodo's orchestration, which manages the problem-solving strategy.

## How patches are captured

After each agent finishes, the benchmark runner captures a unified diff of all changes made to the repository (excluding framework bookkeeping files).

## How "resolved" is determined

Evaluation uses the standard [SWE-bench harness](https://github.com/princeton-nlp/SWE-bench):

1. The agent's diff is applied to the repository at the base commit
2. Test files are reset and the gold test patch is applied
3. The full test suite runs inside an isolated Docker container
4. A task is **resolved** only if every FAIL_TO_PASS test now passes *and* all previously-passing (PASS_TO_PASS) tests remain green
5. Partial fixes do not count

## Limitations

- **Overlap-only view** — this page shows the intersection of tasks both agents completed, not the full benchmark
- **Frozen snapshot** — results are from a specific point in time and do not auto-update
- **Orchestration difference** — {{PRIMARY_ARM_LABEL}} is not raw Cursor; even with the same worker model, the orchestration layer changes how problems are approached
- **Single-run results** — benchmark best practices recommend caution when interpreting any single run as a stable capability estimate
