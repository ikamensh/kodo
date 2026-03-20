"""Example: naive plan-execute loop using kodo primitives.

This was the original approach before the orchestrated mode.
Kept as a simple example of using Agent + Session directly.

Usage:
  .venv/bin/python examples/naive_loop.py goal-td-game.md /tmp/td-test \
    --backend claude --model sonnet --steps 5 --iters 2
"""

import argparse
import os
from pathlib import Path

from kodo.agent import Agent, AgentResult
from kodo.sessions.claude import ClaudeSession
from kodo.sessions.cursor import CursorSession

os.environ.pop("CLAUDECODE", None)

PLAN_PROMPT = """\
You are an autonomous planning agent. You will be given a project goal and the current state of a project directory.

Your job:
1. Read the goal carefully.
2. Examine the current project state (files, code, tests, etc.).
3. Write or update `plan.md` in the project directory with a concrete, ordered list of next steps.

Each step in plan.md should be a checkbox item like:
- [ ] Step description
- [x] Completed step

Mark steps that are already done as [x]. Focus on the next actionable steps.
Only write/update plan.md — do not implement anything."""

EXECUTE_PROMPT = """\
You are an autonomous execution agent. You will be given a project goal and a plan.

Your job:
1. Read `plan.md` in the project directory.
2. Find the FIRST unchecked step (- [ ]).
3. Implement that step fully — write code, create files, run commands, run tests, etc.
4. After completing the step, update `plan.md` to mark it as done (- [x]).

Only do ONE step per invocation. Be thorough — make sure the step actually works."""


def print_result(result: AgentResult) -> None:
    q = result.query
    parts = [f"Done in {q.elapsed_s:.1f}s"]
    if q.turns is not None:
        parts.append(f"{q.turns} turns")
    if q.cost_usd is not None:
        parts.append(f"${q.cost_usd:.4f}")
    print(f"  {' | '.join(parts)}")
    if result.session_tokens > 0:
        print(f"  Session: {result.session_tokens:,} tokens")
    if result.context_reset:
        print(f"  Context reset: {result.context_reset_reason}")
    if q.text:
        summary = q.text[:300] + "..." if len(q.text) > 300 else q.text
        print(f"  Result: {summary}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Naive plan-execute loop")
    parser.add_argument("goal", help="Path to goal .md file")
    parser.add_argument("project_dir", help="Path to project directory")
    parser.add_argument("--backend", choices=["claude", "cursor"], default="claude")
    parser.add_argument("--model", default=None)
    parser.add_argument("--steps", type=int, default=10)
    parser.add_argument("--iters", type=int, default=3)
    args = parser.parse_args()

    if args.model is None:
        args.model = "sonnet" if args.backend == "claude" else "composer-2"

    goal_text = Path(args.goal).read_text()
    project_dir = Path(args.project_dir)
    project_dir.mkdir(parents=True, exist_ok=True)
    project_dir = project_dir.resolve()

    if args.backend == "cursor":
        session = CursorSession(model=args.model)
    else:
        session = ClaudeSession(model=args.model)

    try:
        planner = Agent(session, PLAN_PROMPT, max_turns=15)
        executor = Agent(session, EXECUTE_PROMPT, max_turns=30)

        for iteration in range(1, args.iters + 1):
            print(f"\n{'=' * 50}")
            print(f"ITERATION {iteration}/{args.iters}")
            print(f"{'=' * 50}")

            print("\n=== PLAN PHASE ===")
            print_result(planner.run(goal_text, project_dir))

            for step in range(1, args.steps + 1):
                print(f"\n--- EXECUTE STEP {step}/{args.steps} ---")
                print_result(executor.run(goal_text, project_dir))

        print("\nAll iterations complete.")
    finally:
        if hasattr(session, "close"):
            session.close()


if __name__ == "__main__":
    main()
