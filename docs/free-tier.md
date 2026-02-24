# Running kodo for Free (Gemini CLI Only)

You can run kodo with **zero cost** using only Gemini CLI and a free Google API key. No Claude Code, no Cursor, no credit card required.

## Setup

### 1. Get a free API key

Go to [Google AI Studio](https://aistudio.google.com/apikey) and create a free API key. No credit card needed.

```bash
export GOOGLE_API_KEY="your-key-here"
```

### 2. Install Gemini CLI

```bash
npm install -g @anthropic-ai/gemini-cli
# or
npx @anthropic-ai/gemini-cli
```

See [Gemini CLI docs](https://github.com/google-gemini/gemini-cli) for details.

### 3. Install kodo

```bash
uv tool install git+https://github.com/ikamen/kodo
```

### 4. Run

kodo auto-detects that Gemini CLI is your only backend and builds a full team:

```bash
# Quick improvement scan
kodo --improve ./my-project

# Overnight feature build
kodo --goal-file feature.md ./my-project
```

## What you get

kodo builds a complete team from Gemini CLI with model tiers:

| Role | Model | Purpose |
|------|-------|---------|
| worker_fast | gemini-2.5-flash | Straightforward coding tasks |
| worker_smart | gemini-2.5-pro | Complex reasoning, debugging |
| architect | gemini-2.5-pro | Code review, architecture decisions |
| tester | gemini-2.5-flash | Independent verification |

In mission/quick mode you get both workers (fast + smart). In saga mode you get the full team above.

## Free tier rate limits

The free tier has rate limits that may slow down longer runs:

- **Requests per minute**: 15 (Flash), 5 (Pro)
- **Tokens per minute**: 1M (Flash), 250K (Pro)
- **Requests per day**: 1,500 (Flash), 50 (Pro)

For overnight runs, the daily Pro limit (50 requests) is the main constraint. Consider:

- Using `--mode quick` for smaller tasks (fewer exchanges, 1 cycle)
- Setting `--exchanges 10 --cycles 1` for constrained runs
- Running during off-peak hours when rate limits reset

## Example

```bash
# Minimal run with free tier
kodo --goal "Add input validation to the user registration form" \
     --mode quick --yes ./my-project
```

## Upgrading

If you hit rate limits, you can:

1. **Pay-as-you-go Gemini**: Add billing to your Google Cloud project for higher limits
2. **Add Claude Code**: Install Claude Code CLI for smart workers (requires Max subscription)
3. **Mix backends**: kodo auto-detects all available backends and builds the best team
