<p align="center">
  <img src="docs/logo.png" width="300">
  <br><br>
  <strong>Building while you sleep.</strong>
  <br><br>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.10+-blue?logo=python&logoColor=white" alt="Python 3.10+"></a>
  <a href="https://github.com/ikamen/kodo/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License"></a>
  <a href="https://docs.anthropic.com/en/docs/claude-code"><img src="https://img.shields.io/badge/Claude_Code-Max-blueviolet?logo=anthropic&logoColor=white" alt="Claude Code"></a>
  <a href="https://cursor.com"><img src="https://img.shields.io/badge/Cursor-supported-orange?logo=cursor&logoColor=white" alt="Cursor"></a>
  <a href="https://github.com/openai/codex"><img src="https://img.shields.io/badge/Codex-supported-green?logo=openai&logoColor=white" alt="OpenAI Codex"></a>
  <a href="https://github.com/google-gemini/gemini-cli"><img src="https://img.shields.io/badge/Gemini_CLI-supported-blue?logo=google&logoColor=white" alt="Gemini CLI"></a>
</p>

---

# 🦉 kodo

Autonomous multi-agent coding that runs overnight on your Claude Code Max subscription. An orchestrator directs Claude Code agents through work cycles with independent verification — so you wake up to tested, reviewed code instead of a stale terminal.

## 🎬 How it works in practice

Real run from [blackopt](https://github.com/ikamen/blackopt) — building an auto-solving meta-optimizer with 4 new algorithms, adaptive scheduling, and 73 tests. **3 hours unattended, 2 cycles, succeeded.**

```
🔍 [00:00] orchestrator → architect
           "Survey the codebase — Solver interface, existing algorithms,
            where to add new ones."
📋 [03:04] architect reports back
           Full architecture survey, found 3 bugs in existing code

🔧 [03:14] orchestrator → worker_smart
           "Fix structural bugs identified by architect"
✅ [11:29] worker_smart: 82 turns of editing. All bugs fixed, tests pass.

⚡ [12:36] orchestrator → architect: "Analyze how to implement DE and PSO"
   [15:22] orchestrator → worker_fast: "Implement TabuSearch and EDA"
   [16:01] orchestrator → worker_smart: "Build autosolve() — concurrent
                          portfolio, adaptive scheduling"

🏁 [35:20] orchestrator → done("autosolve complete, 4 new algorithms")
           → tester:          runs tests ✅
           → tester_browser:  runs tests ✅
           → architect:       "ProcessPool is never closed — resource leak" ❌
           REJECTED

🔧 [45:37] orchestrator → worker_smart: "Fix the resource leak"
           → done() → architect: "class-variable contamination" ❌
           REJECTED

           ... 7 more verification rounds ...
           architect catches: time-slice state mutation, exponential
           offspring, crossover edge case — each progressively more subtle

🎉 [2:59:50] → done() → tester ✅ → tester_browser ✅ → architect ✅
             ACCEPTED — "4 new algorithms, autosolve() API, 73 tests pass"
```

The architect verifier caught **9 rounds of bugs** that the worker agent was blind to — resource leaks, class variable contamination, state mutation — each subtler than the last. A single Claude Code session would likely have shipped with several of these.

## 💤 When to use kodo

You have a Claude Code Max subscription. You can't use it while you sleep.

kodo lets you set a goal, go to bed, and wake up to working code that's been independently tested and reviewed. The orchestrator (Gemini Flash, fractions of a cent) directs your subscription-covered Claude Code agents through multiple work cycles with built-in QA.

<table>
<tr><td nowrap>🌙 <strong>Overnight runs</strong></td><td>Set a goal, leave it running for hours. Cycles checkpoint progress automatically.</td></tr>
<tr><td nowrap>🔍 <strong>Built-in verification</strong></td><td>Independent architect + tester agents review work before accepting. Catches bugs the implementing agent is blind to.</td></tr>
<tr><td nowrap>🔄 <strong>Resume interrupted runs</strong></td><td>ctrl-C'd or crashed? <code>kodo --resume</code> picks up where it left off, with agents resuming their prior conversations.</td></tr>
<tr><td nowrap>🎭 <strong>Role separation</strong></td><td>Orchestrator making judgment calls, workers building code, independent reviewers catching issues.</td></tr>
<tr><td nowrap>🧠 <strong>Context efficiency</strong></td><td>Work is spread across multiple agent context windows, so tasks that might overwhelm a single agent's context can succeed when agents take turns with focused scopes. Not yet proven to help in practice, but architecturally sound.</td></tr>
</table>

## 🧑‍💻 When to just use Claude Code directly

<table>
<tr><td nowrap>📖 <strong>Learning</strong></td><td>You want to stay in the loop and build intuition by watching decisions unfold.</td></tr>
<tr><td nowrap>🧭 <strong>Exploration</strong></td><td>You don't know what you want yet and are discovering the shape of the solution as you go.</td></tr>
<tr><td nowrap>🎮 <strong>Steering</strong></td><td>The task needs frequent course corrections that only a human at the keyboard can provide.</td></tr>
</table>

## 📦 Install

```bash
# 1. Install uv (Python package manager) — skip if you already have it
#    See https://docs.astral.sh/uv/getting-started/installation/

# 2. Install kodo as a global CLI tool
uv tool install git+https://github.com/ikamen/kodo

# Or from a local checkout:
git clone https://github.com/ikamen/kodo && cd kodo
uv tool install .
```

That's it. `kodo` is now on your PATH.

### Prerequisites

You need **at least one** agent backend installed:

| Backend | Role | Install |
|---------|------|---------|
| 🤖 [Claude Code](docs/providers.md#claude-code-smart-workers--architect) | Smart workers + architect | `npm install -g @anthropic-ai/claude-code` |
| ⚡ [Cursor](docs/providers.md#cursor-fast-workers--testers) | Fast workers + testers | Comes with Cursor; enable `cursor-agent` in settings |
| 🟢 [OpenAI Codex](docs/providers.md#openai-codex-fast-workers) | Fast workers (alternative to Cursor) | `npm install -g @openai/codex` |
| 💎 [Gemini CLI](docs/providers.md#gemini-cli-fast-workers) | Fast workers (free tier available) | `npm install -g @google/gemini-cli` |

Claude Code + one fast backend (Cursor, Codex, or Gemini CLI) is recommended. See [docs/providers.md](docs/providers.md) for detailed setup instructions, authentication, and troubleshooting.

For the **API orchestrator** (recommended), set a key in `.env` or your environment:
```bash
GOOGLE_API_KEY=...     # Gemini orchestrator (recommended — fast and cheap)
ANTHROPIC_API_KEY=...  # Claude API orchestrator (alternative)
```

> **Why API over CLI orchestrators?** CLI coding tools (Claude Code, Cursor, Codex) are built to solve problems themselves — they'll try to write code, micromanage agents, or go off-script instead of purely delegating. A plain API model stays in its lane as a coordinator: it thinks high level and delegates, closer to human user behavior.

## 🚀 Usage

```bash
# Interactive mode (recommended) — walks you through goal, config, launch
kodo                     # run in current directory
kodo ./my-project        # run in specific directory

# Non-interactive (for scripting, CI, overnight cron jobs)
kodo --goal 'Build a REST API for user management' ./my-project
kodo --goal-file requirements.md ./my-project
kodo --goal 'Build X' --team saga --exchanges 50 --cycles 10 ./my-project

# Resume an interrupted run (looks in ~/.kodo/runs/)
kodo --resume                       # resume latest incomplete run in current dir
kodo ./my-project --resume          # resume latest in specific project
kodo --resume 20260218_205503       # resume specific run by ID
```

### Interactive mode

The interactive CLI will:
1. Ask for your goal (or reuse an existing `goal.md`)
2. Optionally refine it via a Claude interview
3. Let you pick team, orchestrator, and limits
4. Show a summary and ask for confirmation before starting
5. Print a live progress table as agents work

### Non-interactive mode

Passing `--goal` or `--goal-file` enables non-interactive mode — no prompts, no confirmations. The AI still breaks down your goal into stages (unless `--skip-intake` is set), but without asking clarifying questions.

### All flags

```
kodo [project_dir] [options]

Goal (mutually exclusive):
  --goal TEXT               Goal text (inline)
  --goal-file PATH          Path to file containing goal
  --improve                 Auto-analyze, test, and fix the codebase

Improve options:
  --improve-type TYPE       auto (default) | app | library

Configuration:
  --team TEAM               saga (default) | mission | quick
  --exchanges N             Max exchanges per cycle
  --cycles N                Max cycles
  --orchestrator BACKEND    api (default, recommended) | claude-code
  --orchestrator-model M    opus | sonnet | gemini-pro | gemini-flash

Behavior:
  --skip-intake             Skip AI goal refinement
  --auto-refine             Auto-refine goal (no human input, for overnight runs)
  --yes, -y                 Skip confirmation prompts
  --no-auto-commit          Disable auto-commit after stages

Output:
  --json                    Structured JSON to stdout (implies --yes)
  --resume [RUN_ID]         Resume an interrupted run
  --version                 Show version
```


> **⚠️ Heads up:** agents run with full permissions (`bypassPermissions` mode). They primarily work in your project directory but **can access any file on your system** (installing dependencies, editing configs, etc.). Make sure you have a git commit or backup before launching.

### Subcommands

```bash
kodo runs                     # list all past runs
kodo runs ./my-project        # list runs for a specific project
kodo backends                 # show available backends, models, API key status
kodo teams                    # list available teams
kodo teams auto               # auto-generate a team from available backends
kodo teams add my-team        # interactively create a custom team
kodo teams edit my-team       # edit an existing team
```

```
🦉 Orchestrator (Gemini Flash — fractions of a cent)
 │
 ├── 🔍 architect        Survey codebase, review code, find bugs
 ├── 🧠 worker_smart     Complex implementation (Claude Code)
 ├── ⚡ worker_fast       Quick tasks, iterations (Cursor, Codex, or Gemini CLI)
 ├── 🧪 tester           Run tests, verify behavior
 └── 🌐 tester_browser   Browser-based UI testing
```

**Key concepts:**

- **Session** — a stateful conversation with a backend (Claude, Cursor, Codex, or Gemini CLI). Tracks token usage, supports reset.
- **Agent** — a prompt + session + turn budget. Call `agent.run(task, project_dir)` to get work done.
- **Orchestrator** — an LLM that delegates to a team of agents via tool calls:
  - `ClaudeCodeOrchestrator` — runs on Claude Code with agents as MCP tools. Free on Max subscription.
  - `ApiOrchestrator` — runs on Anthropic/Gemini API. Pay-per-token orchestrator, but workers still use your subscription.
- **Cycle** — one unit of orchestrated work. Think of it as one dev session.
- **Run** — multiple cycles until done, with summaries bridging context between cycles.
- **Stage** — an independently verifiable piece of a plan. Stages run sequentially, or in parallel in git worktrees when grouped.

## 🎨 Custom teams

You can customize which agents run by dropping a `team.json` file — no code changes needed.

**Lookup order:**
1. `{project}/.kodo/team.json` — project-level override
2. `~/.kodo/teams/{name}.json` — user-level named team

**Example:** adding a UX/UI designer agent to review user-facing code:

```json
{
  "name": "saga-with-designer",
  "agents": {
    "worker_fast": {
      "backend": "claude", "model": "sonnet",
      "description": "Fast worker for implementation tasks."
    },
    "worker_smart": {
      "backend": "claude", "model": "opus",
      "description": "Deep-thinking worker for complex tasks."
    },
    "tester": {
      "backend": "claude", "model": "sonnet",
      "description": "Runs tests and reports results.",
      "max_turns": 10
    },
    "architect": {
      "backend": "claude", "model": "opus",
      "description": "Reviews architecture, validates direction.",
      "max_turns": 10, "timeout_s": 600
    },
    "designer": {
      "backend": "claude", "model": "opus",
      "description": "UX/UI advisor. Reviews component structure, accessibility, interaction patterns. Provides file/line references.",
      "system_prompt": "You are a UX/UI design advisor. Review code for UI structure, accessibility, responsive design, and consistency. Reference specific files and lines. Fix minor issues yourself. Say 'ALL CHECKS PASS' if clean.",
      "max_turns": 10, "timeout_s": 600,
      "fallback_model": "sonnet"
    }
  }
}
```

The orchestrator sees all agents in the team and delegates to them as needed. You can add any specialized reviewer (security auditor, performance analyst, etc.) the same way.

**Agent fields:** `backend` and `model` are required. Optional: `description`, `system_prompt`, `max_turns` (default 15), `timeout_s`, `chrome` (for browser agents), `fallback_model`.

## 💰 Cost tracking

Kodo tracks costs in two buckets:

| Bucket | What | Example |
|--------|------|---------|
| **🔑 API** | Real money — pay-per-token orchestrator calls | Gemini Flash orchestrator: ~$0.13/run |
| **✨ Virtual** | **Not charged.** Claude Code SDK reports what API usage *would* cost — but on a Max/Pro subscription you pay nothing extra. | Claude Max workers: shows ~$1.69, actual spend $0 |

The progress table labels subscription-covered costs as **Virtual** to make this clear. Only the **API** bucket represents real spend.

## 🔎 Analyzing past runs

```bash
# Open the interactive HTML viewer
python -m kodo.viewer ~/.kodo/runs/20260218_205503/run.jsonl
# Or serve on port 8080: python -m kodo.viewer --serve --port 8080 <logfile.jsonl>
```
