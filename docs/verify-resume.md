# Verify Resume Flow

This document describes how to create and verify a mock interrupted run for testing `kodo --resume`.

## Setup

### 1. Create the mock interrupted run

From the kodo repo root:

```bash
uv run python scripts/create_mock_interrupted_run.py
```

This creates:

- `~/.kodo/runs/interrupted_run/run.jsonl` — JSONL log with `run_init`, `cli_args`, `run_start`, and one `cycle_end` (finished: false)
- `~/.kodo/runs/interrupted_run/goal.md` — Goal text
- `/tmp/kodo_resume_test/` — Project directory (used as `project_dir` in the run)

The script uses the resolved path for the project directory (e.g. `/private/tmp/kodo_resume_test` on macOS) so `find_incomplete_runs` matches correctly.

### 2. Verify the run is discoverable

From the kodo repo root:

```bash
uv run python -c "
from pathlib import Path
from kodo import log
incomplete = log.find_incomplete_runs(Path('/tmp/kodo_resume_test'))
assert len(incomplete) >= 1, 'No incomplete run found'
print('OK: found run', incomplete[0].run_id)
"
```

## Verification steps

### Option A: Resume latest incomplete run (by project dir)

```bash
# From kodo repo
uv run kodo /tmp/kodo_resume_test --resume --yes
```

Or, if kodo is installed globally (`uv tool install .`):

```bash
cd /tmp/kodo_resume_test
kodo --resume --yes
```

Expected: kodo finds the incomplete run, prints the goal and cycle status, then launches the orchestrator to continue. With `--yes`, it skips the confirmation prompt.

### Option B: Resume by run ID

```bash
uv run kodo --resume interrupted_run /tmp/kodo_resume_test --yes
```

Expected: Same as Option A, but targets the specific run `interrupted_run`.

### Option C: List runs

```bash
uv run kodo runs /tmp/kodo_resume_test
```

Expected: Shows the `interrupted_run` with status `cycle 1/5` (or similar).

## Cleanup

To remove the mock run:

```bash
rm -rf ~/.kodo/runs/interrupted_run
rm -rf /tmp/kodo_resume_test
```
