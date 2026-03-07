# Audit Report: `kodo/orchestrators/base.py` and Related Modules

**Date:** 2026-03-07
**Scope:** RunResult.finished correctness, stage transition logic, error recovery patterns, legacy done signal handling, adaptive mode transitions, config propagation

---

## Part 1: RunResult.finished, Stage Transitions, Error Recovery (Stage 1)

### 1. RunResult.finished Property

**File:** `kodo/orchestrators/types.py:110-123`

**Verdict: FIXED** — now uses `max(stage_results, key=stage_index)` instead of `stage_results[-1]`.

Previously P2 issues:
- ~~Non-deterministic `stage_results` ordering from `as_completed`~~ → Fixed: `RunResult.finished` uses highest stage_index.
- ~~`result.stage_results` unsorted after parallel collection~~ → Fixed: `_run_parallel_group` now sorts the tail of `result.stage_results` (base.py:778-784).
- ~~No break on complete parallel group failure~~ → Fixed: `_run_staged` breaks when no parallel stages completed (base.py:650-655).
- ~~Advisor "done" with empty stage_results~~ → Fixed: synthetic `StageResult(stage_index=0, finished=True)` appended (base.py:463-472).

### 2. Error Recovery — No New Issues

- ApiOrchestrator retry logic: sound (3-attempt, exponential backoff, proper non-retryable classification).
- Fatal error patterns: expanded to include `Rate limit exceeded`, `Model not found`, `model_not_available`, `Permission denied` (agent_tools.py:16-19).
- Cost tracking for retried API attempts: still under-reports (P3, deferred).

---

## Part 2: Legacy Done Signal Handling (Stage 2)

### 3. Signal Architecture

**Two modes** controlled by `CycleConfig.done_mode` (default: `"new"`):

| Mode | Tools | Verification | Terminal Value |
|------|-------|-------------|----------------|
| `"legacy"` | `done(summary, success)` | Full regex gate via `verify_done()` → `_check_passed()` | `"legacy"` |
| `"new"` | `goal_done`, `end_cycle`, `raise_issue` | None (agent expected to verify first) | `"goal_done"`, `"end_cycle"`, `"raise_issue"` |

### 4. `_check_passed` Regex — Correctness Review

**File:** `kodo/orchestrators/verification.py:55-83`

**Verdict: Well-hardened, one edge case noted**

The regex pipeline:
1. Negation check: `"NOT ALL CHECKS PASS"` / `"NOT MINOR ISSUES FIXED"` → immediate reject
2. Strip fenced code blocks (triple backtick delimited, `re.DOTALL`)
3. Strip inline code (single backtick delimited)
4. Strip single/double-quoted strings containing the signal
5. Authoritative position check: signal must be at line/sentence start

### 5. Legacy `handle_done` Verification Loop — No Infinite Loop Risk

**File:** `kodo/orchestrators/verification.py:302-378`

**Key safety**: No loop inside `handle_done`. The orchestrator's cycle loop re-invokes `done()` only if the agent decides to call it again. The agent is bounded by `max_exchanges` (per-cycle turn limit). Nudge loops (`_MAX_NUDGES=3`) provide a hard cap.

**Termination guarantee**: `max_exchanges` (from `run_sync(usage_limits=...)` in API orchestrator) or `max_turns` (in Claude Code SDK options) ensures no cycle runs forever, even if the agent enters a done→reject→done loop.

---

## Part 3: Adaptive Mode Transitions (Stage 2)

### 6. Advisor Decision Validation

**File:** `kodo/orchestrators/advisor.py`

**Verdict: Robust — no infinite loop paths**

| Exit condition | Mechanism |
|---------------|-----------|
| Advisor says "done" | `break` at base.py:473 |
| Cycles exhausted | `remaining_cycles <= 0` in while condition (base.py:433) |
| Stage limit | `completed_count >= advisor.max_stages` in while condition (base.py:433) |
| Stage failure | `break` at base.py:504 |
| Advisor crash | `break` at base.py:448 (new try/except — Finding 10) |

All five are checked every iteration.

**Pydantic agent** (`Advisor.assess()`): `AdvisorDecision.action` is `Literal["next_stage", "done"]`. Pydantic-ai validates structured output — invalid action raises `ValidationError`.

**Session advisor** (`SessionAdvisor.assess()`): 4-level JSON parse fallback chain. Unparseable → `AdvisorDecision(action="done", summary="Could not parse...")`. Session error → same "done" fallback. Both are conservative: they halt rather than continue.

### 7. Advisor vs Agent Done Signal — No Conflict Possible

The advisor and DoneSignal operate at different scopes and never run concurrently. The advisor runs *between* stages; DoneSignal is created fresh *inside* each `cycle()` call. They cannot conflict because they never coexist in the same execution phase.

---

## Part 4: Findings Detail

### Finding 8 — `_RE_FENCED_CODE` greediness (P3, acceptable)

**File:** `kodo/orchestrators/verification.py:40`

The pattern `r"```.*?```"` with `re.DOTALL` uses non-greedy `.*?`, which is correct for matching the *nearest* closing fence. If a report contains an unclosed triple-backtick (e.g., truncated output), the regex matches nothing — the signal phrase inside the unclosed block would remain and could produce a false positive. In practice, LLM verifier output is well-formed, so this is theoretical.

**Status:** Acceptable. No fix needed.

### Finding 9 — No double-set guard on DoneSignal (P3, acceptable)

**File:** `kodo/orchestrators/tools.py:117-155`

Nothing prevents an agent from calling `goal_done()` and then `raise_issue()` in the same cycle (or vice versa). Last writer wins — the second call overwrites `terminal`, `summary`, and `success`. The nudge loops check `done_signal.called` but don't re-check the terminal value.

In practice, tool calls are sequential and all done tools return a clear terminal message ("Goal accepted." / "Issue raised. Run stopped."). An LLM would have to actively ignore the response to call a second done tool. Risk: negligible.

**Status:** Acceptable. No fix needed.

### Finding 10 — `advisor.assess()` crash handling (P3, FIXED)

**File:** `kodo/orchestrators/base.py:434-448`

Previously, `advisor.assess()` was called inside the `_run_adaptive` while loop without try/except. If `assess()` raised (API timeout, Pydantic validation error, network failure), the exception propagated unhandled to `run()`.

**Fix applied:** `advisor.assess()` is now wrapped in try/except with a graceful log message and `break`:
```python
try:
    decision = advisor.assess(
        goal, plan, stage_summaries, completed_count,
    )
except Exception as exc:
    log.tprint(
        f"[advisor] assess() failed: {exc} — "
        f"stopping after {_plural(completed_count, 'stage')}",
    )
    log.emit("advisor_assess_error", error=str(exc), completed_stages=completed_count)
    break
```

This matches the conservative fallback pattern used by `SessionAdvisor` for parse failures — halt rather than continue.

### Finding 11 — `CycleConfig` field loss in `_run_parallel_group` thread-pool path (P2, FIXED)

**File:** `kodo/orchestrators/base.py:746-756`

Previously, the `CycleConfig` passed to parallel stage threads only preserved `verifiers` and `auto_commit`, dropping `browser_testing`, `verification`, `acceptance_criteria`, `effort`, and `done_mode`. This was the same pattern as the sequential fallback bug (Finding 4), but in the thread-pool path.

**Fix applied:** All fields are now preserved:
```python
config=CycleConfig(
    browser_testing=config.browser_testing,
    verifiers=config.verifiers,
    auto_commit=(stage.persist_changes and config.auto_commit),
    verification=config.verification,
    acceptance_criteria=config.acceptance_criteria,
    effort=config.effort,
    done_mode=config.done_mode,
),
```

**Impact of prior bug:** Parallel stages always ran with `effort="standard"` and `done_mode="new"` regardless of the run-level configuration. If a run was configured for `done_mode="legacy"` (with verification gate) or `effort="high"`, parallel stages silently ignored those settings.

### Finding 12 — `done_mode` propagation in `_run_one_stage` (P2, FIXED)

**File:** `kodo/orchestrators/base.py:353-361`

Previously, `_run_one_stage` built a `stage_config` that did not include `done_mode`. This meant all stages defaulted to `done_mode="new"` regardless of what was set at the run level.

**Fix applied:** `stage_config` now includes `done_mode=config.done_mode` (line 360):
```python
stage_config = CycleConfig(
    browser_testing=stage.browser_testing,
    verifiers=config.verifiers,
    auto_commit=config.auto_commit,
    verification=stage.verification,
    acceptance_criteria=stage.acceptance_criteria or None,
    effort=config.effort,
    done_mode=config.done_mode,
)
```

**Impact of prior bug:** If a run was configured with `done_mode="legacy"` (which enables the verification gate with `_check_passed` regex), stages would silently use `done_mode="new"` (no verification gate), bypassing the quality check entirely.

### Finding 13 — `run()` missing `config` parameter (P3, FIXED)

**File:** `kodo/orchestrators/base.py:131-180`

Previously, `run()` only accepted individual config kwargs (`verifiers`, `auto_commit`, `effort`) and always constructed a `CycleConfig` from them, losing any other fields that callers might want to set (like `done_mode`).

**Fix applied:** `run()` now accepts an optional `config: CycleConfig | None = None` parameter (line 145). When provided, it's used directly; otherwise, a `CycleConfig` is constructed from the individual kwargs for backward compatibility:
```python
if config is not None:
    run_config = config
else:
    run_config = CycleConfig(
        verifiers=verifiers, auto_commit=auto_commit, effort=effort,
    )
```

Existing callers in `_launch.py` continue to use the individual kwargs and are unaffected.

---

## Part 5: Resource Integrity & Signal Propagation in Parallel Stages (Stage 4)

### Finding 14 — `raise_issue` does not stop other parallel stages or the run (P2, FIXED)

**Files:** `types.py:80`, `base.py:375,628-636,674-682`, `stage_planning.py:25`

**Verdict: FIXED**

Previously, `StageResult` had no `success` field — both `goal_done` and `raise_issue` produced `finished=True`, making them indistinguishable. The waterfall continued after a fatal `raise_issue`.

**Fix applied:**
1. `StageResult` now has `success: bool = True` (types.py:80).
2. `_run_one_stage` propagates `cycle_result.success` into `stage_res.success` (base.py:375).
3. `_handle_stage_crash` sets `success=False` (stage_planning.py:25).
4. Sequential waterfall (base.py:628-636): checks `stage_res.finished and not stage_res.success` → breaks with "stage raised an issue".
5. Parallel waterfall (base.py:674-682): checks `any(pr.finished and not pr.success for pr in parallel_results)` → breaks with "parallel stage raised an issue".

**Remaining limitation:** Other parallel threads still run to completion — `ThreadPoolExecutor` has no cancellation mechanism. Acceptable: compute is subscription-covered and threads complete naturally.

### Finding 15 — Worktree cleanup in `finally` block is correct (P4, acceptable)

**File:** `base.py:709-773`, `parallel.py:176-264`

**Verdict: Correct**

`cleanup_and_merge_worktrees()` is called in a `finally` block at base.py:770-773. The cleanup ordering is correct:
1. Commit `persist_changes` worktrees (safety net)
2. Close cloned sessions (terminates running agents)
3. Remove worktrees (directories + metadata, keeping branches for merge)
4. Merge `persist_changes` branches, then delete branches in their own `finally`

This handles: normal completion, exceptions, `FatalAgentError`, and first `KeyboardInterrupt`.

**Residual risk:** Double `KeyboardInterrupt` (user hits Ctrl+C twice rapidly while `ThreadPoolExecutor.__exit__` runs `shutdown(wait=True)`) could prevent the outer `finally` from completing. This is a Python-level limitation — `finally` blocks are not re-entrant under repeated signals. Acceptable.

### Finding 16 — No startup cleanup of abandoned kodo worktrees (P3, FIXED)

**Files:** `git_ops.py:117-309` (new function), `base.py:733` (call site)

**Verdict: FIXED**

Previously, if a run was killed hard (SIGKILL, OOM-killer, power failure, double `KeyboardInterrupt`), the following persisted:
- Worktree directories: `/tmp/kodo-stage-*-<uuid>` (full project copies)
- Git branches: `kodo-stage-*-<uuid>` in the project repo
- Git worktree metadata: `.git/worktrees/kodo-*` entries

**Fix applied:**

1. **New function:** `cleanup_stale_worktrees(project_dir)` in `git_ops.py`
   - Uses `git worktree list --porcelain` to enumerate all worktrees
   - Identifies stale worktrees by:
     - Having "kodo-" in the directory name
     - Being older than 6 hours (based on mtime)
   - Removes stale worktrees using `git worktree remove --force`
   - Deletes corresponding branches that start with "kodo-"
   - Runs `git worktree prune` to clean up metadata
   - Calls `_cleanup_orphaned_kodo_branches()` to remove orphaned branches
   - Wrapped in try/except to never crash the main flow

2. **Orphaned branch cleanup:** `_cleanup_orphaned_kodo_branches()` in `git_ops.py`
   - Finds `kodo-*` branches that have no associated worktree directory
   - These appear when cleanup is interrupted after worktree removal but before branch deletion
   - Only deletes branches not in the active worktree set

3. **Call site:** `_run_parallel_group()` in `base.py:733`
   - Called right before `create_stage_worktrees()` as designed
   - Runs only when parallel stages are created, avoiding irrelevant execution

**Test coverage:** 7 new tests added to `test_git_ops.py`, covering:
- No-op when no stale worktrees exist
- Removal of worktrees older than 6 hours
- Preservation of recent worktrees
- Graceful handling of git command failures
- Never crashes on unexpected errors
- Skips non-kodo worktrees
- Handles missing worktree paths
- Orphaned branch cleanup scenarios

### Finding 17 — `FatalAgentError` propagation in parallel stages is correct (P4, VERIFIED)

**Files:** `agent_tools.py:99-101`, `base.py:764-766`, `stage_planning.py:13-26`

**Verdict: VERIFIED — Correct as-is**

`FatalAgentError` raised inside a parallel stage's `handle_agent_call()` propagates through `future.result()` → caught by `_handle_stage_crash()` → `StageResult(finished=False, success=False, summary="Stage crashed: ...")`. Other stages continue independently.

This is correct behavior: each parallel stage has its own cloned team (independent agent sessions). A fatal error in one team's workers doesn't imply others are affected.

**Note:** `dead_workers` is per-`cycle()` (created fresh per tool build), so worker death tracking doesn't persist across cycles within a stage. This is by design.

**Additional improvement (Finding 14):** `StageResult` now has `success=False` on crashes, which propagates to stop the waterfall after parallel completion (base.py:674-682).

### Finding 18 — SIGINT suppression during worktree cleanup (P3, FIXED)

**Files:** `parallel.py:176-267` (new context manager and refactored cleanup)

**Verdict: FIXED — Resource integrity hardened**

**Previous behavior:** `cleanup_and_merge_worktrees()` was called in a `finally` block, which protected against normal exceptions and single `KeyboardInterrupt`. However, if a user pressed Ctrl+C twice rapidly during cleanup, the second SIGINT could interrupt the cleanup mid-operation, leaving:
- Partially merged branches
- Undeleted worktree directories
- Agent sessions still open

**Fix applied:**

1. **New context manager:** `_suppress_keyboard_interrupt()` in `parallel.py:176-213`
   - Defers SIGINT by replacing the signal handler during the `with` block
   - If SIGINT arrives, sets a flag but doesn't raise immediately
   - Re-raises the signal on exit after cleanup completes
   - Falls back to no-op on non-Unix platforms or non-main-thread contexts

2. **Refactored cleanup:** `cleanup_and_merge_worktrees()` now wraps the actual cleanup logic:
   ```python
   with _suppress_keyboard_interrupt():
       _cleanup_and_merge_worktrees_inner(...)
   ```

3. **Hardened exception handling:** Changed all `except Exception` to `except BaseException` in:
   - Worktree commit loop (parallel.py:214)
   - Session close loop (parallel.py:226)
   - Worktree removal loop (parallel.py:237)
   - Branch merge loop (parallel.py:253)
   - Branch cleanup finally (parallel.py:266)

**Behavior:**
- First Ctrl+C during parallel execution: interrupts threads gracefully, cleanup runs protected
- Second Ctrl+C during cleanup: deferred until cleanup completes, then re-raised
- Post-cleanup: SIGINT propagates normally to caller, run exits with KeyboardInterrupt
- Non-Unix/non-main-thread: cleanup proceeds unprotected (status quo ante)

**Result:** No worktrees or branches are orphaned by double Ctrl+C. User can still abort, but only after resources are cleaned up.

---

## Summary Table

| # | Finding | Severity | Status | Location |
|---|---------|----------|--------|----------|
| 1 | Parallel group partial failure invisible | P2 | **FIXED** (Stage 1) | base.py:650-655 |
| 2 | Advisor "done" with empty stage_results | P3 | **FIXED** (Stage 1) | base.py:463-472 |
| 3 | No break on complete parallel group failure | P2 | **FIXED** (Stage 1) | base.py:650-655 |
| 4 | Sequential fallback drops CycleConfig fields | P3 | **FIXED** (Stage 1) | parallel.py:145-153 |
| 5 | Non-deterministic stage_results ordering | P2 | **FIXED** (Stage 1) | base.py:778-784 |
| 6 | Retried API attempts not tracked in cost | P3 | Deferred | api.py:279-284 |
| 7 | Fatal error patterns too narrow | P3 | **FIXED** (Stage 1) | agent_tools.py:16-19 |
| 8 | `_RE_FENCED_CODE` greedy across unclosed fences | P3 | Acceptable | verification.py:40 |
| 9 | No double-set guard on DoneSignal | P3 | Acceptable | tools.py:117-155 |
| 10 | `advisor.assess()` crash unhandled in adaptive loop | P3 | **FIXED** (Stage 2) | base.py:434-448 |
| 11 | Thread-pool `CycleConfig` drops fields | P2 | **FIXED** (Stage 2) | base.py:746-756 |
| 12 | `done_mode` not propagated in `_run_one_stage` | P2 | **FIXED** (Stage 2) | base.py:353-361 |
| 13 | `run()` missing `config` parameter | P3 | **FIXED** (Stage 2) | base.py:131-180 |
| 14 | `raise_issue` doesn't stop parallel stages or run | P2 | **FIXED** (Stage 3) | types.py:80, base.py:375,628-636,674-682 |
| 15 | Worktree cleanup `finally` block | P4 | **VERIFIED** (Stage 3) | base.py:770-773 |
| 16 | No startup cleanup of abandoned worktrees | P3 | **FIXED** (Stage 3) | git_ops.py:117-309, base.py:733 |
| 17 | `FatalAgentError` propagation in parallel | P4 | **VERIFIED** (Stage 3) | agent_tools.py:99, base.py:764-766, stage_planning.py:25 |
| 18 | SIGINT suppression during worktree cleanup | P3 | **FIXED** (Stage 3) | parallel.py:176-267 |

---

## Stage 3 Completion Summary

**Focus:** Resource integrity and signal propagation in parallel execution

**Work completed:**
1. ✅ Added `StageResult.success` field to distinguish `goal_done` from `raise_issue`
2. ✅ Implemented waterfall halting on `raise_issue` (sequential and parallel paths)
3. ✅ Verified `FatalAgentError` propagation and crash handling
4. ✅ Verified worktree cleanup `finally` block correctness
5. ✅ Implemented `cleanup_stale_worktrees()` with 6-hour staleness threshold
6. ✅ Implemented orphaned branch cleanup (`_cleanup_orphaned_kodo_branches()`)
7. ✅ Added SIGINT suppression during cleanup to prevent resource leaks
8. ✅ Hardened exception handling in cleanup (Exception → BaseException)
9. ✅ Added 7 comprehensive tests for worktree/branch cleanup

**Test coverage:** +7 tests in `test_git_ops.py` (41 total, all passing)

**Resource integrity improvements:**
- Stale worktrees auto-cleaned before parallel execution starts
- Orphaned branches removed (interrupted cleanup scenarios)
- Double Ctrl+C cannot interrupt cleanup (SIGINT deferred)
- All cleanup paths catch `BaseException` (includes `KeyboardInterrupt`, `SystemExit`)

---

**Open items (2):**
- Finding 6: Retried API cost tracking (P3, deferred — virtual costs under subscription)
- Finding 8: Unclosed code-fence edge case in `_check_passed` (P3, theoretical)

**All P2 and P3 actionable issues are resolved.** No blocking issues remain.
