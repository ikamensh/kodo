# OpenCode model isolation

Call `OpenCodeSession(model="provider/model", isolated_model=True)` when every
native LLM call must use that selected model. Ordinary sessions keep their existing
configuration behavior. Clones and resumed sessions preserve the isolation policy.

The session pins the main, small, title, summary, compaction, and built-in worker
models; limits the provider catalog to that one model; and permits native task
dispatch only to the pinned general/explore agents. Coding tools retain OpenCode's
normal permissions, with explicit access to the system temporary directories
(including `/tmp` and resolved aliases). Native question/plan-mode tools are denied
because these sessions are headless. The helper's explicit `permission="deny"`
mode still denies all native tools for planners. No `--auto` flag is added.
`--pure` excludes external plugins, and a fixed title avoids
automatic title generation. This is configuration isolation, not a sandbox for
arbitrary commands that a coding agent can execute.

`kodo.opencode_config.isolated_opencode_config` supplies the same policy to callers
that manage their own OpenCode process. They must pass `--pure`, an explicit
`--dir`, and a fixed `--title`. It uses temporary config directories without moving
normal auth/session storage. The process-only `OPENCODE_TEST_HOME` override avoids
OpenCode's additional `~/.opencode` discovery. Repository `AGENTS.md` is retained
explicitly; native file reads still discover nested `AGENTS.md` instructions.

OpenCode merges organization and managed settings after inline config. The helper
therefore runs `opencode debug config --pure` before any model call and rejects an
effective policy override with `OpenCode isolation preflight failed:`. It does not
print resolved config, which may contain secrets. Preflight has a 20-second limit;
timeout or cancellation kills its process group, including config dependency
installers. Worker cancellation also kills native tool children.

The behavior follows OpenCode's [config loader](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/config/config.ts),
[provider catalog filtering](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/provider/provider.ts),
[built-in agents](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/agent/agent.ts),
and [instruction loading](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/session/instruction.ts).
Integration regressions exercise real subprocesses for conflicting settings,
managed overrides, permissions, resume/clone, cancellation, and structured provider
errors (including error events emitted alongside a zero exit status).

`QueryResult.incomplete_reason` is nonempty when OpenCode exits without completing
its tool loop or producing a terminal response. It includes the final finish
reason and any last tool error. Progress text and usage remain available;
`is_error` continues to identify provider/process errors. Callers must use this
field to distinguish partial work from completion and decide whether to resume.
A recoverable tool error followed by a final response does not mark the query
incomplete. This handles OpenCode's noninteractive permission refusal, which can
end a tool loop with CLI exit zero and no top-level error event.
