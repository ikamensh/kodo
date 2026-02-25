# Static Analysis Sweep — Core Modules

**Scope:** `kodo/cli`, `kodo/orchestrators`, `kodo/sessions`, `kodo/factory.py`  
**Date:** 2025-02-24

---

## 1. Known Bugs (from xfailed tests)

These are documented in `tests/test_stage2_integration.py`:

| ID | Location | Description |
|----|----------|-------------|
| **M4** | `kodo/log.py:422` | `evt["summary"]` — cycle_end event without `summary` key raises `KeyError`. Makes runs un-resumable when log is malformed. Fix: use `evt.get("summary", "")`. |
| **M11** | `kodo/cli/_launch.py:70-84` | `_format_json_output(result=None, error=None)` — accesses `result.finished` when `result` is None, causing `AttributeError`. Callers (`_emit_json_and_exit`) currently always pass a result from `launch_run`, but the function has no guard. Fix: add `if result is None` branch. |
| **M12** | `kodo/cli/_intake.py:141` | `if not index` — treats `index=0` as falsy, so stage 0 is skipped. Fix: use `if index is None` or `if index is None or not name or ...`. |
| **M6** | `kodo/log.py:108-129` | `RunStats.record_agent()` — non-atomic `+=` on `_AgentStats` fields. Under concurrent calls (e.g. parallel stages), data can be lost. Fix: use a lock or atomic operations. |
| **H1** | `kodo/orchestrators/base.py:1416` | `max(len(r.cycles) for r in parallel_results)` — raises `ValueError` when `parallel_results` is empty. Edge case when all parallel stages fail before appending. Fix: `max((len(r.cycles) for r in parallel_results), default=0)` or guard. |

**M5** (snapshot omits RunStats) — test passes; appears fixed.

---

## 2. Subprocess Error Handling

### 2.1 Sessions — no timeout on blocking reads

| File:Line | Issue |
|-----------|-------|
| **kodo/sessions/base.py:106-110** | `subprocess.Popen` — no timeout. If the child hangs, `proc.stdout` iteration and `proc.wait()` block indefinitely. |
| **kodo/sessions/base.py:134** | `proc.wait()` — no timeout. Process could hang forever. |
| **kodo/sessions/gemini_cli.py:81** | `proc.stdout.read()` — blocks until EOF. No timeout; long-running or stuck gemini process can hang the caller. |
| **kodo/sessions/codex.py:99-164** | Iterates `for line in proc.stdout` — blocking. No overall timeout. |
| **kodo/sessions/cursor.py:83-104** | Same pattern. |

**Note:** Agent-level timeouts (e.g. `timeout_s` in `Agent`) call `session.terminate()` from another thread, which sends SIGTERM. That helps but doesn't cover the case where the subprocess ignores signals or is stuck in kernel wait.

### 2.2 Orchestrators — Popen without timeout

| File:Line | Issue |
|-----------|-------|
| **kodo/orchestrators/codex_cli.py:98-131** | `subprocess.Popen` for MCP bridge. `proc.wait()` at 131 has no timeout. `proc.stderr.read()` after wait can block if stderr buffer is large. |
| **kodo/orchestrators/cursor_cli.py:124-149** | Same pattern. |

### 2.3 Subprocess calls with adequate handling

| File:Line | Notes |
|-----------|-------|
| **kodo/factory.py:147-168** | `subprocess.run` with `timeout=15`. Catches `FileNotFoundError`, `TimeoutExpired`, `OSError`. |
| **kodo/cli/_subcommands.py:93-103** | `subprocess.run` with `timeout=10`. Catches `TimeoutExpired`, `OSError`. |
| **kodo/orchestrators/base.py:757-764** | `subprocess.run` with `check=True` — raises on failure; caller (`create_worktree`) is used in try/except at 1346. |
| **kodo/orchestrators/base.py:771-790** | `remove_worktree` — `subprocess.run` without `check=True`; returncode checked for worktree remove; branch delete and rmtree are best-effort. |

---

## 3. Hardcoded Secrets & Input Sanitisation

### 3.1 Secrets

| File:Line | Finding |
|-----------|---------|
| **kodo/summarizer.py:67-69** | API key in URL query: `?key={api_key}`. Risk if URL is logged. Prefer `Authorization` header. |
| **kodo/factory.py:89-96** | API keys from `os.environ` only. No hardcoded secrets. |
| **kodo/cli/_subcommands.py:121-122** | `_masked()` shows first 4 and last 4 chars. Reasonable for display. |

### 3.2 Input / Injection

| File:Line | Finding |
|-----------|---------|
| **kodo/sessions/base.py:106** | `subprocess.Popen(cmd, ...)` — `cmd` is list; no `shell=True`. Safe. |
| **kodo/sessions/codex.py:56-79** | `prompt`, `project_dir`, `model` passed as separate argv elements. No shell. Safe. |
| **kodo/sessions/cursor.py:51-66** | Same. Safe. |
| **kodo/sessions/gemini_cli.py:55-66** | Same. Safe. |
| **kodo/cli/_intake.py:102, 231** | `input()` — user input used as goal/chat. Passed to session as prompt text, not as shell args. Low risk. |
| **kodo/cli/_subcommands.py:396** | `path.write_text(json.dumps(config, indent=2))` — config from questionary; no user path injection in `_teams_dir()`. |

**Verdict:** No command injection. Subprocess calls use list argv and avoid `shell=True`.

---

## 4. Performance & Resource Leaks

### 4.1 Potential hot-spots

| File:Line | Issue |
|-----------|-------|
| **kodo/log.py:152** | `_run_stats` — global singleton. `record_agent` called on every agent run. Under parallel stages, lock contention (M6) or lost updates. |
| **kodo/sessions/base.py:114-121** | `_drain()` — appends up to 10k stderr lines. Bounded; acceptable. |
| **kodo/orchestrators/base.py:1351** | `ThreadPoolExecutor` — one thread per parallel stage. Bounded by stage count. |

### 4.2 Resource leaks

| File:Line | Issue |
|-----------|-------|
| **kodo/orchestrators/base.py:1338-1350** | Worktree creation — on exception, `worktrees` may be partially populated. `finally` at 1398 cleans up. |
| **kodo/orchestrators/base.py:1404-1411** | `remove_worktree` in finally — on exception, logs and continues. Temp dirs should be removed. |
| **kodo/sessions/base.py:86** | `_process` — set in `_spawn`, cleared in `terminate()`. If `query()` raises before `_wait`, process may be left running. Subclasses (codex, cursor, gemini) call `_wait` which does `proc.wait()`, so process is reaped. If `_spawn` raises (e.g. FileNotFoundError), no process is created. |

### 4.3 File handles

| File:Line | Notes |
|-----------|-------|
| **kodo/log.py:406** | `with open(log_file)` — context manager ensures close. |
| **kodo/cli/_launch.py:240** | `read_text` — no explicit close; Path.read_text closes. |

---

## 5. Dead Code & Obvious Bugs

### 5.1 Dead / unreachable code

None identified in the scoped modules.

### 5.2 Additional robustness issues

| File:Line | Issue |
|-----------|-------|
| **kodo/log.py:430-434** | `stage_end` — `evt["summary"]` at 434. If `finished` is True but `summary` is missing, `KeyError`. Same pattern as M4. Fix: `evt.get("summary", "")`. |
| **kodo/log.py:427** | `evt["stage_index"]` — could raise if key missing. Less likely than summary. |
| **kodo/cli/_launch.py:52-60** | `_emit_json_and_exit` — assumes `result` is not None. Current callers always pass a result. Defensive check would align with M11 fix. |
| **kodo/orchestrators/base.py:785-790** | `subprocess.run(["git", "branch", "-D", branch_name])` — no `check=True`. Branch delete failure is silent. Intentional best-effort cleanup. |

---

## 6. Summary

| Category | Count |
|----------|-------|
| Known bugs (xfail) | 5 (M4, M11, M12, M6, H1) |
| Subprocess timeout gaps | 6 locations |
| Secret / injection risks | 1 (API key in URL) |
| Log parsing KeyError risks | 2 (cycle_end, stage_end) |
| Thread-safety | 1 (RunStats) |

**Recommended fixes (priority):**

1. **M4, stage_end** — Use `.get("summary", "")` in `log.py` for `cycle_end` and `stage_end`.
2. **M11** — Add `if result is None` guard in `_format_json_output`.
3. **M12** — Change `if not index` to `if index is None` in `_parse_goal_plan`.
4. **H1** — Guard `max()` with `default=0` or empty check.
5. **M6** — Add lock in `RunStats.record_agent` for concurrent safety.
6. **Sessions** — Consider `subprocess.run` with timeout, or a wrapper that enforces timeouts on Popen-based sessions.
