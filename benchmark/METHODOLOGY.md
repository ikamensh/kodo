# SWE-bench Evaluation Methodology

## Overview

We evaluate kodo (an orchestrator that coordinates coding agents) against standalone
coding agents on SWE-bench Verified — a human-verified subset of 500 real GitHub issues
from 12 popular Python repositories.

The goal: measure whether orchestration adds value over running a single agent alone.

## Evaluation Protocol

### Dataset

- **Primary**: SWE-bench Verified (`princeton-nlp/SWE-bench_Verified`, 500 tasks)
- All tasks are real GitHub issues with human-verified test cases
- We report results on **full dataset runs** as the primary metric
- Development subsets (20/50 tasks) are used for iteration and clearly labeled

### Agent Prompting

All arms receive an identical prompt:

```
Fix the following GitHub issue in this repository.

Issue: {instance_id}

{problem_statement}

Make the minimal code changes needed to fix this issue.
Do not add or modify tests.
```

The `problem_statement` is the original GitHub issue text — the standard input used by
all SWE-bench evaluations. No additional hints, gold patch content, test names, or
file locations are provided.

### What agents receive

- A clean checkout of the repository at the issue's `base_commit`
- The prompt above
- Full access to the repository filesystem and shell

### What agents do NOT receive

- Gold patch or any portion thereof
- `fail_to_pass` or `pass_to_pass` test names
- `hints_text` field
- `test_patch` field
- Any information about which files were modified in the gold solution

### Arms

| Arm | Description | Tool |
|-----|-------------|------|
| `cursor` | Standalone Cursor agent (composer-1.5) | `cursor-agent --print` |
| `kodo:solo` | Kodo orchestrator with single Cursor worker | `kodo --team solo` |

Both arms use Cursor's composer-1.5 model as the underlying agent. The only difference
is whether kodo's orchestrator (planning, verification loop) wraps the agent.

### Timeouts

| Arm | Default Timeout |
|-----|-----------------|
| Standalone agents | 7,200s (2 hours) |
| Kodo orchestrated | 43,200s (12 hours) |

**Disclosure**: Kodo receives more time because the orchestrator adds overhead (planning,
verification, potential retries). This timeout asymmetry must be considered when
interpreting results. We report wall-clock time per task alongside resolve rates.

### Execution

- Each task runs in an isolated repository clone (bare-clone cache + `git clone --shared`)
- Agent processes run with `ANTHROPIC_API_KEY` and `CLAUDECODE` removed from environment
  to prevent nested session conflicts
- Per-repo locking prevents parallel clone race conditions
- Results are appended incrementally and support resumption

## Diff Capture

After each agent completes:

1. `git reset {base_commit}` — collapses any agent commits to working tree changes
2. `git add -A` — stages all changes including new/deleted files
3. `git diff --cached -- . :(exclude).kodo` — captures unified diff, excluding kodo metadata

This produces the standard unified diff format expected by `git apply`.

## Evaluation

We use the **standard swebench harness** (`swebench.harness.run_evaluation`) — the same
tool used by Anthropic, Google, and other labs reporting SWE-bench numbers.

The harness:

1. Builds a Docker container per instance with the repo at `base_commit`
2. Applies the agent's diff via `git apply` (with fallbacks to `--reject` and `patch`)
3. Resets test files to `base_commit`, then applies the gold `test_patch`
4. Runs the full test suite
5. Checks that ALL `FAIL_TO_PASS` tests now pass AND ALL `PASS_TO_PASS` tests still pass

An instance is **resolved** only if both conditions hold (`RESOLVED_FULL`). Partial
fixes do not count.

### Predictions Format

Standard swebench JSONL format:

```json
{"instance_id": "...", "model_name_or_path": "...", "model_patch": "..."}
```

## Comparison with Major Lab Methodology

| Aspect | Anthropic (Claude 4.5) | Google (Gemini 3) | Our Setup |
|--------|----------------------|------------------|-----------|
| Dataset | Full 500 Verified | Full 500 Verified | Subsets + full runs |
| Harness | Standard swebench | Standard swebench | Standard swebench |
| Trials | 10 (averaged) | Not disclosed | Single run |
| Prompt | Engineered (explore, test) | Not disclosed | Minimal |
| Timeout | Not disclosed | Not disclosed | Disclosed per arm |
| Timing | Not reported | Not reported | Reported (median, mean, p90) |

### Key Differences

1. **Single trial vs multi-trial**: Major labs average over multiple runs. Single runs
   have higher variance. We plan to move to 3-5 trials for published numbers.

2. **Minimal prompt**: Our prompt is intentionally simple. More engineered prompts
   (instructing agents to explore, write tests, etc.) improve scores. We use the same
   prompt for all arms to isolate orchestration impact.

3. **Timing transparency**: We report wall-clock time per task, which most labs omit.
   This enables time-adjusted comparisons.

## Known Limitations

1. **Subset selection bias**: Development subsets are curated toward multi-file,
   medium-complexity tasks where orchestration is expected to help. Full-dataset runs
   remove this bias.

2. **SWE-bench Verified contamination**: OpenAI (Feb 2026) found that frontier models
   can reproduce ground-truth patches for some Verified tasks, suggesting training data
   contamination. We also plan SWE-bench Pro evaluation for contamination-resistant results.

3. **Resource contention**: Parallel execution (`--parallel > 1`) introduces CPU/memory
   contention. Official numbers should use `parallel=1`.

4. **Timeout asymmetry**: Kodo's higher timeout is necessary for its multi-step process
   but constitutes a time advantage. We mitigate this by reporting time metrics.

## Reproducibility

```bash
# Install
uv pip install 'swebench>=2.0'

# Run cursor baseline
uv run python -m benchmark \
  --dataset verified --arm cursor --parallel 1

# Run kodo orchestrated
uv run python -m benchmark \
  --dataset verified --arm kodo:solo --parallel 1

# Evaluate
uv run python -m benchmark \
  --dataset verified --arm cursor --arm kodo:solo --evaluate-only --run-id <RUN_ID>
```

Results are written to `~/.kodo/benchmark/runs/<run_id>/` with full JSONL logs,
predictions, and eval summaries.
