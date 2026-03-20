# Orchestration Tenets

Why orchestration should bring benefits over a single unattended agent, and where it can go wrong.

## Behavior problems orchestrator should solve

1. **Agents waiting on permission** — orchestrator gives permission, enabling unattended operation.
2. **Agents waiting on decision** — two approaches:
   - Get the agent to decide by posing the right questions.
   - Try multiple paths in parallel.
3. **Agents doing minimum viable work (low ambition)** — push back on shallow work, demand quality.
4. **Agents not verifying their work** — enforce verification structurally, not aspirationally.
5. **Agents not using methodologies** — option to make agents follow predefined methodologies (TDD, fault injection, etc.) via flags. Not imposed strictly — like `--test` and `--improve` work today. More such flags possible.

## Technical problems orchestrator should solve

1. **LLM context management** — orchestrator keeps focused high-level context for decision making. Agents get fresh contexts for low-level work without thinking tokens wasted on high-level planning.
2. **LLM roles and specialization** — different prompts and roles let each agent operate in a narrower, more effective domain.
3. **Parallelism** — run multiple agents concurrently on independent subtasks. Wall-clock-time win a single agent cannot achieve.

## Behavioral failure modes

1. **Micromanagement** — if the orchestrator gets fixated on low-level implementation, it becomes worse than an un-orchestrated agent because it tries to do low-level work through a communication bottleneck.
2. **Drift / heresy** — LLMs feeding off their own bad content, getting progressively worse.
3. **Over-decomposition** — breaking work into pieces so small that agents lack the context to make good local decisions.

## Technical drawbacks

1. **Models not finetuned for roles** — we are in OOD (out-of-distribution) generalization space. Performance penalty from using general models in specialized interaction patterns they weren't trained for.
2. **Time and token costs go up** — orchestration overhead in latency and tokens compared to a single agent session.

## A/B testing findings (2026-03-19)

Tested prompt-level changes against these tenets across 10+ real kodo runs on 5 different tasks (calculator, bug fix, todo feature, weather chart, Deno URL shortener).

**What worked:**
- "Don't accept untested code. New functionality needs tests before it ships." — 3/3 tasks got tests vs 2/3 baseline. Shipped to dev.

**What didn't work:**
- Verbose authority instruction ("you have permission to create files, install deps...") — produced fewer tests, lazier structure. Agents already take bold actions when needed (installed Deno runtime unprompted).
- Architecture value statement ("separate concerns, keep modules focused") — no measurable effect.
- Bundling multiple prompt additions together — caused regression (1/3 tests vs 2/3 baseline). More prompt text = more noise.

**Key insight:** Short, concrete, checkable value statements work. Abstract encouragement and detailed methodology don't. The model is smart enough — it needs clear standards, not instructions on how to think.
