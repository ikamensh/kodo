"""CLI entry point for kodo knowledge."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from kodo import log
from kodo.knowledge.models import KnowledgeGoal
from kodo.knowledge.orchestrator import KnowledgeOrchestrator


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="kodo-knowledge",
        description="Autonomous knowledge work agent — give it a goal, get a converged answer.",
    )
    parser.add_argument("goal", nargs="?", help="The knowledge goal/question")
    parser.add_argument(
        "--effort",
        choices=["quick", "standard", "deep", "exhaustive"],
        default="standard",
    )
    parser.add_argument("--model", default="claude-opus-4-6", help="Orchestrator model")
    parser.add_argument("--designer-model", default=None, help="Model for team design (defaults to orchestrator model)")
    parser.add_argument("--agent-model", default=None, help="Model for worker agents")
    parser.add_argument("--domain", action="append", default=[], help="Domain hints (repeatable)")
    parser.add_argument("--constraint", action="append", default=[], help="Constraints (repeatable)")
    parser.add_argument("--format", dest="output_format", default=None, help="Desired output format")
    parser.add_argument("--ref", action="append", default=[], help="Reference file paths (repeatable)")
    parser.add_argument("--output", default=None, help="Write final answer to this file")

    args = parser.parse_args(argv)

    if not args.goal:
        # Interactive: read from stdin
        print("Enter your goal (Ctrl+D to submit):")
        args.goal = sys.stdin.read().strip()
        if not args.goal:
            parser.error("No goal provided")

    goal = KnowledgeGoal(
        goal=args.goal,
        effort=args.effort,
        domain_hints=args.domain,
        constraints=args.constraint,
        output_format=args.output_format,
        reference_files=args.ref,
    )

    # Initialize logging with a proper RunDir (timestamped)
    run_dir = log.RunDir.create(project_dir=Path.cwd())
    log.init(run_dir)

    orchestrator = KnowledgeOrchestrator(
        model=args.model,
        designer_model=args.designer_model,
        agent_model=args.agent_model,
    )

    result = orchestrator.run(goal)

    # Print results
    print("\n" + "=" * 60)
    print(f"VERDICT: {result.verdict_type} (confidence: {result.confidence:.2f})")
    print(f"Rounds used: {result.rounds_used}")
    print(f"Cost: ${result.total_cost_usd:.4f}")
    print("=" * 60)
    print()
    print(result.answer)

    if result.open_questions:
        print("\n--- Open Questions ---")
        print(result.open_questions)

    if args.output:
        Path(args.output).write_text(result.answer)
        print(f"\nAnswer written to: {args.output}")

    log_file = log.get_log_file()
    if log_file and log_file.exists():
        print(f"\n  View run: uv run python -m kodo.viewer {log_file}\n")


if __name__ == "__main__":
    main()
