# SWE-bench Benchmark

Evaluate kodo against standalone coding agents (Claude Code, Cursor, Codex, Gemini) on SWE-bench tasks.

## Quick Start

```bash
# Run a small subset
uv run python -m benchmark --subset benchmark/subsets/pro-20.json --arm kodo:solo --arm claude

# Run full dataset
uv run python -m benchmark --dataset verified --arm kodo:solo --arm cursor

# Resume a crashed run
uv run python -m benchmark --run-id 20260309_105930

# Evaluate + report only
uv run python -m benchmark --evaluate-only --run-id 20260309_105930
```

## Arms

| Arm | Tool | Notes |
|-----|------|-------|
| `claude` | Claude Code CLI | Default model: opus. Override: `claude:sonnet` |
| `cursor` | Cursor agent CLI | composer-1.5 |
| `codex` | OpenAI Codex CLI | Default model: gpt-5.4. Override: `codex:o3` |
| `gemini` | Google Gemini CLI | |
| `kodo` | Kodo orchestrator | Default team. Override: `kodo:solo`, `kodo:solo+opus` |

## Options

```
--dataset pro|verified|lite   SWE-bench variant (default: pro)
--subset <path>               JSON subset file (overrides --dataset)
--arm <name>                  Repeatable (default: claude + kodo)
--limit N                     Run first N tasks
--offset N                    Skip first N tasks
--instance-ids id1 id2 ...    Specific tasks
--repo owner/repo             Filter by repository
--language python|go|js       Filter by language (Pro only)
--timeout <s>                 Non-kodo timeout (default: 7200 / 2h)
--timeout-kodo <s>            Kodo timeout (default: 43200 / 12h)
--parallel N                  Concurrent tasks (default: 1)
--run-id <id>                 Resume or reference a run
--skip-eval                   Skip evaluation after run
--evaluate-only               Evaluate existing predictions
--report-only                 Generate report from existing results
--publish                     Publish to GitHub Pages
--status                      Show all runs
```

## Online Results

Results upload automatically per-task to the online store. No batch step needed — if the benchmark crashes, completed tasks are already stored.

**Viewer**: https://kodo-bench-430011644943.europe-west1.run.app

### Setup (for uploading)

Set two environment variables:

```bash
export KODO_BENCH_URL=https://kodo-bench-430011644943.europe-west1.run.app
export KODO_BENCH_TOKEN=<your-api-token>
```

Then run benchmarks normally. Results upload in the background after each task.

If these variables are not set, the benchmark runs in local-only mode (no upload, no errors).

### Getting a Token

Ask the project admin. Tokens are managed via the admin API:

```bash
# Create (admin only)
curl -X POST $KODO_BENCH_URL/admin/tokens \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "descriptive-name", "issued_to": "user@example.com"}'

# List all tokens (admin only)
curl $KODO_BENCH_URL/admin/tokens \
  -H "Authorization: Bearer $ADMIN_TOKEN"

# Revoke (admin only)
curl -X DELETE $KODO_BENCH_URL/admin/tokens/<prefix> \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

### Architecture

See [online/ARCHITECTURE.md](online/ARCHITECTURE.md) for infrastructure details (Firestore, GCS, Cloud Run, token model, API endpoints).

## Local Storage

All runs are saved locally at `~/.kodo/benchmark/runs/<run_id>/`:

| File | Content |
|------|---------|
| `meta.json` | Run config: arms, dataset, instance_ids |
| `results.jsonl` | Per-task: status, elapsed, patch_len, error |
| `predictions-<arm>.jsonl` | SWE-bench format patches |
| `eval-summary.json` | Resolved/failed/error per arm |
| `report.md` | Human-readable summary |
| `logs/<iid>/<arm>/` | stdout.log, stderr.log, kodo_trace.jsonl |

## Evaluation

Uses the standard swebench harness (`swebench.harness.run_evaluation`). See [METHODOLOGY.md](METHODOLOGY.md) for full details on prompting, diff capture, and comparison with major lab methodology.

## Subsets

Pre-built task subsets in `benchmark/subsets/`:

| File | Tasks | Dataset |
|------|-------|---------|
| `pro-20.json` | 20 | SWE-bench Pro (8 Python, 8 Go, 4 JS) |
| `verified-20.json` | 20 | SWE-bench Verified |
| `verified-50.json` | 50 | SWE-bench Verified |
| `verified-250-new.json` | 250 | SWE-bench Verified |
