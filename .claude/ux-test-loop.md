# UX & Integration Test Loop — Work Instructions

## Purpose

Test kodo **as a real user would**, not with mocks. Run actual CLI commands,
inspect output, check UX quality, find bugs and limitations. Think like a
user who just installed kodo and is trying to get things done.

## Persistence

**Read at start of every iteration:**
- `.claude/ux-test-loop.md` — this file (instructions + test list)
- `.claude/ux-test-progress.md` — tracks what's done, findings, bugs

**Update `.claude/ux-test-progress.md` at end of every iteration.**

## Rules

- Do **ONE test group** per iteration (pick next uncompleted from progress file)
- Actually run `uv run kodo <args>` commands — no mocking
- For commands that would start a real LLM run, use `--debug` mode or
  set a short timeout. Only use `--debug --yes --skip-intake` for full runs.
- Capture stdout, stderr, and exit code for each command
- For each test, write down:
  - **PASS/FAIL** — did it work as expected?
  - **UX notes** — is the output clear? confusing? too verbose? missing info?
  - **Bugs** — anything broken, misleading, or inconsistent
  - **Simplification ideas** — could this feature be simpler or removed?
- If you find a bug, file it in progress with reproduction steps
- Do NOT fix bugs during this loop — only find and document them
- Timeout: if a command hangs for >30s, kill it and note it as a bug

**Every 5th iteration:** Reflection — review all findings, prioritize bugs,
look for patterns (e.g. "error messages are consistently bad"), write summary.

## Fix Phase

After completing **all test groups (T1-T15)**, switch to fix mode:

1. Review the full bug list in progress file
2. For each bug marked P0 or P1 — if the fix is **obvious and safe** (clear
   root cause, small change, no architectural risk), implement it immediately.
   Run tests after each fix (`uv run pytest -q`). Commit each fix separately.
3. Skip bugs that need design decisions, user input, or large refactors —
   leave those documented for the user.
4. After all obvious fixes are done, **re-run the failed test groups** to
   verify fixes. Update progress with PASS/FAIL status changes.
5. If re-testing reveals new bugs, document them but don't start a second
   fix cycle — report to the user.

The goal is: find bugs → fix the easy ones → verify the fixes → hand off
the rest. Don't spend time on subjective UX preferences or debatable changes.

---

## Test Groups

### T1: Help, version, and first impressions
**Perspective:** New user just installed kodo, exploring what it does.

1. `kodo --help` — Is the help text clear? Can a new user understand what kodo does?
   Does it explain the basic workflow? Are flag descriptions helpful?
2. `kodo --version` — Does it print version cleanly?
3. `kodo` (no args, with stdin closed/redirected to avoid blocking) — What happens
   when user just types `kodo`? Is the interactive prompt welcoming or confusing?
4. **UX audit:** Read the help text critically. Are there flags that seem redundant?
   Flags with confusing names? Missing examples?

### T2: Backend and team discovery
**Perspective:** User wants to know what backends are available before running.

1. `kodo backends` — Does it show installed backends? Are missing ones clear?
   Is the API key status useful?
2. `kodo teams` — Does it list built-in teams? Is the output readable?
   Can user understand which team to pick?
3. `kodo teams` with a custom team in `~/.kodo/teams/` — Does it show up?
4. **UX audit:** Is the terminology consistent? ("backend" vs "orchestrator" vs
   "session" — does the user need to understand all three?)

### T3: Run listing and log viewing
**Perspective:** User wants to see past runs and debug failures.

1. `kodo runs` — Does it show runs? Is the table format readable?
   What if there are no runs?
2. `kodo runs /some/project` — Does project filtering work?
3. `kodo logs` — Does the log viewer launch? Is it useful?
4. **UX audit:** Can the user find logs for a specific run easily?
   Is the run ID format human-friendly?

### T4: Debug mode full run
**Perspective:** User runs their first task with mocked backends.

1. `kodo --debug --goal "Create a hello world script" --yes --skip-intake --project <tmpdir>`
   — Does it complete? What does the output look like? Is progress visible?
2. Check the run directory — are config.json, goal.md, log.jsonl all created?
3. Check `kodo runs` after — does the run appear?
4. **UX audit:** Is the debug output helpful for understanding how kodo works?
   Could a developer use `--debug` to test their team configs?

### T5: Flag conflicts and error messages
**Perspective:** User makes mistakes, tries invalid flag combinations.

1. `kodo --goal "X" --improve` — Is the error message clear?
2. `kodo --goal "X" --goal-file f.md` — Clear error?
3. `kodo --goal ""` — What happens with empty goal?
4. `kodo --goal "X" --exchanges -1` — Negative exchanges?
5. `kodo --goal "X" --cycles 0` — Zero cycles?
6. `kodo --resume --goal "X"` — Resume + goal conflict?
7. `kodo --goal "X" --team nonexistent` — Invalid team?
8. `kodo --goal-file /nonexistent` — Missing goal file?
9. **UX audit:** Are error messages helpful? Do they suggest corrections?
   Do they exit with proper codes?

### T6: Goal file and goal input variations
**Perspective:** User provides goals in different ways.

1. `kodo --goal-file <file>` with a valid markdown file
2. `kodo --goal-file <file>` with a file containing only whitespace
3. `kodo --goal "X" --project /nonexistent` — Invalid project dir?
4. Very long goal (>1000 chars) — Does it get truncated? Where?
5. Unicode goal text — Does it handle emoji/CJK/RTL?
6. **UX audit:** Is `--goal` vs `--goal-file` the right split? Would a single
   `--goal` that auto-detects file paths be simpler?

### T7: JSON mode output
**Perspective:** User building CI/CD integration with kodo.

1. `kodo --json --debug --goal "X" --yes --skip-intake --project <tmpdir>`
   — Is stdout valid JSON? Is stderr clean?
2. Parse the JSON — does it have all documented fields?
3. `kodo --json --goal "" --project <tmpdir>` — Does error produce JSON too?
4. **UX audit:** Is the JSON schema documented anywhere? Is it stable?
   Are exit codes consistent with the JSON status field?

### T8: Resume flow
**Perspective:** User's run was interrupted, they want to continue.

1. Create a run with --debug, interrupt it (--cycles 1), then `kodo --resume`
2. `kodo --resume` with no incomplete runs — Is the error helpful?
3. `kodo --resume bogus_id` — Is the error helpful?
4. **UX audit:** Is resume discoverable? Does kodo suggest it when a run fails?
   Is the output during resume clear about what's being restored?

### T9: Effort levels and auto-commit
**Perspective:** User fine-tunes run behavior.

1. `kodo --effort low --debug --goal "X" --yes --skip-intake --project <tmpdir>`
   — Does effort affect anything visible in debug mode?
2. `kodo --effort max` — Same test, does output differ?
3. `kodo --no-auto-commit --debug --goal "X" --yes --skip-intake --project <tmpdir>`
   — Does it skip commits? (In a git repo vs non-git dir)
4. **UX audit:** Are the effort level names intuitive? Would "quick/normal/thorough"
   be better than "low/standard/high/max"?

### T10: Improve mode
**Perspective:** User wants kodo to find and fix issues in their project.

1. `kodo --improve --debug --yes --project <tmpdir-with-code>` — Does it run?
2. Does it produce a report? Where?
3. `kodo --improve --focus "error handling" --debug --yes --project <tmpdir>`
   — Does focus affect the plan?
4. **UX audit:** Is `--improve` discoverable? Would a user expect `kodo improve`
   (subcommand) rather than `kodo --improve` (flag)?

### T11: Team configuration
**Perspective:** User wants to customize which agents/models kodo uses.

1. Does `kodo teams` show useful info about each team's agents?
2. Test `kodo teams auto` — does it generate viable configs?
3. Is the team JSON format documented somewhere the user can find?
4. **UX audit:** Is the team system too complex for most users?
   Would simpler presets ("fast", "thorough", "budget") suffice?
   Is there a learning cliff?

### T12: Edge cases and stress
**Perspective:** Adversarial usage and boundary conditions.

1. Run kodo from a directory with spaces in the path
2. Run kodo from a non-git directory — does it handle gracefully?
3. Run kodo with `--project` pointing to a symlink
4. `kodo --debug --goal "X" --yes --skip-intake` with ANTHROPIC_API_KEY unset
   — Does debug mode still work without API keys?
5. Ctrl+C during a --debug run — Does it clean up properly?
6. **UX audit:** Are edge case errors graceful or do they produce tracebacks?

### T13: Subcommand UX consistency
**Perspective:** Power user who uses multiple subcommands.

1. `kodo run` vs `kodo runs` — Do both work (singular/plural)?
2. `kodo backend` vs `kodo backends` — Same?
3. `kodo team` vs `kodo teams` — Same?
4. `kodo log` vs `kodo logs` — Same?
5. `kodo help` vs `kodo --help` — Consistent?
6. `kodo nonexistent` — What error for unknown subcommand?
7. **UX audit:** Is the subcommand discovery natural? Would `kodo status`
   be useful? `kodo cancel`? What commands are missing?

### T14: Cross-feature interactions
**Perspective:** User combining features in realistic workflows.

1. Full workflow: `kodo --debug --goal "X" --yes --skip-intake` → check run →
   `kodo runs` → `kodo --resume` → check final state
2. `kodo --json --debug --goal "X" --yes --skip-intake` then pipe to `jq`
3. Multiple sequential runs in same project — does config reuse work?
4. **UX audit:** Is the end-to-end experience smooth? Any rough transitions?
   Missing progress indicators? Unclear what's happening?

### T15: Final reflection
**Perspective:** Step back and synthesize.

1. Review all bugs found across T1-T14
2. Prioritize: P0 (broken), P1 (confusing), P2 (polish)
3. Look for patterns in UX issues
4. List features that seem over-engineered for their value
5. List features that are missing but users would expect
6. Write final UX report with recommendations

---

## Notes

- Use `uv run kodo` to invoke kodo (ensures correct Python environment)
- Create tmp directories for test projects: `mktemp -d`
- For debug runs that might hang, use `timeout 30 uv run kodo ...`
- Always capture exit code: `echo $?` after each command
- Some tests need a git repo: `git init` in tmpdir first
- Be creative — go off script when you notice something odd
