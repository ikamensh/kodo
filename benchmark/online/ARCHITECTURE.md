# Online Benchmark System

## Architecture

```
                    ┌──────────────┐
                    │   Viewer     │  Static SPA (served by Cloud Run)
                    │  index.html  │  https://kodo-bench-430011644943.europe-west1.run.app
                    └──────┬───────┘
                           │ GET /data/{dataset}/index.json
                           │ GET /api/patch/{dataset}/{iid}/{arm}
                    ┌──────▼───────┐
                    │  Cloud Run   │  benchmark/online/server.py
                    │  (API + auth)│  europe-west1
                    └──┬────────┬──┘
                       │        │
              ┌────────▼─ ─┐  ┌──▼──────────┐
              │ Firestore  │  │    GCS      │
              │ (metadata) │  │  (patches)  │
              │ europe-west1  │ kodo-bench  │
              └────────────┘  └─────────────┘
                       ▲        ▲
                       │        │
              ┌────────┴────────┴──┐
              │   Benchmark Runner │  benchmark/online/client.py
              │   (per-task upload)│  any machine with a token
              └────────────────────┘
                       ▲        ▲
                       │        │
              ┌────────┴────────┴──┐
              │   Eval Machine     │  benchmark/evaluate_pending.py
              │ (--evaluate-pending│  any machine with Docker + token
              │  fetch → eval →    │  GET /api/unevaluated → eval → POST /api/eval-results
              │  upload results)   │
              └────────────────────┘
```

## Data Stores

### Firestore (structured data)

| Collection | Document | Fields |
|---|---|---|
| `runs/{run_id}` | Run metadata | kodo_version, task_count, arms, timeout, dataset, instance_ids, provenance |
| `datasets/{dataset}/results/{instance_id}` | Task results | `arms` map: `{arm: {status, elapsed_s, patch_len, error, run_id, provenance, resolved, eval_status}}` |
| `tokens/{sha256}` | API token registry | name, issued_to, notes, prefix, active, created_at, last_used_at, usage_count |

### GCS Bucket `kodo-bench` (large blobs)

```
patches/{dataset}/{instance_id}/{arm}.diff
```

## API Endpoints

### Write (require `Authorization: Bearer <api-token>`)

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/task-result` | Upload one task result + patch |
| `POST` | `/api/run` | Register a benchmark run |
| `POST` | `/api/eval-results` | Upload evaluation results |

### Read (authenticated)

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/unevaluated/{dataset}` | Predictions needing evaluation (with patches). Returns `{dataset, predictions: [{instance_id, arm, patch}]}`. Scans Firestore for results with `status` but no `eval_status`, fetches patches from GCS. Only `ok`/`partial` status included. |

### Read (public)

| Method | Path | Description |
|---|---|---|
| `GET` | `/data/{dataset}/index.json` | Aggregated results (from Firestore, 30s cache) |
| `GET` | `/data/{dataset}/patches.json` | All patches (from GCS, 30s cache) |
| `GET` | `/api/patch/{dataset}/{iid}/{arm}` | Single patch |
| `GET` | `/api/health` | Health check |
| `GET` | `/` | Viewer SPA |

### Admin (require `Authorization: Bearer <admin-token>`)

| Method | Path | Description |
|---|---|---|
| `POST` | `/admin/tokens` | Create token `{name, issued_to, notes}` → returns raw token |
| `GET` | `/admin/tokens` | List all tokens with metadata |
| `DELETE` | `/admin/tokens/{id_or_prefix}` | Revoke a token |

## Token Management

Tokens are stored in Firestore as SHA-256 hashes. The raw token is shown only at creation time.

Each token tracks:
- **name**: Human label (e.g. "Alice's laptop")
- **issued_to**: Who received it (e.g. "alice@example.com")
- **prefix**: First 8 characters, for identification in listings
- **usage_count** / **last_used_at**: Auto-updated on each API call
- **active**: Set to `false` to revoke

## Crash Resilience

Results upload **per-task** immediately after each task completes. If the benchmark process crashes:
- All completed tasks are already in Firestore + GCS
- The local `results.jsonl` also has them (for resumption via `--run-id`)
- No batch publish step needed

## Legacy: GitHub Pages Publishing

`benchmark/online/publish.py` still works as a batch tool for pushing results to the `gh-pages` branch. It's no longer required for the online viewer but can serve as a static backup or for offline viewing.

```bash
uv run python -m benchmark --publish           # publish all runs
uv run python -m benchmark --publish --run-id X # publish one run
```

## Infrastructure

| Component | Location | Details |
|---|---|---|
| Firestore | europe-west1 | Project `covenance-469421`, native mode, free tier |
| GCS bucket | us-central1 | `gs://kodo-bench` |
| Cloud Run | europe-west1 | `kodo-bench`, min 0 / max 3 instances, 256Mi |
| Admin token | Cloud Run env | `KODO_BENCH_ADMIN_TOKEN` |
