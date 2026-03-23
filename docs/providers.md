# Agent Backend Setup

kodo delegates coding work to agent backends. You need **at least one** installed.

## Claude Code (smart workers + architect)

Claude Code handles complex reasoning, architecture review, and deep debugging. Used for `worker_smart` and `architect` roles.

**Install and authenticate:** [Claude Code setup](https://code.claude.com/docs/en/setup)

**Requires:** [Claude Max](https://claude.ai) or Pro subscription.

### kodo-specific notes

- Agents run under your Claude subscription — no per-token API cost for workers.
- kodo strips `ANTHROPIC_API_KEY` from the agent environment by default so sessions bill through your subscription, not the API.
- Supports session resume (agents continue their prior conversation on `kodo --resume`).

---

## Cursor (fast workers + testers)

Cursor handles fast iteration, testing, and browser-based verification. Used for `worker_fast`, `tester`, and `tester_browser` roles.

**Install and authenticate:** [Cursor CLI installation](https://cursor.com/docs/cli/installation)

**Requires:** Cursor subscription. Enable the CLI agent: Cursor Settings > Features > enable **cursor-agent**.

### kodo-specific notes

- No per-token cost — agents bill through your Cursor subscription.
- Testers and browser testers currently require Cursor (Codex does not yet support these roles).
- Supports session resume via chat ID.

---

## OpenAI Codex (fast workers)

OpenAI's Codex CLI is an alternative fast worker backend. If Cursor is not available but Codex is, kodo uses it for the `worker_fast` role.

**Install and authenticate:** [Codex CLI install](https://github.com/openai/codex/blob/main/docs/install.md)

**Requires:** [ChatGPT Plus/Pro](https://chatgpt.com) subscription or OpenAI API key.

### kodo-specific notes

- Default model: `gpt-5.4`. Configurable in team config.
- Supports session resume (agents continue their prior thread on `kodo --resume`).
- Runs in `workspace-write` sandbox mode by default.

---

## Gemini CLI (fast workers)

Google's open-source Gemini CLI. Used for the `worker_fast` role when Cursor and Codex are unavailable.

**Install and authenticate:** [Gemini CLI installation](https://geminicli.com/docs/get-started/installation/)

**Requires:** Google account (free tier) or [Gemini API key](https://aistudio.google.com/) (paid tier).

### kodo-specific notes

- Default model: `gemini-2.5-flash`. 1M token context window.
- Supports session resume (auto-saved sessions, `--resume` flag).
- The only backend with a generous free tier — useful for development and testing.

---

## Kimi (smart workers)

Moonshot AI's Kimi agent SDK. Used as a smart worker alternative.

**Install and authenticate:** [Kimi CLI getting started](https://www.kimi.com/code/docs/en/kimi-cli/guides/getting-started.html)

**Requires:** `KIMI_API_KEY` environment variable.

---

## Kiro (workers)

Amazon's AI coding CLI. Used as a general-purpose worker.

**Install and authenticate:** [Kiro CLI installation](https://kiro.dev/docs/cli/installation/)

**Requires:** AWS Builder ID (free) or AWS Pro subscription.

### kodo-specific notes

- Default model: auto-selected by Kiro. Override with `--model` in team config.
- Session resume supported (`--resume` flag, per-directory).
- No token usage reporting — cost tracking shows `0` tokens but the subscription covers all usage.

---

## Orchestrator API keys

The orchestrator (the "brain" that directs agents) can run on Gemini API, Claude API, or local Ollama. This is separate from the agent backends above.

```bash
# Gemini orchestrator (recommended — fast and cheap)
export GOOGLE_API_KEY=...     # or GEMINI_API_KEY

# Claude API orchestrator (alternative)
export ANTHROPIC_API_KEY=...
```

Set these in a `.env` file in your project directory or export them in your shell.

### Local Ollama orchestrator

If you already use [Ollama](https://ollama.com/), you can run the orchestrator locally:

```bash
ollama pull qwen2.5-coder:14b
kodo --goal "..." --orchestrator-model ollama:qwen2.5-coder:14b
```

Notes:

- Interactive setup lists the detected local Ollama models so you can pick one directly.
- `ollama-local` remains available as a shortcut for "first detected local model".
- Passing an Ollama model implies the `api` orchestrator automatically, so `--orchestrator api` is optional.
- kodo assumes the default Ollama OpenAI-compatible endpoint at `http://localhost:11434/v1`.
- This only replaces the orchestrator API cost. You still need at least one worker backend from the sections above.
