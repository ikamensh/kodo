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
--evaluate-pending            Fetch & eval unevaluated results from server
--report-only                 Generate report from existing results
--publish                     Publish to GitHub Pages
--upload-pending              Upload results not yet sent to server
--status                      Show all runs
```

## Online Results

Results upload automatically per-task to the online store. No batch step needed — if the benchmark crashes, completed tasks are already stored.

**Viewer**: https://kodo-bench-430011644943.europe-west1.run.app

### Mirroring For Analysis

To copy the public benchmark snapshot into local JSON files:

```bash
uv run python -m benchmark --mirror-online --dataset verified
uv run python -m benchmark --mirror-online --dataset pro --mirror-patches
```

This writes files under `~/.kodo/benchmark/mirror/<dataset>/`:

| File | Content |
|------|---------|
| `index.json` | Raw public dataset snapshot |
| `rows.json` | One row per `instance_id` + `arm` for plotting |
| `patches.json` | Optional patch mirror |

For Python analysis:

```python
from benchmark.online.mirror import load_rows

rows = load_rows("~/.kodo/benchmark/mirror/verified")
resolved = [row for row in rows if row.get("resolved") is True]
```

### Setup (for uploading)

Set two environment variables:

```bash
export KODO_BENCH_URL=https://kodo-bench-430011644943.europe-west1.run.app
export KODO_BENCH_TOKEN=<your-api-token>
```

Then run benchmarks normally. Results upload in the background after each task.

If these variables are not set, the benchmark runs in local-only mode (no upload, no errors).

### Getting a Token

**Self-service**: Go to the [registration page](https://kodo-bench-430011644943.europe-west1.run.app/register.html), enter your name and GitHub username, agree to the benchmark guidelines, and get a token instantly.

**Admin**: Tokens can also be managed via the admin API (`KODO_BENCH_ADMIN_TOKEN` required):

```bash
curl -X POST $KODO_BENCH_URL/admin/tokens \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "descriptive-name", "issued_to": "user@example.com"}'
```

### Contributing Results

1. Register at the [benchmark page](https://kodo-bench-430011644943.europe-west1.run.app/register.html) to get a token
2. Install agents you want to benchmark (any of: Claude Code, Cursor, Codex, Gemini CLI)
3. Configure your environment:
   ```bash
   export KODO_BENCH_URL=https://kodo-bench-430011644943.europe-west1.run.app
   export KODO_BENCH_TOKEN=<your-token>
   ```
4. Run the benchmark:
   ```bash
   uv run python -m benchmark
   ```

The harness auto-detects installed agents and requests task assignments from the central server. Results upload after each task — if the process crashes, completed work is preserved.

To run specific backends: `uv run python -m benchmark --backends claude,cursor`

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

### Evaluating Pending Results

Contributors run agents and upload results to the server. Evaluation (Docker-based swebench harness) can happen separately on any machine with Docker:

```bash
# Fetch all unevaluated predictions from the server, run Docker eval, upload results back
uv run python -m benchmark --evaluate-pending --dataset verified
```

This enables a separation of concerns: contributors only need agent access (Claude/Cursor/etc.), while evaluation can run on a dedicated machine with Docker and swebench installed.

**Flow:**
1. Fetches predictions missing `eval_status` from the server (`GET /api/unevaluated/{dataset}`)
2. Writes them as `predictions-{arm}.jsonl` files in a synthetic run directory
3. Runs `evaluate_predictions()` (Docker swebench harness)
4. Uploads `resolved`/`failed`/`error` arrays back via `POST /api/eval-results`

Only predictions with status `ok` or `partial` are fetched (errors/timeouts are skipped).

## Subsets

Pre-built task subsets in `benchmark/subsets/`:

| File | Tasks | Dataset |
|------|-------|---------|
| `pro-20.json` | 20 | SWE-bench Pro (8 Python, 8 Go, 4 JS) |
| `verified-20.json` | 20 | SWE-bench Verified |
| `verified-50.json` | 50 | SWE-bench Verified |
| `verified-250-new.json` | 250 | SWE-bench Verified |
