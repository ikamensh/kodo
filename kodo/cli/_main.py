"""Main entry point and argument parsing."""

import argparse
import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from kodo import __version__, log  # noqa: E402
from kodo.cli._improve import (  # noqa: E402
    _IMPROVE_GOAL,
    _IMPROVE_REPORT_FORMAT,
    _build_fallback_plan,
    _extract_section,
    run_improve_discovery,
)
from kodo.cli._intake import (  # noqa: E402
    _load_goal_plan,
    _offer_intake,
    get_goal,
    run_intake_auto,
    run_intake_noninteractive,
)
from kodo.cli._launch import (  # noqa: E402
    EXIT_ERROR,
    _emit_json_and_exit,
    _fail,
    launch_resume,
    launch_run,
)
from kodo.cli._params import (  # noqa: E402
    _build_params_from_flags,
    _load_or_select_params,
)
from kodo.cli._subcommands import _cmd_backends, _cmd_runs, _cmd_teams  # noqa: E402
from kodo.cli._ui import _print_banner  # noqa: E402
from kodo.factory import TEAMS, get_team, has_claude, has_cursor  # noqa: E402
from kodo.log import RunDir  # noqa: E402
from kodo.orchestrators.base import GoalPlan  # noqa: E402


def main() -> None:
    json_mode = "--json" in sys.argv
    try:
        _main_inner()
    except KeyboardInterrupt:
        if json_mode:
            print(json.dumps({"status": "error", "error": "Interrupted"}))
        else:
            print("\nInterrupted.")
        sys.exit(130)
    except SystemExit:
        raise
    except Exception as exc:
        if json_mode:
            print(json.dumps({"status": "error", "error": str(exc)}))
            sys.exit(EXIT_ERROR)
        raise


def _main_inner() -> None:
    # Handle subcommands before argparse
    if len(sys.argv) > 1 and sys.argv[1] == "runs":
        _cmd_runs()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "backends":
        _cmd_backends()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "teams":
        _cmd_teams()
        return

    parser = argparse.ArgumentParser(
        description="kodo — autonomous multi-agent coding",
        epilog="subcommands:\n  kodo runs [PROJECT_DIR]  List all known runs\n  kodo backends            List available backends and API keys\n  kodo teams               List, add, or edit team configurations",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"kodo {__version__}")
    parser.add_argument(
        "--resume",
        nargs="?",
        const="__latest__",
        default=None,
        metavar="RUN_ID",
        help="Resume an interrupted run. No value = latest incomplete run.",
    )

    # Non-interactive goal input
    goal_group = parser.add_mutually_exclusive_group()
    goal_group.add_argument(
        "--goal",
        type=str,
        default=None,
        help="Goal text (inline). Enables non-interactive mode.",
    )
    goal_group.add_argument(
        "--goal-file",
        type=str,
        default=None,
        help="Path to a file containing the goal text. Enables non-interactive mode.",
    )
    goal_group.add_argument(
        "--improve",
        action="store_true",
        default=False,
        help="Analyze codebase, auto-fix safe issues, and produce an improvement report.",
    )

    # Non-interactive config flags
    parser.add_argument(
        "--team",
        type=str,
        default=None,
        choices=["saga", "mission", "quick"],
        help="Team preset (default: saga).",
    )
    parser.add_argument(
        "--exchanges", type=int, default=None, help="Max exchanges per cycle."
    )
    parser.add_argument("--cycles", type=int, default=None, help="Max cycles.")
    parser.add_argument(
        "--orchestrator",
        type=str,
        default=None,
        choices=["api", "claude-code", "gemini-cli", "codex", "cursor"],
        help="Orchestrator backend.",
    )
    parser.add_argument(
        "--orchestrator-model",
        type=str,
        default=None,
        choices=["opus", "sonnet", "gemini-pro", "gemini-flash"],
        help="Model for the orchestrator LLM.",
    )
    parser.add_argument(
        "--skip-intake",
        action="store_true",
        default=False,
        help="Skip intake interview, use goal as-is",
    )
    parser.add_argument(
        "--auto-refine",
        action="store_true",
        default=False,
        help="Auto-refine goal before implementation (surfaces implicit constraints). "
        "Useful for unattended/overnight runs when no human is available for intake.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output structured JSON to stdout. Implies --yes.",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        default=False,
        help="Skip all confirmation prompts.",
    )
    parser.add_argument(
        "--no-auto-commit",
        action="store_true",
        default=False,
        help="Disable auto-commit after completed stages/goals.",
    )

    parser.add_argument(
        "project_dir",
        nargs="?",
        default=".",
        help="Project directory (default: current dir)",
    )
    args = parser.parse_args()

    # ── Early input validation ───────────────────────────────────────────
    if args.goal is not None and not args.goal.strip():
        _fail("--goal must not be empty or whitespace-only.")
    if args.exchanges is not None and args.exchanges <= 0:
        _fail("--exchanges must be a positive integer.")
    if args.cycles is not None and args.cycles <= 0:
        _fail("--cycles must be a positive integer.")

    # --json and --auto-refine imply --yes
    if args.json or args.auto_refine:
        args.yes = True

    # --improve forces non-interactive, skip-intake, yes, and defaults mode to saga
    if args.improve:
        args.skip_intake = True
        args.yes = True
        if args.team is None:
            args.team = "saga"

    non_interactive = (
        args.goal is not None or args.goal_file is not None or args.improve
    )
    skip_prompts = non_interactive or args.yes

    # In JSON mode, redirect prints to stderr so stdout stays clean for JSON
    import kodo.cli._launch as _launch_mod

    _launch_mod._original_stdout = None
    if args.json:
        _launch_mod._original_stdout = sys.stdout
        sys.stdout = sys.stderr
        os.environ["KODO_NO_VIEWER"] = "1"

    if not args.json:
        _print_banner()

    if non_interactive and args.resume is not None:
        _fail("--resume cannot be used with --goal/--goal-file/--improve")

    project_dir = Path(args.project_dir)
    project_dir.mkdir(parents=True, exist_ok=True)
    project_dir = project_dir.resolve()
    if not args.json:
        print(f"  Project: {project_dir}")

    # Handle --resume
    if args.resume is not None:
        if args.resume == "__latest__":
            runs = log.find_incomplete_runs(project_dir)
            if not runs:
                _fail("No incomplete runs found.")
            state = runs[0]
        else:
            run_log = log._runs_root() / args.resume / "run.jsonl"
            if run_log.exists():
                log_file = run_log
            else:
                _fail(f"Run not found: {args.resume}")
            state = log.parse_run(log_file)
            if state is None:
                _fail(f"Could not parse run from {log_file}")

        print(f"  Goal: {state.goal[:80]}{'...' if len(state.goal) > 80 else ''}")
        print(f"  Cycles completed: {state.completed_cycles}/{state.max_cycles}")
        if not skip_prompts:
            confirm = input("\nResume this run? [Y/n] ").strip().lower()
            if confirm in ("n", "no"):
                print("Aborted.")
                sys.exit(0)

        run_dir = RunDir.from_log_file(state.log_file, project_dir)
        result = launch_resume(run_dir, state)
        _emit_json_and_exit(args, result)
        return

    # 1. Get goal
    if non_interactive:
        if args.improve:
            goal_text = None  # constructed after run_dir is created
        elif args.goal is not None:
            goal_text = args.goal.strip()
            if not goal_text:
                _fail("Goal text is empty.")
        elif args.goal_file is not None:
            goal_path = Path(args.goal_file).resolve()
            if not goal_path.is_file():
                _fail(
                    f"Goal file not found or not a file: {goal_path}"
                    if goal_path.exists()
                    else f"Goal file not found: {goal_path}"
                )
            try:
                goal_text = goal_path.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeDecodeError) as exc:
                _fail(f"Cannot read goal file: {goal_path} — {exc}")
            if not goal_text:
                _fail("Goal file is empty.")
        else:
            _fail("No goal provided. Use --goal, --goal-file, or --improve.")
    else:
        goal_file = next(
            (p for p in project_dir.iterdir() if p.name.lower() == "goal.md"), None
        )
        if goal_file is not None:
            try:
                goal_text = goal_file.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeDecodeError) as exc:
                print(f"\nWarning: could not read {goal_file}: {exc}")
                goal_text = get_goal()
            else:
                print(f"\nFound existing goal in {goal_file}:")
                print("-" * 40)
                print(goal_text[:500])
                if len(goal_text) > 500:
                    print("...")
                print("-" * 40)
                use_existing = input("Use this goal? [Y/n] ").strip().lower()
                if use_existing in ("n", "no"):
                    goal_text = get_goal()
        else:
            goal_text = get_goal()

    # 2. Select parameters
    if non_interactive:
        params = _build_params_from_flags(args, project_dir)
    else:
        params = _load_or_select_params(project_dir)

    # 3. Create run directory
    run_dir = RunDir.create(project_dir)

    # Construct --improve goal and staged plan now that we have a run_dir
    # 4. Intake / goal plan
    plan: GoalPlan | None = None

    if args.improve:
        report_path = run_dir.root / "improve-report.md"
        goal_text = _IMPROVE_GOAL.format(
            report_path=report_path,
            report_format=_IMPROVE_REPORT_FORMAT,
        )
        if not args.json:
            print("  Running improve discovery...")
        plan = run_improve_discovery(run_dir, str(report_path))
        if plan is None:
            if not args.json:
                print("  Discovery unavailable; using default plan.")
            plan = _build_fallback_plan(str(report_path))
    elif non_interactive:
        existing_plan = _load_goal_plan(run_dir)
        if existing_plan:
            plan = existing_plan
            print(f"Using existing goal plan ({len(plan.stages)} stages)")
        elif args.auto_refine:
            backend = (
                "claude"
                if has_claude()
                else ("cursor" if has_cursor() else "gemini-cli")
            )
            refined = run_intake_auto(backend, run_dir, goal_text)
            if refined:
                goal_text = refined
        elif not args.skip_intake:
            plan = run_intake_noninteractive(run_dir, goal_text)
    else:
        # Check for existing goal plan first
        existing_plan = _load_goal_plan(run_dir)
        if existing_plan:
            print(f"\nFound existing goal plan ({len(existing_plan.stages)} stages):")
            print("-" * 40)
            for s in existing_plan.stages:
                print(f"  {s.index}. {s.name}")
                if s.acceptance_criteria:
                    print(f"     Done when: {s.acceptance_criteria[:100]}")
            print("-" * 40)
            use_plan = input("Use this goal plan? [Y/n] ").strip().lower()
            if not use_plan or use_plan == "y":
                plan = existing_plan

        if plan is None and not args.skip_intake:
            if args.auto_refine:
                backend = (
                    "claude"
                    if has_claude()
                    else ("cursor" if has_cursor() else "gemini-cli")
                )
                refined = run_intake_auto(backend, run_dir, goal_text)
                if refined:
                    goal_text = refined
            else:
                intake_result = _offer_intake(run_dir, goal_text)
                if isinstance(intake_result, GoalPlan):
                    plan = intake_result
                elif isinstance(intake_result, str):
                    goal_text = intake_result

    # 5. Summary and confirm
    team_name = params.get("team")
    if team_name not in TEAMS:
        _fail(f"Unknown team {team_name!r}. Valid teams: {', '.join(TEAMS)}.")
    if not args.json:
        team_preset = get_team(team_name)
        print("\n" + "=" * 60)
        print("  READY TO LAUNCH")
        print("=" * 60)
        print(f"  Project:      {project_dir}")
        print(f"  Goal:         {goal_text[:80]}{'...' if len(goal_text) > 80 else ''}")
        if plan:
            print(f"  Stages:       {len(plan.stages)}")
            for s in plan.stages:
                print(f"                  {s.index}. {s.name}")
        print(f"  Team:         {team_preset.name} — {team_preset.description}")
        print(
            f"  Orchestrator: {params['orchestrator']} ({params['orchestrator_model']})"
        )
        print(
            f"  Exchanges:    {params['max_exchanges']}/cycle, {params['max_cycles']} cycles"
        )
        print()

    if not skip_prompts:
        print("  WARNING: Agents run with full permissions (bypass mode).")
        print("  They will create, modify, and delete files — primarily in")
        print(f"  {project_dir}")
        print("  but they CAN access any file on your system (install deps,")
        print("  edit configs, etc). Make sure you have a git commit or backup.")
        print()
        confirm = input("Proceed? [Y/n] ").strip().lower()
        if confirm in ("n", "no"):
            print("Aborted.")
            sys.exit(0)

    # 6. Launch
    result = launch_run(run_dir, goal_text, params, plan=plan, json_mode=args.json)

    # 7. --improve post-run: report summary
    if args.improve:
        report_path = run_dir.root / "improve-report.md"
        if report_path.exists():
            try:
                report_content = report_path.read_text(encoding="utf-8")
            except OSError:
                report_content = ""
            auto_fixed = len(
                re.findall(
                    r"^- .+$",
                    _extract_section(report_content, "Auto-fixed"),
                    re.MULTILINE,
                )
            )
            needs_decision = len(
                re.findall(
                    r"^- .+$",
                    _extract_section(report_content, "Needs decision"),
                    re.MULTILINE,
                )
            )
            print(f"\n{'=' * 50}")
            print(f"Improve report: {report_path}")
            print(f"  Auto-fixed:     {auto_fixed}")
            print(f"  Needs decision: {needs_decision}")

            if args.json:
                _emit_json_and_exit(args, result, improve_report=report_content)
                return
        _emit_json_and_exit(args, result)
    else:
        _emit_json_and_exit(args, result)


if __name__ == "__main__":
    main()
