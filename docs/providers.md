# Agent Backend Setup

kodo delegates coding work to agent backends. You need **at least one** installed.

## Claude Code (smart workers + architect)

Claude Code handles complex reasoning, architecture review, and deep debugging. Used for `worker_smart` and `architect` roles.

**Requires:** [Claude Max](https://claude.ai) or Pro subscription.

### Install

```bash
npm install -g @anthropic-ai/claude-code
```

### Verify

```bash
claude --version
```

### Notes

- Agents run under your Claude subscription — no per-token API cost for workers.
- kodo strips `ANTHROPIC_API_KEY` from the agent environment by default so sessions bill through your subscription, not the API.
- Supports session resume (agents continue their prior conversation on `kodo --resume`).

---

## Cursor (fast workers + testers)

Cursor handles fast iteration, testing, and browser-based verification. Used for `worker_fast`, `tester`, and `tester_browser` roles.

**Requires:** [Cursor](https://cursor.com) subscription.

### Install

1. Install [Cursor](https://cursor.com) (the editor).
2. Enable the CLI agent: Cursor Settings > Features > enable **cursor-agent**.
3. Ensure `cursor-agent` is on your PATH (Cursor adds it automatically on most systems).

### Verify

```bash
cursor-agent --help
```

### Notes

- No per-token cost — agents bill through your Cursor subscription.
- Testers and browser testers currently require Cursor (Codex does not yet support these roles).
- Supports session resume via chat ID.

---

## OpenAI Codex (fast workers)

OpenAI's Codex CLI is an alternative fast worker backend. If Cursor is not available but Codex is, kodo uses it for the `worker_fast` role.

**Requires:** [ChatGPT Plus/Pro](https://chatgpt.com) subscription or OpenAI API key.

### Install

```bash
# Option A: npm
npm install -g @openai/codex

# Option B: Homebrew (macOS)
brew install --cask codex
```

> **Note:** The [Codex desktop app](https://openai.com/index/introducing-the-codex-app/) does **not** install the CLI automatically. You need to install it separately using one of the commands above.

### Verify

```bash
codex --version
```

### Authentication

Codex CLI authenticates via your ChatGPT account (interactive login) or an API key for non-interactive use:

```bash
# Interactive: browser login on first run
codex exec "hello world" --full-auto

# Non-interactive: set API key
export CODEX_API_KEY=sk-...
```

For overnight/unattended kodo runs, set `CODEX_API_KEY` in your environment or `.env` file.

### Notes

- Default model: `gpt-5.4`. Configurable in team builders.
- Codex CLI and the Codex desktop app share the same underlying engine (App Server) — they are different frontends for the same technology.
- Supports session resume (agents continue their prior thread on `kodo --resume`).
- Runs in `workspace-write` sandbox mode by default (can read anything, writes limited to project directory).

---

## Gemini CLI (fast workers)

Google's open-source Gemini CLI is a full agentic coding tool — it reads/writes files, runs shell commands, and handles multi-turn coding tasks. Used for the `worker_fast` role when Cursor and Codex are unavailable.

**Requires:** Google account (free tier) or [Gemini API key](https://aistudio.google.com/) (paid tier).

### Install

```bash
npm install -g @google/gemini-cli
```

### Verify

```bash
gemini --version
```

### Authentication

On first run, Gemini CLI opens a browser for Google account OAuth (free tier). For headless/unattended use, set an API key:

```bash
export GEMINI_API_KEY=...
```

### Free tier limits

- 60 requests/minute, 1,000 requests/day
- No credit card required
- Uses Gemini 2.5 Flash by default

### Notes

- Default model: `gemini-2.5-flash`. 1M token context window.
- Open source (Apache-2.0) at [google-gemini/gemini-cli](https://github.com/google-gemini/gemini-cli).
- Built-in Docker/Podman sandboxing for safe unattended execution.
- Supports session resume (auto-saved sessions, `--resume` flag).
- The only backend with a generous free tier — useful for development and testing.

---

## Orchestrator API keys

The orchestrator (the "brain" that directs agents) can run on Gemini or Claude API. This is separate from the agent backends above.

```bash
# Gemini orchestrator (recommended — fast and cheap)
export GOOGLE_API_KEY=...     # or GEMINI_API_KEY

# Claude API orchestrator (alternative)
export ANTHROPIC_API_KEY=...
```

Set these in a `.env` file in your project directory or export them in your shell.
