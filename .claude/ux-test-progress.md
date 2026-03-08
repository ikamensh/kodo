# UX Test Progress

## Current State
- **Phase:** FIX PHASE — implementing obvious P0/P1 fixes
- **Completed:** T1-T15 (all test groups)
- **Iteration count:** 15
- **Total bugs found:** 15

## Bugs Found

### BUG-01: `kodo help` fails with argparse error (P1)
- **Repro:** `uv run kodo help`
- **Expected:** Show help text (same as `--help`)
- **Actual:** `kodo: error: unrecognized arguments: help` (exit code 2)
- **Note:** Users commonly type `command help`. Should either work or suggest `--help`.

### BUG-02: Buffered keystrokes during spinner consumed by next prompt (P2)
- **Group:** T1
- **Repro:** In interactive mode, press Enter while "Analyzing goal..." spinner runs.
  The next "Proceed? [Y/n]" prompt consumes the buffered Enter as "Y".
- **Impact:** User can accidentally confirm launch without meaning to.
- **Note:** This is a fundamental issue with sequential input() calls after async work.

### BUG-03: `kodo teams` shows `?` for exchanges/cycles on built-in teams (P2)
- **Group:** T2
- **Repro:** `uv run kodo teams` (before running `teams auto`)
- **Actual:** `? exchanges, ? cycles` — confusing
- **Expected:** Show the actual defaults that will be used (30/5)
- **Root cause:** Built-in team JSON files don't set `max_exchanges`/`max_cycles`. Display code shows `?` for missing values.

### BUG-04: `kimi` backend has no URL in `kodo backends` output (P2)
- **Group:** T2
- **Repro:** `uv run kodo backends` — kimi line shows `not found` with blank URL
- **Expected:** Should show kimi documentation URL

### BUG-05: `kodo teams auto` not idempotent — fails on second run (P1)
- **Group:** T2
- **Repro:** `uv run kodo teams auto` (works first time) → `uv run kodo teams auto` (fails)
- **Actual:** `Error: No built-in team templates found.` (exit code 1)
- **Root cause:** `teams auto` generates user teams in `~/.kodo/teams/` that shadow built-in teams. The `auto` command filters for `source == "built-in"` teams, but user teams override them in `list_available_teams()`.
- **Fix:** Read built-in templates directly from `kodo/defaults/` instead of through `list_available_teams()`.

### BUG-06: `teams auto` defaults differ from interactive defaults (P2)
- **Group:** T2

### BUG-07: `kodo logs` gives no feedback before blocking (P2)
- **Group:** T3

### BUG-10: Inconsistent banner display after errors (P2)
- **Group:** T5
- **Details:** Some errors print the banner after the error message (--resume --goal, --goal-file /nonexistent, whitespace goal file), others don't (--goal "", --exchanges -1, --project /nonexistent). No consistent pattern.

### BUG-11: JSON mode runtime errors go to stderr instead of stdout (P1)
- **Group:** T7
- **Repro:** `uv run kodo --json --debug --goal "X" --yes --skip-intake --project <tmpdir>`
- **Expected:** JSON error on stdout (like early validation errors)
- **Actual:** JSON error on stderr, stdout empty
- **Impact:** CI/CD integrations parsing stdout will miss runtime errors

### BUG-12: `--effort` not shown in launch box (P2)
- **Group:** T9
- **Details:** `--effort low` and `--effort max` produce identical launch boxes with "Exchanges: 30/cycle, 5 cycles". No visible indication effort was applied.
- **Fix:** Add effort level to the launch summary box.

### BUG-13: `--improve --debug` makes real API calls (P1)
- **Group:** T10
- **Repro:** `uv run kodo --improve --debug --yes --project <tmpdir-with-code>`
- **Expected:** Debug mode = no real API calls
- **Actual:** "Planning improvements..." spinner runs for 70+ seconds, making real API calls. `--debug` only mocks the execution phase, not the improve discovery/planning phase.
- **Impact:** Users testing improve mode with `--debug` will incur real API costs and need real API keys.

### BUG-14: `kodo teams --help` doesn't work (P2)
- **Group:** T11
- **Repro:** `uv run kodo teams --help`
- **Actual:** "Error: Unknown teams subcommand: --help" (exit 1)
- **Expected:** Show help for teams subcommand

### BUG-15: `kodo logs` raw traceback when port in use (P1)
- **Group:** T13
- **Repro:** Start `kodo logs`, then start another `kodo logs`
- **Actual:** Raw Python traceback: `OSError: [Errno 98] Address already in use`
- **Expected:** "Port 8080 already in use. Try: kodo logs --port 8081"

### BUG-08: Debug mode crashes — MockSession missing `close()` method (P0)
- **Group:** T4
- **Repro:** `uv run kodo --debug --goal "Create a hello world" --yes --skip-intake --project <tmpdir>`
- **Error:** `AttributeError: 'MockSession' object has no attribute 'close'. Did you mean: 'clone'?`
- **Root cause:** `MockSession` in `kodo/debug.py` doesn't implement `close()`. The Session protocol (base.py:73) requires it. `Agent.close()` calls `self.session.close()`.
- **Fix:** Add `def close(self) -> None: pass` to MockSession.

### BUG-09: Debug mode crashes — mock model output validation fails (P0)
- **Group:** T4
- **Repro:** Same as BUG-08 (this error happens first, then BUG-08 during cleanup)
- **Error:** `pydantic_ai.exceptions.UnexpectedModelBehavior: Exceeded maximum retries (1) for output validation`
- **Root cause:** The `done` tool call from mock model (`build_mock_model` in `debug.py`) has args `{"summary": ..., "success": true}` but pydantic-ai v1.20.0 validation rejects them. The `done` tool's schema may have changed or validation is stricter.
- **Impact:** Debug mode is completely broken — no mock runs can complete.
- **Fix:** Check what pydantic-ai expects for the `done` tool args. May need to update args format or increase `max_result_retries`.
- **Group:** T3
- **Repro:** `uv run kodo logs` — hangs with no output
- **Expected:** Should print "Serving on http://localhost:8080" or similar before blocking
- **Impact:** User doesn't know what's happening, especially on headless/SSH
- **Group:** T2
- **Details:** Auto-generated teams get `max_exchanges=20, max_cycles=1`. Interactive mode defaults to 30/5. Same team name, different behavior.

## UX Issues

### UX-01: Too many interactive prompts before launch (P1)
- **Details:** 7 prompts before anything happens: Team → Orchestrator → Model → Exchanges → Cycles → Refine → Proceed.
- **Impact:** Overwhelming for new users. Most should just press Enter through all.
- **Suggestion:** Use sensible defaults. Only prompt for goal + proceed. Put advanced options behind `--configure` or similar. The `--effort` flag could replace exchanges/cycles entirely.

### UX-02: "Exchanges" and "Cycles" are jargon (P2)
- **Details:** Even with inline explanations ("An exchange = one orchestrator turn"), a new user has no idea what number to pick.
- **Suggestion:** Hide behind `--effort` flag. Low=fewer exchanges/cycles, max=more.

### UX-03: Help text lacks usage examples (P2)
- **Details:** `--help` shows flags but no usage examples like `kodo --goal "Fix login"` or `kodo --improve`.
- **Suggestion:** Add 2-3 examples at the bottom of help text.

### UX-04: `--skip-intake` unclear in help (P2)
- **Details:** "Skip intake interview, use goal as-is" — "intake interview" is not a term explained elsewhere in help. A new user reading `--help` doesn't know what intake means.
- **Suggestion:** "Skip interactive goal refinement" or add context.

### UX-05: `--exchanges` and `--cycles` defaults not shown in help (P2)
- **Details:** Help says "Max exchanges per cycle" and "Max cycles" but doesn't show defaults (30 and 5 respectively). User has to enter interactive mode to discover.

### UX-06: `--auto-refine` vs `--skip-intake` relationship unclear (P2)
- **Details:** Both relate to goal processing but their interaction isn't explained. `--auto-refine` says "surfaces implicit constraints, no conversation" — how is that different from the "Quick refine" interactive option?

### UX-07: Questionary escape codes despite NO_COLOR=1 (P2)
- **Group:** T1
- **Details:** questionary outputs ANSI escape sequences for cursor positioning even with NO_COLOR=1 and FORCE_COLOR=0. Affects piping/automation.
- **Note:** This is a questionary library limitation, not directly kodo's fault.

## Simplification Ideas

### S-01: Collapse interactive prompts
- Team/Orchestrator/Model/Exchanges/Cycles could be a single "effort level" prompt, or entirely defaults-only with `--effort` flag for customization.

### S-02: Effort flag should control exchanges/cycles
- `--effort low` → fewer exchanges/cycles, `--effort max` → more. No need to expose raw numbers to users.

## Iteration Log

### Iteration 1 — T1: Help, version, and first impressions

**Tests run:**

1. `kodo --help` — **PASS** (exit 0, clear output, flags documented)
   - Missing: no usage examples, no defaults for exchanges/cycles
   - Subcommands listed at bottom in custom format (fine)

2. `kodo --version` — **PASS** (prints `kodo 0.4.214`, exit 0, clean)

3. `kodo` (interactive, empty stdin) — **PASS** (prints banner, asks for goal, exits with "Error: No goal provided." exit 1)

4. `kodo` (interactive via pexpect) — **PASS with issues**
   - Banner is nice: owl emoji, version, URL, project path
   - Full prompt sequence: Goal → Team → Orchestrator → Model → Exchanges → Cycles → Refine → Proceed
   - That's 8 prompts — too many for a first-time user
   - "Quick refine" makes a real API call (spinner ~13s) — unexpected during "just exploring"

5. `kodo help` — **FAIL** (BUG-01: argparse error, exit 2)

6. `kodo runs --help` — **PASS** (clean subcommand help)

**UX Audit:**
- The help text is functional but not welcoming — no examples, some jargon
- Interactive mode is thorough but overwhelming — needs a "fast path" for common case
- The safety warning before Proceed is good ("they CAN access any file on your system")
- Banner/branding is clean and professional

### Iteration 2 — T2: Backend and team discovery

**Tests run:**

1. `kodo backends` — **PASS** (exit 0)
   - Clean output: CLI backends with versions/URLs, orchestrator models with status, API keys masked
   - BUG-04: kimi has no URL (blank line)
   - Sections "CLI backends (agents)" vs "Orchestrator models (API)" — terminology may confuse

2. `kodo teams` — **PASS with issues** (exit 0)
   - Shows team details with agent breakdowns, `[ok]`/`[missing]` status
   - BUG-03: Shows `? exchanges, ? cycles` for built-in teams
   - Default `full` team: 3/5 agents require cursor (missing) — will fail for users without cursor

3. `kodo teams auto` — **PASS first time, FAIL second** (BUG-05)
   - First run: generates adapted teams using available backends, saves to ~/.kodo/teams/
   - Second run: "No built-in team templates found" because user teams shadow built-in
   - BUG-06: Auto teams default to 20 exchanges/1 cycle vs interactive 30/5

4. `kodo teams` (after auto) — **PASS**
   - Shows `(user)` label, all agents `[ok]`, exchanges/cycles values populated

**UX Audit:**
- `backends` is useful and clear — good first command for new users
- `teams` output is detailed but may overwhelm — 3/5 agents `[missing]` on default team is discouraging
- `teams auto` is a great feature but not idempotent
- Terminology: "backend" / "orchestrator" / "session" — user shouldn't need to know all three
- `teams auto` hint at bottom of `kodo teams` is helpful

### Iteration 3 — T3: Run listing and log viewing

**Tests run:**

1. `kodo runs` — **PASS** (exit 0)
   - Table format: RUN ID, STATUS, PROJECT, GOAL — readable
   - Run ID is timestamp-based (20260308_211543) — human-friendly
   - Goal truncated with `...` — good

2. `kodo runs /tmp/...` — **PASS** — project filtering works

3. `kodo runs /nonexistent` — **PASS** — "No runs found." exit 0

4. `kodo logs` — **PASS** (blocks, serves HTTP)
   - No output before blocking — no "Serving on http://localhost:8080" message
   - In headless/SSH environment, just hangs silently

5. `kodo logs --help` — **PASS** — clean subcommand help

6. Run directory inspection — **PASS**
   - config.json, goal.md, goal-refined.md, run.jsonl, team.json — all present

**UX Audit:**
- Run listing is clean and useful
- No way to view a specific run's log from CLI without the browser viewer
- `kodo logs` should print the URL before blocking (BUG-07)
- Missing: `kodo runs show <id>` or `kodo runs <id>` for quick CLI log inspection

### Iteration 4 — T4: Debug mode full run

**Tests run:**

1. `kodo --debug --goal "Create a hello world script" --yes --skip-intake --project <tmpdir>` — **FAIL** (BUG-08, BUG-09)
   - Banner and "READY TO LAUNCH" box render nicely
   - Debug letter assignments (A=orchestrator, B=worker_fast, etc.) are clear
   - Mock sessions produce deterministic responses (A1, A2, etc.)
   - Progress table renders after first agent call — good!
   - CRASH after 5 agent calls: pydantic-ai validation error on `done` tool
   - Then MockSession.close() AttributeError during cleanup

2. Run directory check — **PARTIAL**
   - Run was created at ~/.kodo/runs/20260308_212109/ but incomplete due to crash

3. `kodo runs` after crash — not tested (run was created)

**UX Audit:**
- The "READY TO LAUNCH" summary box is excellent UX — shows exactly what will happen
- Debug mode letter assignments are clever and useful
- Progress table with agent stats is great for understanding what's happening
- The crash traceback is raw Python — should be caught and shown as a friendly error
- ANSI color codes visible in output despite NO_COLOR not being set (this was a real terminal run)

### Iteration 5 — T5: Flag conflicts and error messages

**Tests run:**

1. `kodo --goal "X" --improve` — **PASS** (argparse error, exit 2, "not allowed with")
2. `kodo --goal "X" --goal-file f.md` — **PASS** (argparse error, exit 2)
3. `kodo --goal ""` — **PASS** (exit 1, "must not be empty or whitespace-only")
4. `kodo --goal "X" --exchanges -1` — **PASS** (exit 1, "must be a positive integer")
5. `kodo --goal "X" --cycles 0` — **PASS** (exit 1, "must be a positive integer")
6. `kodo --resume --goal "X"` — **PASS** (exit 1, "cannot be used with --goal/--goal-file/--improve")
   - BUG-10: Banner prints AFTER the error message — inconsistent
7. `kodo --goal "X" --team nonexistent` — **PASS** (argparse error, exit 2, shows valid choices)
8. `kodo --goal-file /nonexistent` — **PASS** (exit 1, "Goal file not found")
   - BUG-10: Banner prints AFTER error — same inconsistency

**UX Audit:**
- Error messages are clear and actionable
- Two error patterns: argparse (exit 2, prints full usage) vs custom (exit 1, prints just error)
- Argparse errors are noisy (full usage dump); custom errors are cleaner
- BUG-10: Some errors print banner after error message, others don't — inconsistent

### Iteration 5 — Reflection

**Bug severity summary (so far):**
- P0: BUG-08 (MockSession.close missing), BUG-09 (debug mode validation crash) — debug mode is completely broken
- P1: BUG-01 (`kodo help`), BUG-05 (`teams auto` not idempotent)
- P2: BUG-02 (buffered keystrokes), BUG-03 (? exchanges/cycles), BUG-04 (kimi URL), BUG-06 (auto defaults differ), BUG-07 (logs no feedback), BUG-10 (banner inconsistency)

**Patterns emerging:**
1. **Inconsistent error output format** — some errors show banner, some show usage, some show just the error. No single pattern.
2. **Debug mode is broken** — this blocks T4 tests, T7 (JSON mode), T8 (resume), T9 (effort), T10 (improve), T14 (workflow). Many test groups depend on `--debug`.
3. **Too many interactive prompts** — the 8-prompt interactive sequence is the biggest UX issue for new users.
4. **Terminology overload** — "backend", "orchestrator", "session", "agent", "team" — users need a glossary.

**Priority for fix phase:**
1. Fix BUG-08 + BUG-09 first — unblocks all debug-dependent tests
2. Fix BUG-05 (teams auto idempotent) — easy fix, clear root cause
3. Fix BUG-01 (kodo help) — easy fix
4. Consider BUG-10 (banner consistency) — might be easy

### Iteration 6 — T6: Goal file and goal input variations

**Tests run:**

1. `--goal-file <valid.md>` — **PASS** (reads multiline markdown, shows in launch box)
   - Minor: Goal shows literal `\n` in launch box for multiline content — should show first line
2. `--goal-file <whitespace.md>` — **PASS** (exit 1, "Goal file is empty.")
   - Banner after error (BUG-10 again)
3. `--goal "X" --project /nonexistent` — **PASS** (exit 1, "path does not exist" — no banner here)
4. Very long goal (>1000 chars) — **PASS** (truncated with `...`, accepted)
5. Unicode (CJK + emoji + RTL) — **PASS** (renders correctly in launch box)

**UX Audit:**
- Goal handling is robust — all edge cases handled gracefully
- Multiline goal display in launch box could be improved (show first line, not `\n` literal)
- `--goal` vs `--goal-file` split is reasonable — no change needed

### Iteration 7 — T7: JSON mode output

**Tests run:**

1. `kodo --json --debug --goal "X" --yes --skip-intake` — **FAIL** (crashes per BUG-08/09)
   - Progress output goes to stderr (correct)
   - BUG-11: JSON error output went to stderr instead of stdout. Stdout was empty.
   - Error JSON: `{"status": "error", "error": "'MockSession' object has no attribute 'close'"}`

2. `kodo --json --goal ""` — **PASS** (JSON on stdout, stderr clean)
   - `{"status": "error", "error": "--goal must not be empty or whitespace-only."}`

3. `kodo --json --goal-file /nonexistent` — **PASS** (JSON on stdout, stderr clean)
   - `{"status": "error", "error": "Goal file not found: /nonexistent"}`

**UX Audit:**
- JSON error format is clean: `{"status": "error", "error": "message"}`
- Early validation errors correctly go to stdout; runtime crashes go to stderr (BUG-11)
- Can't test full JSON success output until debug mode is fixed

### Iteration 8 — T8: Resume flow

**Tests run:**

1. `kodo --resume` (with incomplete run from T4 crash) — **PASS**
   - Found the incomplete run, showed: Project, Goal, Cycles completed (0/5)
   - "Resume this run? [Y/n]" — good confirmation prompt
   - Blocked waiting for input (expected without --yes)

2. `kodo --resume` (no incomplete runs) — **PASS** (exit 1)
   - "Error: No incomplete runs found." — clear message
   - Banner after error (BUG-10 pattern again)

3. `kodo --resume bogus_id` — **PASS** (exit 1)
   - "Error: Run not found: bogus_id" — clear, mentions the ID
   - Banner after error (BUG-10)

**UX Audit:**
- Resume is discoverable from the help text
- Error messages are helpful and specific
- Can't test full resume flow until debug mode is fixed (BUG-08/09)
- Banner-after-error inconsistency continues

### Iteration 9 — T9: Effort levels and auto-commit

**Tests run:**

1. `--effort low` — **PASS** (accepted, but launch box shows same 30/5 as default)
2. `--effort max` — **PASS** (accepted, launch box identical to low/standard)
3. `--effort invalid` — **PASS** (argparse error, exit 2, shows valid choices)
4. `--no-auto-commit` — **PASS** (accepted, not shown in launch box)

**UX Audit:**
- BUG-12: `--effort` has no visible effect in the launch box. Both `low` and `max` show identical "Exchanges: 30/cycle, 5 cycles". User has no feedback that effort was applied.
- Effort actually adjusts orchestrator system prompt and worker session effort parameter — but this is invisible to the user.
- `--no-auto-commit` is also invisible in the launch box — no indication it's active.
- The effort level names (low/standard/high/max) are fine — "max" is more natural than "thorough".
- **Suggestion:** The launch box should show effort level and auto-commit status.

### Iteration 10 — T10: Improve mode + Reflection

**Tests run:**

1. `kodo --improve --debug --yes --project <tmpdir-with-code>` — **FAIL** (BUG-13)
   - Shows "Running improve discovery..." then "Planning improvements..." spinner
   - Spinner runs for 70+ seconds making real API calls despite `--debug`
   - Never completed — killed after 70s

**UX Audit:**
- `--improve` is a flag, not a subcommand — users might expect `kodo improve`
- `--focus` only works with `--improve` but help doesn't make this dependency clear
- Can't test improve output quality until BUG-13 is fixed

### Iteration 10 — Reflection (every 5th iteration)

**Bug count: 13 bugs found so far**
- P0: 2 (BUG-08 MockSession.close, BUG-09 mock model validation)
- P1: 4 (BUG-01 kodo help, BUG-05 teams auto idempotent, BUG-11 JSON stderr, BUG-13 improve debug)
- P2: 7 (BUG-02 buffered keys, BUG-03 ? display, BUG-04 kimi URL, BUG-06 auto defaults, BUG-07 logs feedback, BUG-10 banner inconsistency, BUG-12 effort not shown)

**Key patterns:**
1. **Debug mode is fundamentally broken** (BUG-08+09) — blocks testing T4, T7, T8, T9, T14
2. **Error output format inconsistency** — BUG-10 appears repeatedly across T5, T6, T8
3. **--debug doesn't fully mock** — BUG-13 shows improve mode escapes the mock
4. **Interactive vs non-interactive parity** — interactive mode offers settings not available as flags, and vice versa

**Remaining test groups (T11-T15):**
- T11 (teams): Mostly covered in T2, can quickly verify
- T12 (edge cases): Will test spaces in path, non-git, symlinks, Ctrl+C
- T13 (subcommand consistency): Will test singular/plural
- T14 (cross-feature): Blocked by debug mode crash
- T15 (final reflection): Will synthesize everything

**Fix phase priorities (after testing):**
1. BUG-08+09: Fix debug mode (P0, unblocks everything)
2. BUG-05: Fix teams auto idempotent (P1, clear root cause)
3. BUG-01: Fix kodo help (P1, trivial)
4. BUG-11: Fix JSON stderr (P1, probably small change)
5. BUG-13: Skip — needs design decision (what should --improve --debug do?)

### Iteration 11 — T11: Team configuration
- Already covered in T2. Additional finding:
- BUG-14: `kodo teams --help` fails — "Unknown teams subcommand: --help" (exit 1)
- Teams JSON format lives in `kodo/defaults/team-*.json` and `~/.kodo/teams/`. Not documented for users.

### Iteration 12 — T12: Edge cases and stress

1. Spaces in path (via --project) — **PASS** (works, display correct)
2. Non-git directory — **PASS** (accepted without complaint)
3. Symlink — **PASS** (resolves to real path in display)
4. Debug without API keys — **BLOCKED** (debug mode crashes per BUG-08/09)
5. Ctrl+C during debug — **BLOCKED** (same)

- No tracebacks from path edge cases — error handling is solid

### Iteration 13 — T13: Subcommand UX consistency

1. `kodo run` = `kodo runs` — **PASS** (both work)
2. `kodo backend` = `kodo backends` — **PASS** (both work)
3. `kodo team` = `kodo teams` — **PASS** (both work)
4. `kodo log` = `kodo logs` — **PASS** (both launch log server)
   - BUG-15: Raw traceback when port 8080 in use (should catch OSError)
5. `kodo help` — **FAIL** (BUG-01, known)
6. `kodo nonexistent` — argparse error "unrecognized arguments" (not "unknown subcommand")

**UX Audit:**
- Singular/plural aliases are excellent — no user confusion
- `kodo help` is the only missing alias
- `kodo nonexistent` should say "unknown subcommand" instead of argparse noise

### Iteration 14 — T14: Cross-feature interactions

- **Mostly blocked by BUG-08/09** (debug mode crash prevents full workflow test)
- Run listing works across multiple runs — table format is readable
- All failed runs show "cycle 0/5" correctly
- Config reuse can't be tested until debug mode works
- JSON + debug can't complete until debug mode works

### Iteration 15 — T15: Final reflection

**Bug summary (15 bugs):**

| Bug | Priority | Group | Description | Fix Complexity |
|-----|----------|-------|-------------|----------------|
| BUG-08 | P0 | T4 | MockSession missing close() | Trivial (add method) |
| BUG-09 | P0 | T4 | Mock model output validation fails | Medium (debug schema) |
| BUG-01 | P1 | T1 | `kodo help` unrecognized | Easy (add to subcommand map) |
| BUG-05 | P1 | T2 | `teams auto` not idempotent | Easy (read built-in directly) |
| BUG-11 | P1 | T7 | JSON runtime errors on stderr | Medium (error handler) |
| BUG-13 | P1 | T10 | `--improve --debug` makes real API calls | Skip (needs design) |
| BUG-15 | P1 | T13 | `kodo logs` raw traceback on port conflict | Easy (catch OSError) |
| BUG-02 | P2 | T1 | Buffered keystrokes during spinner | Skip (fundamental) |
| BUG-03 | P2 | T2 | `?` for built-in team exchanges/cycles | Easy |
| BUG-04 | P2 | T2 | kimi backend missing URL | Trivial |
| BUG-06 | P2 | T2 | Auto-generated team defaults differ | Skip (needs design) |
| BUG-07 | P2 | T3 | `kodo logs` no feedback before blocking | Easy |
| BUG-10 | P2 | T5 | Banner inconsistency after errors | Medium |
| BUG-12 | P2 | T9 | Effort not shown in launch box | Easy |
| BUG-14 | P2 | T11 | `kodo teams --help` doesn't work | Easy |

**Patterns:**
1. Debug mode is completely broken (P0) — blocks all testing
2. Error output inconsistency (banner, stderr vs stdout) — systemic
3. Subcommand help is incomplete (help, teams --help)
4. Many features work correctly — flag conflicts, goal handling, unicode, path edge cases all solid

**Over-engineered features:**
- The 7-prompt interactive intake for exchanges/cycles/team/orchestrator/model — most users should just get defaults
- Team system complexity — most users will never create custom teams

**Missing features:**
- `kodo status` — check on running/recent runs without full log viewer
- `kodo cancel <run_id>` — cancel a running job
- CLI log viewing (without browser) — `kodo runs show <id>` for quick inspection

**Fix phase plan (implementing now):**
1. BUG-08: Add close() to MockSession — trivial
2. BUG-09: Fix mock model validation — need to investigate pydantic-ai schema
3. BUG-01: Add "help" to subcommand map → show --help
4. BUG-05: Fix teams auto to read built-in templates directly
5. BUG-15: Catch OSError in log server
6. BUG-14: Add --help to teams subcommand parser
