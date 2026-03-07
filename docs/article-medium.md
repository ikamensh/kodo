# Multi-Agent AI Coding: What Happens When You Let Agents Review Each Other's Work

You spend your day writing, reviewing, and debugging code. When the day ends, the work stops. I wanted to see what would happen if I could hand off an architecture doc, go to sleep, and come back to something usable in the morning.

## The Self-Review Problem

Single-agent AI coding has a blind spot: the agent can't reliably review its own work. It writes a function, decides it's correct, and moves on. The bugs it introduces are often invisible to it—the same context window that created the errors is the same one evaluating them.

In practice, you know who catches those bugs? A different developer looking at the code with fresh eyes.

So I tried giving the AI fresh eyes, too.

## What kodo Does

[kodo](https://github.com/ikamen/kodo) is a multi-agent system where a lightweight orchestrator (Gemini Flash) directs a team of Claude Code agents—workers, an architect, and testers—through multiple work cycles with independent verification.

You set a goal, step away, and come back to code that's been built, reviewed, rejected, rebuilt, re-reviewed, and finally accepted.

Here's a condensed log from a real run—building an auto-solving meta-optimizer with 4 new algorithms and 73 tests:

```text
🔍 orchestrator → architect
           "Survey the codebase — Solver interface, existing algorithms"
📋 architect reports back — found 3 bugs in existing code

⚡ orchestrator dispatches 3 agents in parallel:
           → architect:     "Analyze how to implement DE and PSO"
           → worker_fast:   "Implement TabuSearch and EDA"
           → worker_smart:  "Build autosolve() — concurrent portfolio"

🏁 orchestrator → done("autosolve complete")
           → tester: ✅
           → architect: "ProcessPool is never closed — resource leak" ❌
           REJECTED

🔧 orchestrator → worker_smart: "Fix the resource leak"
           → done() → architect: "class-variable contamination" ❌
           REJECTED

           ... 7 more verification rounds ...

🎉 ALL CHECKS PASS — 4 new algorithms, 73 tests
```

The architect caught 9 rounds of bugs—resource leaks, class variable contamination, state mutation, crossover edge cases—each subtler than the last. A single-agent session would likely have shipped with several of these.

## A Longer Run: Game Engine from a Design Doc

I wanted to see how kodo handled something larger. I had an architecture document for a Python game engine—component systems, physics, a rendering pipeline—but no code.

I pointed kodo at the design doc and went to sleep.

Six hours later: a working game engine. Not a skeleton—an actual engine that was built, tested, reviewed, torn apart by the architect, rebuilt, and verified.

The interesting part is how it handles context degradation. A single session would have hit context limits or lost track of the architecture partway through. With kodo, when the worker drifted off-spec at hour 4, the architect caught it with fresh context and sent it back.

## `--improve`: Automated QA Audit

This is the feature I use the most:

```bash
kodo --improve ./my-project
```

No goal to write. Just point it at your codebase.

It runs a 4-stage analysis:

1. **Baseline & Static Analysis** — Runs your existing tests, linters, and type checkers, noting failures and coverage gaps.
2. **Happy Path Testing** — Actually *uses* your software the way a user would, writing integration tests for uncovered workflows.
3. **Adversarial Testing** — Throws garbage inputs, race conditions, and edge cases at your code to see what breaks.
4. **Fix & Report** — Auto-fixes the safe stuff (committing it for you) and writes a report splitting everything into "auto-fixed" vs. "needs your decision."

Stages 2 and 3 run in parallel in isolated Git worktrees so the agents don't interfere with each other. You come back to a clean commit of auto-fixes and a markdown report with file and line references for everything else.

## How It Works

The core idea is role separation:

- **Orchestrator** (Gemini Flash) — Makes decisions, manages workflow, delegates tasks.
- **Workers** (Claude Code) — Write the actual code.
- **Architect** (Claude Code) — Reviews the implementation against the original requirements.
- **Testers** (Claude Code) — Independently verify the code works.

When the orchestrator calls `done()`, the architect and tester receive the code with *zero shared context* from the building phase. They review it cold. If they find issues, the orchestrator sends the worker back to fix them. This loop continues until the reviewers independently accept.

Because the reviewer doesn't know what the worker *intended*, it can only judge what the worker actually *built*.

## Cost

Running a multi-agent system purely on API calls gets expensive—a big overnight run could easily cost $40–50. kodo routes the heavy lifting through your existing Claude Code subscription (the Max tier), so the only incremental cost is the orchestrator on Gemini Flash.

A typical 3-hour session costs about **$0.10–0.15**.

## Getting Started

```bash
# Install
uv tool install git+https://github.com/ikamen/kodo

# Automated QA audit
kodo --improve ./my-project

# Build from a design doc
kodo --goal-file my-feature-design.md ./my-project

# Overnight run
nohup kodo --goal-file feature.md --cycles 10 ./project > run.log 2>&1 &
```

Requirements: Claude Code installed (`npm install -g @anthropic-ai/claude-code`) and a Gemini API key for the orchestrator.

## Free Tier

No Claude Code Max subscription? You can still run kodo for free.

Install the [Gemini CLI](https://github.com/google-gemini/gemini-cli), grab a free API key from [Google AI Studio](https://aistudio.google.com/apikey) (no credit card), and kodo auto-detects it as your backend. It builds a team using Gemini models—Flash for fast workers/testers, Pro for smart workers and the architect.

```bash
export GOOGLE_API_KEY="your-key"
uv tool install git+https://github.com/ikamen/kodo
kodo --improve ./my-project
```

The free tier has rate limits (50 Pro requests/day), so large overnight runs will hit those. But for quick runs and `--improve` scans, it works well. See [docs/free-tier.md](free-tier.md) for the full setup guide.

## When to Use It (and When Not To)

kodo isn't a replacement for sitting at the keyboard. If you're exploring, learning, or iterating on something fuzzy—stay in the loop and use Claude Code directly.

It's useful for the other kind of work: when you know what you want, can write the spec, and just need execution with independent review. The overnight/unattended angle is the main draw.

---

*[kodo](https://github.com/ikamen/kodo) is open source (MIT). Try `--improve`, file issues when it breaks.*
