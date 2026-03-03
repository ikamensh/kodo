# Improve Report — Kodo Audit 2026-03-02 / 2026-03-03

Run: `20260302_202833` (initial audit) + `20260303_052307` (triage & fixes)

---

## Auto-fixed

All items below have been implemented, tested, and verified (599 tests passing).

### Batch 1 — Immediate Priority (Items 1–5)

| # | ID | File:Line | Description |
|---|-----|-----------|-------------|
| 1 | F1 | `kodo/orchestrators/claude_code.py:106-108` | **Billing race condition** — moved `ClaudeSDKClient(options=options)` inside `with anthropic_env_lock:` block to prevent API key restoration race causing unexpected billing |
| 2 | F1 | `kodo/orchestrators/base.py:1238-1268` | **Merge conflict resolver crash** — wrapped session creation and query in try-except; returns `False` on error to trigger merge abort; added `finally` cleanup for session |
| 3 | F4 | `kodo/orchestrators/base.py:283` | **Malformed git directive** — added missing `\n` to Co-Authored-By line that caused string concatenation: `"<noreply@github.com>Do NOT push..."` |
| 4 | F2 | `kodo/sessions/base.py:111-126` | **Spawn exception handling** — wrapped `_spawn()` in all 3 subprocess sessions (Cursor, Codex, GeminiCli) with try-except for `FileNotFoundError`/`PermissionError`/`OSError`; returns `QueryResult(is_error=True)` |
| 5 | F2 | `kodo/agent.py:242-246` | **Agent.close() resource leak** — wrapped `terminate()` in try/finally to ensure `session.close()` always runs even if terminate raises |

### Batch 2 — Short-Term Robustness (Items 6–11)

| # | ID | File:Line | Description |
|---|-----|-----------|-------------|
| 6 | F7 | `kodo/orchestrators/base.py:389-394` | **Auto-commit blocking** — update DoneSignal before auto-commit; run `_auto_commit()` in background daemon thread so MCP handler isn't blocked |
| 7 | F4 | `kodo/orchestrators/base.py:658-667` | **McpServerContext escalation** — added retry stop with `call_soon_threadsafe(loop.stop)` and 2s second join; force-close loop regardless; emit `mcp_server_thread_stuck` event |
| 8 | F2 | `kodo/orchestrators/base.py:666-675` | **VerificationState test coverage** — added `test_verification_state_resets_between_cycles()` to validate per-cycle state isolation |
| 9 | F3 | `kodo/sessions/base.py:200-234` | **Zombie process logging** — added `log.emit("zombie_process", ...)` and user-visible warning when process survives 4-tier escalation; added `proc.wait(timeout=2)` reap after kill |
| 10 | F1 | `kodo/sessions/claude.py:62,223-249` | **ClaudeSession close() TOCTOU race** — added `threading.Lock()` (`_close_lock`) for atomic check-and-set of `_closed` flag; prevents double-disconnect from concurrent close calls |
| 11 | F3 | `kodo/agent.py:211-215` | **Agent._run_timed() exception broadening** — grace-period `future.result(timeout=0.5)` now catches `Exception` (not just `FuturesTimeoutError`); prevents raw exception propagation bypassing timeout result |

### Batch 3 — Short-Term Robustness Continued (Items 12–15)

| # | ID | File:Line | Description |
|---|-----|-----------|-------------|
| 12 | F6 | `kodo/log.py:337-351` | **Stats table throttle race** — moved `_last_table_time` check-and-set inside existing `with _lock:` block to prevent duplicate table prints from parallel threads |
| 13 | F5 | `kodo/summarizer.py:131-139` | **Summarize-after-shutdown guard** — added `if self._executor is None: return` inside lock to safely discard fire-and-forget jobs after shutdown |
| 14 | F10 | `kodo/summarizer.py:185-191` | **Shutdown idempotency** — swap executor to `None` under lock, then shutdown outside lock; safe to call multiple times without `RuntimeError` |
| 15 | F3 | `kodo/cli/_launch.py:186-187` | **Silent exception logging** — replaced bare `except/pass` in worktree cleanup (and 2 other locations) with `logging.debug()` for operational visibility |

### Batch 4 — Long-Term Quality (Items 16–20)

| # | ID | File:Line | Description |
|---|-----|-----------|-------------|
| 16 | F4 | `kodo/orchestrators/base.py:1672-1695` | **Empty GoalPlan warning** — added explicit check for `GoalPlan(stages=[])` with `log.tprint` warning and `log.emit("run_empty_plan_fallback")` before falling through to single-goal |
| 17 | F12 | `kodo/orchestrators/base.py:458-462` | **DoneSignal atomic snapshot** — added `snapshot()` method returning `(called, summary, success)` tuple under lock for consistent multi-property reads |
| 18 | F11 | `kodo/sessions/claude.py:230-247` | **Daemon thread abandonment logging** — added `log.emit("session_close_warning", reason="thread_still_alive_after_kill")` when thread survives full escalation |
| 19 | F5 | `kodo/cli/_intake.py:200,215` | **Exception chaining** — added `from err` to `raise ValueError(...)` in stage index parsing for proper debugging context |
| 20 | F2 | `pyproject.toml` | **CVE patches** — pinned pillow >=12.1.1, pypdf >=6.7.4, wheel >=0.46.2; documented pip CVE-2025-8869/CVE-2026-1703 as build-env only |

### Batch 5 — High-Priority Prior Items (Items 21–26)

| # | ID | File:Line | Description |
|---|-----|-----------|-------------|
| 21 | BL-1 | `kodo/cli/_launch.py` | **skip_intake validation** — `--skip-intake`/`--auto-refine` without `--goal` now raises error instead of silently entering interactive mode (hangs in CI) |
| 22 | BL-3 | `kodo/cli/_launch.py` | **Resume team config** — `launch_resume()` now applies team config overrides (`max_exchanges`, `max_cycles`) like `launch_run()` does |
| 23 | BL-4 | `kodo/log.py` | **parse_run PermissionError** — wrapped `iterdir()` and config reads with try-except for `PermissionError`; individual run failures no longer crash `kodo runs` |
| 24 | BL-8 | `kodo/factory.py` | **TEAMS stale after cache clear** — `TEAMS` dict now regenerated dynamically when `clear_backend_cache()` is called, not computed only at import time |
| 25 | BL-9 | `kodo/cli/_params.py` | **Invalid team name handling** — unhandled `KeyError` on invalid saved team name now falls back gracefully to team selection instead of crashing |
| 26 | BL-10 | `kodo/cli/_subcommands.py` | **int() safety in teams add/edit** — wrapped `int(input(...))` with try-except `ValueError`; shows friendly error message instead of stack trace |

### Batch 6 — Prior Items from Triage (Items 27–29)

| # | ID | File:Line | Description |
|---|-----|-----------|-------------|
| 27 | F2 | `kodo/orchestrators/base.py:707-714` | **_check_passed() false positive hardening** — strip fenced code blocks, inline code, single-quoted and double-quoted strings before checking for pass signals; prevents LLM quoting from triggering false acceptance. Added 5 regression tests. |
| 28 | P11 | `kodo/cli/_main.py:226-233` | **stdout redirection encapsulation** — replaced manual `sys.stdout = sys.stderr` with `contextlib.ExitStack` + `json_output_redirect()` context manager for proper cleanup |
| 29 | BL-7 | `kodo/orchestrators/cursor_cli.py:103`, `codex_cli.py:103` | **Popen encoding safety** — added `encoding="utf-8", errors="replace"` to all `subprocess.Popen()` calls to prevent `UnicodeDecodeError` on non-UTF8 subprocess output |

### Batch 7 — Medium-Priority Items (Items 30–35)

| # | ID | File:Line | Description |
|---|-----|-----------|-------------|
| 30 | BL-16 | `kodo/sessions/cursor.py:100-128` | **CursorSession token reporting** — added extraction of `input_tokens`/`output_tokens` from cursor-agent JSON stream (`usage` dict and `token_count` events); updates `SessionStats` and returns tokens in `QueryResult` |
| 31 | BL-17 | `kodo/sessions/claude.py:37,103-109` | **ClaudeSession session_timeout_s enforcement** — added `session_timeout_s` parameter to `__init__`; renamed `_QUERY_TIMEOUT` to `_DEFAULT_QUERY_TIMEOUT`; added `_query_timeout` property that honors per-agent override; updated `make_session()` to pass through; updated `clone()` to preserve. Added 2 new tests. |
| 32 | BL-18 | `kodo/cli/_subcommands.py` | **Teams auto overwrite confirmation** — added interactive `Overwrite? [y/N]` prompt before overwriting existing team config files in `teams auto` command |
| 33 | BL-19 | `kodo/team_config.py:183-204`, `kodo/cli/_launch.py:230,409` | **Verifier reference validation** — added `validate_verifiers()` function that removes references to unavailable agents (with warning); applied in both `launch_run()` and `launch_resume()` |
| 34 | BL-20 | `kodo/cli/_ui.py:21-23`, `kodo/orchestrators/base.py:25-27`, + 4 files | **Grammar: singular/plural** — added `_plural(n, word)` helper; fixed all 13 instances of hardcoded "(s)" patterns across `_ui.py`, `base.py`, `_launch.py`, `_intake.py`, `_subcommands.py` (e.g., "1 cycles" -> "1 cycle") |
| 35 | BL-27 | `kodo/cli/_params.py:250-277`, `kodo/cli/_launch.py` | **Legacy config migration persistence** — `"mode"` -> `"team"` key rename now saved back to disk (best-effort) in both `_load_or_select_params()` and `launch_resume()` via `_atomic_write()`; prevents repeated migration on every run |

### Prior Auto-fixed Items (from previous runs)

Items already resolved before this audit cycle began:

| # | ID | File:Line | Description |
|---|-----|-----------|-------------|
| — | F1 | `kodo/cli/_main.py:409` | f-string without placeholders — removed extraneous `f` prefix |
| — | F2 | `kodo/cli/_subcommands.py:15` | Unused import `CODEX_DEFAULT` — removed |
| — | F3 | `kodo/cli/_subcommands.py:88` | Unused import `open_viewer` — removed |
| — | F4 | `kodo/factory.py:28` | Unused import `CODEX_O3` — removed |
| — | F5 | `kodo/factory.py:591` | Unused import `load_team_config` — removed |
| — | F10 | Multiple files (338 hits) | COM812 missing trailing commas — fixed via `ruff --fix` |
| — | F11 | `pyproject.toml` | Pillow CVE-2026-25990 — pinned >=12.1.1 |
| — | F14 | `pyproject.toml` | pypdf CVE-2026-28351 — pinned >=6.7.4 |
| — | F15 | `pyproject.toml:2` | wheel CVE-2026-24049 — pinned >=0.46.2 |
| — | P9 | `kodo/user_config.py` | Path.home() RuntimeError — better error message for headless environments |
| — | P12 | `kodo/orchestrators/api.py` | Explicit `continue` for 529 fallback — clarity in control flow |
| — | P14 | `kodo/cli/_intake.py` | Empty stages — validation for missing descriptions |
| — | P21 | `kodo/cli/_main.py` | Banner/error ordering in JSON mode — stderr for metadata |

---

## Test Coverage

**Total tests: 599 passing** (3 skipped, 1 xfailed)

New tests added during this audit:

| Test | File | Covers |
|------|------|--------|
| `test_code_block_pass_rejected` | `tests/test_regression.py` | _check_passed ignores signals inside fenced code blocks |
| `test_inline_code_pass_rejected` | `tests/test_regression.py` | _check_passed ignores signals inside inline code |
| `test_double_quoted_pass_rejected` | `tests/test_regression.py` | _check_passed ignores signals inside double-quoted strings |
| `test_pass_after_period_accepted` | `tests/test_regression.py` | _check_passed accepts signals after sentence-ending punctuation |
| `test_pass_on_own_line_accepted` | `tests/test_regression.py` | _check_passed accepts signals on standalone lines |
| `test_session_timeout_s_overrides_default` | `tests/test_security_fixes.py` | ClaudeSession._query_timeout honors session_timeout_s override |
| `test_default_query_timeout_when_no_override` | `tests/test_security_fixes.py` | ClaudeSession._query_timeout uses _DEFAULT_QUERY_TIMEOUT when no override |
| 52 tests in `test_error_paths.py` | `tests/test_error_paths.py` | Error handling, edge cases, security validation |
| 14 tests in `test_concurrency_audit.py` | `tests/test_concurrency_audit.py` | Thread-safety, race conditions, stress tests |

---

## Needs decision

| # | ID | File / Area | Description | Suggested fix |
|---|-----|-------------|-------------|---------------|
| 1 | P7/33 | `~/.kodo/runs/*.jsonl` | Sensitive data in run logs | Security decision: evaluate encryption vs directory permissions (currently 0o700). Encryption would break resume. Revisit if multi-user needs arise. |
| 2 | P13/39 | `kodo/summarizer.py` | Truncation limits (500/300 chars) | Evaluate if limits are too aggressive for complex runs; consider configurable limits. |
| 3 | P15/41 | Core modules | Low coverage core modules | Schedule for future testing pass; this audit added 7 new targeted tests covering major gaps. |
| 4 | P16/42 | CLI | Missing automated tests for interactive flows | Future work; consider pexpect for interactive CLI testing. |
| 5 | F6 | `kodo/sessions/claude.py:147,180-182` | `CLAUDECODE` env var permanently removed | Appears intentional for nested session support. Document in docstring or restore after use. |
| 6 | F1 | `kodo/agent.py:193` | `BaseException` catch in daemon worker thread | Intentional for daemon thread communication. Add comment documenting intent. |
| 7 | F8 | `kodo/log.py:235` | `log.init()` reassigns `_run_stats` — cached references break | Document "don't cache" or change lifecycle to reset-in-place instead of reassign. |
| 8 | F9 | `kodo/log.py:261-276` | `log.emit()` performs file I/O while holding `_lock` | Trade-off: build record under lock, write outside lock. Needs careful error handling. |
| 9 | BL-2/52 | `kodo/cli/_launch.py` | `--resume <id>` ignores project_dir mismatch | Decide if cross-project resume is a feature or bug. |
| 10 | BL-11/61 | `kodo/log.py` | Race condition with concurrent kodo instances | Module-level globals can collide. Decide on instance isolation strategy. |
| 11 | BL-12/62 | `kodo/orchestrators/base.py` | Parallel worktree fallback to shared dir | Runs sequentially now. Decide if parallel worktree support should be re-enabled with proper isolation. |
| 12 | BL-13/63 | `kodo/orchestrators/base.py` | MCP server crash not surfaced to caller | `__exit__` joins thread but doesn't check `self._exc`. Decide on error propagation strategy. |
| 13 | BL-29/79 | `kodo/log.py` | `list_runs`/`parse_run` no PermissionError on `iterdir()` | Partially fixed (item 23); decide on error-recovery granularity for remaining cases. |
| 14 | Prior/88 | `kodo/orchestrators/base.py` | Monolith decomposition (~1900 lines) | Significant refactoring effort with regression risk. Needs dedicated planning session. |

---

## Skipped by triage

| # | ID | Finding | Reason |
|---|-----|---------|--------|
| 1 | F6 | S101 `assert` usage (949 hits) | Intentional for developer-focused tool; defer until mypy/TypeGuard added. |
| 2 | F7 | ANN001 missing arg type annotations (599 hits) | Project-wide style; defer until mypy added to CI. |
| 3 | F8 | T201 `print()` in library code (428 hits) | CLI tool; prints are intentional user output. |
| 4 | F9 | ANN201 missing return type annotations (419 hits) | Project-wide style; defer until mypy added to CI. |
| 5 | F5/20 | httpx.AsyncClient usage | Verified properly closed via AsyncClient context or explicit close. |
| 6 | F6/21 | log.py syscall overhead | Performance nit; not a resource leak or bug. |
| 7 | F3/26 | RunStats.record_agent() bucket reassignment | Theoretical issue with bucket overwrite; no practical impact in current usage. |
| 8 | P4/30 | Imports after load_dotenv | Intentional; handled via `per-file-ignores` in pyproject.toml. |
| 9 | P5/31 | scripts/*.py imports after sys.path | Intentional script setup; suppressed by ruff per-file-ignores. |
| 10 | P10/36 | log.py parse_run auto_commit | Already evaluated; loads from config. No issue. |
| 11 | P17/43 | Logging: redact sensitive information flag | Feature request; low priority given 0o700 perms and local-only use. |
| 12 | P18/44 | Subcommands: refactor to subparsers | Low urgency; not worth the churn right now. |
| 13 | P19/45 | RunStats.record_agent() non-atomic | Verified thread-safe via module-level `_lock`. |
| 14 | P23/49 | Subprocess timeout visibility | Already addressed by `session_timeout_s` and error hints. |
| 15 | P24/50 | ClaudeSession `_env_lock` held during blocking I/O | Verified intentional and correctly scoped; released before `connect()`. |
| 16 | BL-6/56 | Conflict resolution wall-clock timeout | `max_turns=30` provides bounded execution. Wall-clock timeout adds complexity for marginal benefit. |
| 17 | BL-14/64 | `kodo runs /nonexistent/dir` says "No runs found" | Minor UX nit; message is not incorrect. Low priority. |
| 18 | BL-15/65 | saga/mission shown as duplicate teams | Backward-compat aliases with same descriptions. Cosmetic; users adapt quickly. |
| 19 | BL-21/71 | `chrome`/`fallback_model` silently dropped for non-Claude | Params accepted but ignored. Documenting limitations is sufficient. |
| 20 | BL-28/78 | `user_config.py` no documentation of supported keys | Documentation task, not a code fix. Address when writing user docs. |
| 21 | S3-F2/85 | SubprocessSession._drain UnicodeDecodeError | Fix (`encoding="utf-8", errors="replace"`) already applied in prior run. Verified still in place. |

### Dropped items (obsolete / irrelevant)

| # | ID | Finding | Reason |
|---|-----|---------|--------|
| 1 | P1/27 | kodo/runner.py dead code | File deleted from source tree; only build artifacts remain. |
| 2 | P2/28 | kodo/cli.py + kodo/intake.py duplication | Both files restructured into `kodo/cli/` subpackage; originals no longer exist. |
| 3 | P3/29 | load_user_config() cache | `lru_cache(maxsize=1)` is intentional; `clear_user_config_cache()` exists for tests. |
| 4 | BL-22/72 | Resume of completed run allowed | Edge case; resuming a completed run is harmless. Not worth guarding. |
| 5 | BL-23/73 | Run ID second-granularity collision | Extremely unlikely; sub-second precision would be over-engineering. |
| 6 | BL-24/74 | `_try_auto_fix_team` fragile return | `_fail` always calls `sys.exit()`; safety return is a non-bug fix. |
| 7 | BL-25/75 | API orchestrator uses Opus for summarization | Subscription-covered; cost is virtual per kodo's value proposition. |
| 8 | BL-26/76 | Hardcoded model strings bypass models.py | Cursor-specific choices, not part of kodo model abstraction. Intentional. |
| 9 | BL-30/80 | Non-atomic log writes | Parser already skips bad lines. Event loss on crash is acceptable for a local dev tool. |
| 10 | S1-F8/81 | Missing `[build-system]` in pyproject.toml | Already added in prior run. |
| 11 | S2-F2/82 | ClaudeSession.close() dangling thread | Covered by TOCTOU race fix (item 10 above). |
| 12 | S2-F6/83 | SubprocessSession.terminate() zombie | Covered by zombie process logging (item 9 above). |
| 13 | S3-F1/84 | _check_passed docstring contradicts implementation | Already fixed in prior run. |
| 14 | S3-F4/86 | parse_run overly sensitive to missing cli_args | `cli_args` now optional in parser. Resolved. |
| 15 | S3-F5/87 | `_validate_improve_plan` lacks stage index validation | Stage indices are cosmetic; validated elsewhere. No functional impact. |

---

## Summary Statistics

| Category | Count |
|----------|-------|
| **Total fixes applied** | 35 |
| **New tests added** | 73 (7 targeted + 52 error paths + 14 concurrency) |
| **Total test count** | 599 (passing) |
| **Needs decision** | 14 |
| **Skipped** | 21 |
| **Dropped** | 15 |
| **Prior auto-fixed** | 13 |
| **Files modified** | ~25 |
