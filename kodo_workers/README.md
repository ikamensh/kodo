# kodo-workers

Worker-session backends extracted from [kodo](../kodo) so other projects can
drive Claude / Cursor / Codex / Gemini-CLI / Kimi / Kiro / OpenCode without
pulling kodo's orchestrator layer (pydantic-ai, mcp, uvicorn, questionary,
piicleaner, …).

A "session" here is a thin wrapper around one coding-agent process: it
accepts prompts, returns `QueryResult`s, tracks token/cost stats, and knows
how to resume or terminate.

## Install

From the kodo repo root:

```
pip install -e kodo_workers/                  # core, subprocess backends only
pip install -e 'kodo_workers/[claude]'        # adds claude-agent-sdk
pip install -e 'kodo_workers/[kimi]'          # adds kimi-agent-sdk
```

Subprocess-only backends (`cursor`, `codex`, `gemini-cli`, `kiro`,
`opencode`) need no Python deps — they shell out to the backend's CLI.

## Minimal usage

```python
from pathlib import Path

from kodo_workers import make_session

s = make_session("claude", "claude-opus-4-7")
result = s.query(
    "write a python function that adds two numbers",
    project_dir=Path("/tmp/scratch"),
    max_turns=3,
)
print(result.text)
```

`make_session(backend, model, ...)` returns a `Session` with
`.query(prompt, project_dir, *, max_turns) -> QueryResult`, `.reset()`,
`.terminate()`, `.close()`, and `.stats` (a `SessionStats`).

## Logging

Sessions call `kodo_workers.log.emit(...)` / `tprint(...)` /
`save_conversation(...)`.  By default these are silent (except `tprint`,
which goes to stdout).  Point them at a JSONL file with
`kodo_workers.log.set_log_file(path)`, or install a custom sink via
`kodo_workers.log.set_sink(sink)` — kodo itself uses the sink hook to
route session events into its own run log.
