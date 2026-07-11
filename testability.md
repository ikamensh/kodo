# Testability Contract

This repository is primarily a Python CLI product. The standing test surfaces are:

- the `kodo` command and mocked end-to-end scripts;
- local read-only web surfaces for run logs: dashboard and viewer;
- the benchmark online results service under `benchmark/online`, which has its own Dockerfile.

## Run

### local

From a fresh checkout:

```bash
git clone https://github.com/ikamensh/kodo.git
cd kodo
uv sync --extra test --group dev
```

Verify the installed CLI entrypoint:

```bash
uv run kodo --version
```

Run a no-credentials mocked end-to-end CLI flow:

```bash
export KODO_RUNS_DIR="$(mktemp -d)"
uv run python scripts/run_cli_mocked.py
```

Start the dashboard on an explicit port. Default port: `8050`.

```bash
uv run python -m kodo.dashboard --port 8050 --no-open
```

Start the standalone log viewer on an explicit port. Default port: `8080`.

```bash
RUN_LOG="$(find "$KODO_RUNS_DIR" -name log.jsonl | sort | tail -n 1)"
uv run python -m kodo.viewer --serve --port 8080 "$RUN_LOG"
```

The dashboard and viewer commands are foreground servers. Run one server per shell or stop the first with `Ctrl-C` before starting the next.

For fast regression coverage, use the default pytest selection:

```bash
uv run pytest -q
```

`pyproject.toml` excludes `live`, `slow`, and `integration` tests by default.

### docker

Docker support is scoped to the benchmark online results service, not the root `kodo` CLI.

Build the image:

```bash
docker build -t kodo-benchmark-online:test benchmark/online
```

Run it with an explicit host port. Container default port: `8080`.

```bash
docker run --rm --name kodo-benchmark-online -p 8081:8080 kodo-benchmark-online:test
```

## Health

CLI entrypoint health:

```bash
uv run kodo --version
```

Expected output: `kodo <version>`, for example `kodo 0.5.1`. It should return within 5 seconds after dependencies are installed.

Mocked run health:

```bash
export KODO_RUNS_DIR="$(mktemp -d)"
uv run python scripts/run_cli_mocked.py
```

Expected output includes `Done: 1 cycle` and `Done.`. It should return within 30 seconds and create one run directory containing `log.jsonl` under `$KODO_RUNS_DIR`.

Dashboard health, while `uv run python -m kodo.dashboard --port 8050 --no-open` is running:

```bash
curl -fsS http://127.0.0.1:8050/api/runs
```

Expected response: a JSON array, possibly empty. Wait up to 5 seconds after server start.

Viewer health, while `uv run python -m kodo.viewer --serve --port 8080 "$RUN_LOG"` is running:

```bash
curl -fsS http://127.0.0.1:8080/ -o /tmp/kodo-viewer-health.html
head -n 1 /tmp/kodo-viewer-health.html
```

Expected first line: `<!DOCTYPE html>`. Wait up to 5 seconds after server start. A browser request for `/favicon.ico` may return 404 and does not indicate viewer failure.

Benchmark online Docker health, while the container is running:

```bash
curl -fsS http://127.0.0.1:8081/api/health
```

Expected response: `{"status": "ok"}`. Wait up to 5 seconds after container start.

## Reset

Kodo is not stateless. Run state lives in these places:

- `$KODO_RUNS_DIR` when set;
- otherwise `~/.kodo/runs/`;
- project-local `.kodo/` files such as `config.json`, `run-status.md`, `team.json`, role notes, and `test-coverage.md`;
- user-level config and teams under `~/.kodo/config.json` and `~/.kodo/teams/`;
- benchmark runner state under `~/.kodo/benchmark/`;
- ignored smoke-script scratch directories such as `tmp_mock_run/` and `tmp_smoke_*`.

Recommended test isolation is to set `KODO_RUNS_DIR` to a temporary directory for every test run and use a disposable project checkout or fixture project.

Reset local test state created by the commands above:

```bash
rm -rf "$KODO_RUNS_DIR"
rm -rf tmp_mock_run tmp_smoke_* tmp_fault_cli
rm -f /tmp/kodo-viewer-health.html
```

Reset project-local kodo state only inside a disposable test project:

```bash
rm -rf .kodo
```

Reset benchmark local state only when the benchmark workspace is disposable:

```bash
rm -rf ~/.kodo/benchmark/runs ~/.kodo/benchmark/mirror
```

Reset Docker state:

```bash
docker rm -f kodo-benchmark-online 2>/dev/null || true
docker rmi kodo-benchmark-online:test 2>/dev/null || true
```

There is no seed command for the mocked CLI flow; `scripts/run_cli_mocked.py` creates its own temporary target project and mock orchestration.

## Credentials & accounts

No credentials are required for:

- `uv run kodo --version`;
- `uv run python scripts/run_cli_mocked.py`;
- dashboard and viewer health checks against local run logs;
- `docker build` for `benchmark/online`;
- `GET /api/health` on the local benchmark online container.

Real non-mocked `kodo` runs need at least one authenticated worker backend account or CLI, depending on team selection:

- Claude Code account/subscription and `claude` CLI for Claude workers and architects;
- Cursor account/subscription and Cursor CLI for Cursor workers and testers;
- Codex CLI with ChatGPT subscription or `OPENAI_API_KEY` for Codex workers;
- Gemini CLI with Google login or Gemini API key for Gemini CLI workers;
- Kimi account with `KIMI_API_KEY` for Kimi workers;
- Kiro CLI with AWS Builder ID or AWS Pro for Kiro workers.

API orchestrators and model-backed helper paths use:

- `GOOGLE_API_KEY` or `GEMINI_API_KEY` for Gemini API orchestrator and summarizer paths;
- `ANTHROPIC_API_KEY` for Claude API orchestrator paths;
- `OLLAMA_BASE_URL` for local Ollama orchestrator paths, defaulting to `http://localhost:11434/v1` when Ollama is selected.

Benchmark upload and online service write paths use:

- `KODO_BENCH_URL` and `KODO_BENCH_TOKEN` for benchmark result uploads;
- `KODO_BENCH_ADMIN_TOKEN` for benchmark admin token management;
- Google Cloud application credentials for Firestore and GCS access when running benchmark online service data endpoints;
- optional `KODO_BENCH_PROJECT` and `KODO_BENCH_BUCKET` to point the service at a non-production GCP project and bucket.

Trace upload is disabled unless `KODO_TRACE_UPLOAD=1`. When enabled, it writes to GCS and Firestore and needs Google Cloud credentials.

GitHub Actions assistant workflows use repository secrets `CI_ASSISTANT_URL` and `CI_ASSISTANT_SECRET`; `GITHUB_TOKEN` is provided by GitHub Actions. These are CI integration secrets, not local product-test requirements.

Never write secret values into this file, logs, committed config, or test fixtures.

## Constraints

Default Hive tests should use mocked backends and local-only health checks. They must not exercise live LLM providers, subscription-backed CLI agents, or paid APIs unless the run is explicitly marked live and assigned a sandbox account.

Do not point routine tests at the production benchmark infrastructure: GCP project `covenance-469421`, bucket `kodo-bench`, or the public Cloud Run benchmark service. Use local `/api/health` for container health, or a dedicated sandbox GCP project and bucket for write-path tests.

Do not set `KODO_TRACE_UPLOAD=1` in routine tests. That path uploads run archives to GCS and writes Firestore metadata.

Do not run `kodo` against a developer's real working tree for automated tests. Use a disposable fixture project, set `KODO_RUNS_DIR`, and avoid inherited `~/.kodo` teams/config unless the test is specifically about user config.

Do not use real third-party accounts, real customer repositories, production tokens, or admin benchmark tokens for automated tests.

Do not run destructive cleanup commands against `~/.kodo`, benchmark storage, GCP resources, or user projects unless the path was created by the current test run and is known to be disposable.
