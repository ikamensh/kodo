"""Goal refinement and planning prompts."""

INTAKE_PREAMBLE = """\
You are refining a software project goal: {purpose}.

Ask 2-3 clarifying questions about constraints, tech choices, and scope.
When clear enough, write {output}."""

INTAKE_STAGES_SUFFIX = """

JSON format:
{{
  "context": "Shared context — tech stack, key files, conventions",
  "stages": [
    {{
      "index": 1,
      "name": "Short label",
      "description": "What this stage accomplishes",
      "acceptance_criteria": "Verifiable definition of done",
      "browser_testing": false
    }}
  ]
}}

Set browser_testing=true only for stages with web UI to verify in a browser.
Break into 2-5 independently verifiable stages, ordered by dependency."""

PARALLELISM_PASS_PROMPT = """\
Now review the plan you just wrote and identify stages that can run in parallel.

Stages can be parallel when they:
- Touch different files or areas of the codebase
- Don't depend on each other's output
- Could be done by separate developers simultaneously

For each stage, decide:
- parallel_group: assign the same integer to stages that should run concurrently. \
null for sequential stages. Each parallel stage runs in its own git worktree.
- persist_changes: true if a parallel stage produces code changes that should \
be merged back into the main branch. false for read-only/exploration stages \
(testing, auditing, analysis).

Update the JSON file, adding parallel_group and persist_changes to each stage. \
If no stages can be parallelized, leave them all as null/false."""

AUTO_REFINE_PROMPT = """\
Review this goal before implementation:

{goal}

Concisely answer (2-3 sentences each):
1. **Implicit constraints** — what does this goal imply that isn't stated?
2. **Simplest architecture** — one specific approach, not options.
3. **Common traps** — most likely over-engineering mistakes

Then write a refined goal to {output_path} incorporating the original intent \
plus what you deduced. Keep it concise — low level details don't belong here.
"""
