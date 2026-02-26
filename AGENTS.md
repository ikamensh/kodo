# Rules for writing code in this repo

## End-to-end test scenarios (mocked)

Primary user workflows and how to verify them with mocks (no API keys or real backends):

| Scenario | Script | What it verifies |
|----------|--------|------------------|
| **Non-interactive run** | `scripts/smoke_test_cli.py` | CLI starts, mocked orchestrator runs one cycle, exits 0, JSON output |
| **Full mocked run** | `scripts/run_cli_mocked.py` | Goal + team + launch flow, run_start/cycle_end/run_end emitted |
| **Resume interrupted run** | `scripts/smoke_test_resume.py` | Creates incomplete run, `kodo --resume` finds it, passes correct ResumeState to orchestrator |
| **Improve mode** | `scripts/smoke_test_improve.py` | Project type detection (app vs library), 6-stage improve plan, orchestrator receives plan |
| **Interactive flow** | `scripts/smoke_test_interactive.py` | Goal input, team/orchestrator/model selection, refine skip, confirmation, launch |
| **Improve full run** | `scripts/run_improve_mocked.py` | `kodo --improve` on buggy_project, plan built, cycle completes |

**Log viewer (browser):** `python -m kodo.viewer <run.jsonl>` or `--serve --port 8080`. `scripts/verify_viewer_browser.py` uses Playwright (optional dep) to verify embedded data, stats bar, timeline, expand button.

## Boundary condition tests (final status)

| Test | File | Status | Notes |
|------|------|--------|-------|
| **BC1** emit survives disk write failure | `test_log_adversarial.py` | PASS | log.emit catches OSError |
| **BC2** agent returns on hanging session | `test_subprocess_adversarial.py` | XFAIL | Thread leak: worker thread persists after timeout |
| **BC3** malformed JSON (Cursor, Gemini, Codex) | `test_malformed_json.py` | PASS | Sessions coerce result/response/msg to str |
| **BC4** worktree edge cases (4 tests) | `test_worktree.py` | PASS | Non-git, cleanup, nonexistent, locked files |
| **H1** max() on empty parallel_results | `test_stage2_integration.py` | PASS | Asserts ValueError raised |
| **M6** RunStats.record_agent thread-safety | `test_stage2_integration.py` | PASS | No data loss observed (race rare) |

Full suite: `uv run pytest tests/` — 464 passed, 1 xfailed (BC2).

## Rule A.0

User has insight, and final decision. But also user can miss detail and might have wrong ideas. If you think what user is asking
is not the best solution or misses something, first stop and ask, giving your reasons and why you believe user is asking for a wrong thing.

## Rule A.1

I want you to create less code but focus on higher quality and relevance. Try to strike a balance
of completing task and yet not overwhelming me with redundant code I will have to review. If you need some bit to experiment or progress, write it, use it, delete it.

Remember to run tests / demo runs of new features so that I get tested code to review.

## Rule A.2

Don't hedge against exceptions that you have no control over. 
I prefer a clear exception to some surprising default value and convoluted code with special cases

## Rule 3: Useful, maintanable tests

Mostly write two kind of tests: 
1) test the executability the code, cover for simple syntax mistakes;
2) Verify properties of building blocks. Like you would test addition to be commutative,
come up with properties that should hold for classes, functions, etc. and verify them in most general way.
Examples: invariance under saving/loading to disk; a summary makes content shorter than it was; similarity is high for a equal inputs; 
output of "prepare_inputs" can be processed by the next function in the pipeline (e.g. when the data content matters).
3) regression tests: when we find a bug, its usually worth it to reflect what kind of test should have caught it in the first place. Add such a test, and look for similar cases in the codebase, look for catching bugs of same kind.

Add doctests to describe what is tested, why its expected to pass.
Tests should ideally be built to survive refactorings and changes of constants with
minimal changes.

## Rule 4: don't deprecate, keep clean and simple instead
This is not a legacy codebase. This is a clean code base.
When you want to deprecate something, delete it instead. 

## Rule 5
When you think you are done, inspect your code:
1) consider if simpler architecture would solve it better? suggest better design.
2) code: can code be simplified while not changing what it does? fix right away.

## Rule 6
Prompting LLMs: I've noticed that as an AI software assistant, you make the same mistakes when writing prompts that I want you to avoid:
- Unjustified usage of 'MUST, IMPORTANT, ALWAYS' in caps - if you write something in prompt it is important already. You dilute other parts of prompt by doing this.
- Duplicating instructions in system and user messages. User message specifies the current task. Prefer using user message for most info. System message tells what style and priorities are, and maybe the most immutable rules. Often system message can be omitted.

Avoid the mistakes above and keep your prompts to be shorter and focused. 
Rewrite a prompt a few times until you get to the best prompt you can get.

# B. Practical choices

- docstrings: if there is nothing useful to be said about a variable, return etc on top of its variable name and type, don't put it into docstring - it only creates bulk in the code.
   therefore after finishing a docstring go back and review if its helpful. delete boilerplate parts.
- when creating scripts, by default I don't need CLI interface - I just need a simple python file where all "input" variables are grouped together for easy overview and modification.
- Python: Don't use relative imports, use absolute imports
- when resolving circular imports, its in general acceptable to put imports inside a function
- LLM Prompts 1: Don't use excessive highlighting like IMPORTANT for ordinary requirements
- LLM Prompts 2: Don't duplicate task in both system and user message. System message sets the style and core priorities of LLM, and rules + definitions. User message gives tasks.
- Don't call scripts test_x or x_test, as this provokes pycharm to run them as pytest and not a script.
- git add new files you intend to become part of the repo, otherwise I might miss them and deploy incomplete software.
