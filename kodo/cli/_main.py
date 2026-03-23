"""Main entry point and argument parsing."""

import argparse
import contextlib
import json
import os
import re
import sys
from pathlib import Path
from typing import NoReturn

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))

from kodo import __version__, log  # noqa: E402
from kodo.cli._improve import (  # noqa: E402
    _build_fallback_plan,
    _collect_prior_needs_decision,
    _extract_section,
    run_improve_discovery,
)
from kodo.cli._test import (  # noqa: E402
    _build_test_fallback_plan,
    _collect_prior_test_work,
    parse_test_report_summary,
    run_test_discovery,
)
from kodo.prompts.improve import IMPROVE_GOAL, IMPROVE_REPORT_FORMAT  # noqa: E402
from kodo.prompts.test import TEST_GOAL, TEST_REPORT_FORMAT  # noqa: E402
from kodo.cli._intake import (  # noqa: E402
    _load_goal_plan,
    _offer_intake,
    get_goal,
    run_intake_auto,
    run_intake_noninteractive,
)
from kodo.cli._launch import (  # noqa: E402
    EXIT_ERROR,
    LaunchAssets,
    _emit_json_and_exit,
    _fail,
    build_launch_assets,
    json_output_redirect,
    launch_resume,
    launch_run,
    print_launch_identity,
)
from kodo.cli._params import (  # noqa: E402
    _build_params_from_flags,
    _load_or_select_params,
)
from kodo.cli._subcommands import (  # noqa: E402
    _cmd_backends,
    _cmd_issue,
    _cmd_logs,
    _cmd_runs,
    _cmd_teams,
    _cmd_update,
    _pick_run,
)
from kodo.cli._ui import _print_banner  # noqa: E402
from kodo.factory import TEAMS, get_team, preferred_backend  # noqa: E402
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


class _JSONArgumentParser(argparse.ArgumentParser):
    """ArgumentParser that outputs JSON errors when --json is in sys.argv."""

    def error(self, message: str) -> NoReturn:
        """Override error to output JSON when --json flag is present."""
        if "--json" in sys.argv:
            # Output JSON error and exit
            print(json.dumps({"status": "error", "error": message}))
            sys.exit(EXIT_ERROR)
        # Default behavior: print usage and exit
        super().error(message)


def _main_inner() -> None:
    # Handle subcommands before argparse (accept singular and plural forms)
    def _cmd_help() -> None:
        sys.argv[1] = "--help"
        _main_inner()

    _SUBCOMMAND_MAP = {
        "run": _cmd_runs,
        "runs": _cmd_runs,
        "backend": _cmd_backends,
        "backends": _cmd_backends,
        "team": _cmd_teams,
        "teams": _cmd_teams,
        "log": _cmd_logs,
        "logs": _cmd_logs,
        "issue": _cmd_issue,
        "issues": _cmd_issue,
        "update": _cmd_update,
        "help": _cmd_help,
    }
    if len(sys.argv) > 1 and sys.argv[1] in _SUBCOMMAND_MAP:
        try:
            from kodo.tips import record_subcommand

            record_subcommand(sys.argv[1])
        except Exception:
            pass
        _SUBCOMMAND_MAP[sys.argv[1]]()
        return

    # "kodo test/improve [...]" → "kodo --test/--improve [...]" — subcommand aliases
    if len(sys.argv) > 1 and sys.argv[1] in ("test", "improve"):
        sys.argv[1] = f"--{sys.argv[1]}"

    parser = _JSONArgumentParser(
        description="kodo — autonomous multi-agent coding",
        epilog="subcommands:\n  kodo test      Find bugs through realistic testing\n  kodo improve   Code review: simplification, usability, architecture\n  kodo runs      List all known runs\n  kodo logs      Open log viewer in browser\n  kodo issue     Report a bug (opens GitHub with run context)\n  kodo backends  List available backends and API keys\n  kodo teams     List, add, edit, or delete team configurations\n  kodo update    Update kodo to the latest version",
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
        help="Code review: simplification, usability, architecture.",
    )
    goal_group.add_argument(
        "--test",
        action="store_true",
        default=False,
        help="Find bugs through realistic interaction and workflows.",
    )
    goal_group.add_argument(
        "--fix-from",
        type=str,
        default=None,
        metavar="RUN_ID",
        help="Fix findings from a previous test or improve run.",
    )
    parser.add_argument(
        "--focus",
        type=str,
        default=None,
        help="Short guidance on what to focus on during --improve or --test (e.g. 'error handling in CLI').",
    )
    parser.add_argument(
        "--target",
        type=str,
        action="append",
        default=None,
        help="File or directory to target for --test (repeatable). E.g. --target src/auth/ --target api.py.",
    )

    # Non-interactive config flags — discover available teams dynamically
    from kodo.team_config import list_available_teams

    _team_names = [name for name, *_ in list_available_teams()]
    parser.add_argument(
        "--team",
        type=str,
        default=None,
        choices=_team_names or None,
        help="Team preset (default: full).",
    )
    parser.add_argument(
        "--exchanges",
        type=int,
        default=None,
        help="Max exchanges per cycle.",
    )
    parser.add_argument("--cycles", type=int, default=None, help="Max cycles.")
    parser.add_argument(
        "--orchestrator",
        type=str,
        default=None,
        help=(
            "Orchestrator model. Examples: opus, gpt-5.4, openai:gpt-5.4-mini, "
            "deepseek, ollama:qwen2.5-coder:14b. "
            "For CLI backends: claude-code:opus, cursor:sonnet-4-6, gemini-cli:gemini-3-flash."
        ),
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
        "--effort",
        type=str,
        default=None,
        choices=["low", "standard", "high", "max"],
        help="Effort level: low (simple tasks), standard (default), "
        "high (thorough verification), max (aggressive iteration).",
    )
    parser.add_argument(
        "--no-auto-commit",
        action="store_true",
        default=False,
        help="Disable auto-commit after completed stages/goals.",
    )
    parser.add_argument(
        "--coach",
        action="store_true",
        default=False,
        help="Enable AI coach that monitors orchestration patterns and flags "
        "issues (circles, drift, over-decomposition). Requires GEMINI_API_KEY.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="Run with fully mocked backends (no real LLM calls). "
        "Each mock session gets a letter (A, B, C...) and responds deterministically.",
    )

    parser.add_argument(
        "--project",
        type=str,
        default=".",
        help="Project directory (default: current directory).",
    )
    args = parser.parse_args()

    # ── Activate JSON mode early so _fail() outputs JSON ─────────────────
    import kodo.cli._launch as _launch_mod

    _json_stack = contextlib.ExitStack()
    _launch_mod._original_stdout = None
    if args.json:
        _json_stack.enter_context(json_output_redirect())
        os.environ["KODO_NO_VIEWER"] = "1"

    # ── Early input validation ───────────────────────────────────────────
    if args.goal is not None and not args.goal.strip():
        _fail("--goal must not be empty or whitespace-only.")
    if args.exchanges is not None and args.exchanges <= 0:
        _fail("--exchanges must be a positive integer.")
    if args.exchanges is not None and args.exchanges > 1000:
        _fail("--exchanges must not exceed 1000.")
    if args.cycles is not None and args.cycles <= 0:
        _fail("--cycles must be a positive integer.")
    if args.cycles is not None and args.cycles > 100:
        _fail("--cycles must not exceed 100.")

    # --project must exist and be a directory
    _project_path = Path(args.project).resolve()
    if not _project_path.exists():
        _fail(f"--project path does not exist: {_project_path}")
    if not _project_path.is_dir():
        _fail(f"--project path is not a directory: {_project_path}")

    # --resume must not be an empty string
    if args.resume is not None and args.resume == "":
        _fail(
            "--resume must not be an empty string. Omit the value to resume the latest run."
        )

    # --focus must not be empty if provided
    if args.focus is not None and not args.focus.strip():
        _fail("--focus must not be empty or whitespace-only.")

    # --focus requires --improve or --test
    if args.focus and not args.improve and not args.test:
        _fail("--focus can only be used with --improve or --test.")

    # --target requires --test
    if args.target and not args.test:
        _fail("--target can only be used with --test.")

    # Validate --target paths exist relative to project dir
    if args.target:
        for t in args.target:
            target_path = _project_path / t
            if not target_path.exists():
                _fail(f"--target path does not exist: {t} (resolved: {target_path})")

    # --skip-intake and --auto-refine require a goal (otherwise silently goes interactive)
    if (args.skip_intake or args.auto_refine) and not (
        args.goal or args.goal_file or args.improve or args.test
    ):
        _fail(
            "--skip-intake and --auto-refine require --goal, --goal-file, --improve, or --test."
        )

    # --json and --auto-refine imply --yes
    if args.json or args.auto_refine:
        args.yes = True

    # --improve forces non-interactive, skip-intake, yes, and defaults mode to full
    if args.improve:
        args.skip_intake = True
        args.yes = True
        if args.team is None:
            args.team = "full"

    # --test forces non-interactive, skip-intake, yes, and defaults to test team
    if args.test:
        args.skip_intake = True
        args.yes = True
        if args.team is None:
            args.team = "test"

    # --fix-from: load findings from a previous run and create a fix goal
    if args.fix_from:
        args.skip_intake = True
        args.yes = True
        if args.team is None:
            args.team = "full"

    non_interactive = (
        args.goal is not None
        or args.goal_file is not None
        or args.improve
        or args.test
        or args.fix_from
    )
    skip_prompts = non_interactive or args.yes

    if non_interactive and args.resume is not None:
        _fail(
            "--resume cannot be used with --goal/--goal-file/--improve/--test/--fix-from"
        )

    project_dir = Path(args.project).resolve()

    # --debug: skip intake, let normal flow handle the rest
    if args.debug:
        args.skip_intake = True

    # Handle --resume
    if args.resume is not None:
        if args.resume == "__latest__":
            runs = log.find_incomplete_runs(project_dir)
            if not runs:
                _fail("No incomplete runs found.")
            state = _pick_run(runs, skip_prompts=skip_prompts)
            if state is None:
                print("Aborted.")
                sys.exit(0)
        else:
            run_dir = log._runs_root() / args.resume
            run_log = run_dir / "log.jsonl"
            if not run_log.exists():
                run_log = run_dir / "run.jsonl"  # legacy
            if run_log.exists():
                log_file = run_log
            else:
                _fail(f"Run not found: {args.resume}")
            _parsed = log.parse_run(log_file)
            if _parsed is None:
                _fail(f"Could not parse run from {log_file}")
            state = _parsed

        if not args.json:
            _print_banner()
            print(f"  Project: {project_dir}")
        print(f"  Goal: {state.goal[:80]}{'...' if len(state.goal) > 80 else ''}")
        print(f"  Cycles completed: {state.completed_cycles}/{state.max_cycles}")
        if not skip_prompts:
            confirm = input("\nResume this run? [Y/n] ").strip().lower()
            if confirm in ("n", "no"):
                print("Aborted.")
                sys.exit(0)

        run_dir = RunDir.from_log_file(state.log_file, project_dir)
        if not args.json:
            try:
                from kodo.tips import record_run_start

                record_run_start({}, is_resume=True, json_mode=args.json)
            except Exception:
                pass
        try:
            result = launch_resume(
                run_dir,
                state,
                team_override=args.team,
                orchestrator_override=getattr(args, "orchestrator", None),
                exchanges_override=getattr(args, "exchanges", None),
                cycles_override=getattr(args, "cycles", None),
                effort_override=getattr(args, "effort", None),
                debug=args.debug,
            )
        except Exception as exc:
            _fail(str(exc) or type(exc).__name__)
        _emit_json_and_exit(args, result)
        if not args.json:
            try:
                from kodo.tips import show_next_steps

                show_next_steps(run_id=state.run_id)
            except Exception:
                pass
        return

    # 1. Get goal
    goal_text: str | None = None
    if non_interactive:
        if args.improve or args.test:
            pass  # goal_text constructed after run_dir is created
        elif args.fix_from:
            pass  # goal_text constructed after run_dir is created
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
                    else f"Goal file not found: {goal_path}",
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
        if not args.json:
            _print_banner()
            print(f"  Project: {project_dir}")
        goal_file = next(
            (p for p in project_dir.iterdir() if p.name.lower() == "goal.md"),
            None,
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

    # Print banner after validation passes (non-interactive)
    if non_interactive and not args.json:
        _print_banner()
        print(f"  Project: {project_dir}")

    # 2. Select parameters
    if non_interactive:
        params = _build_params_from_flags(args, project_dir)
    else:
        params = _load_or_select_params(project_dir)

    # Determine run mode for mode-specific behavior (e.g. coach tenets)
    if args.test:
        params["mode"] = "test"
    elif args.improve:
        params["mode"] = "improve"
    elif args.fix_from:
        params["mode"] = "fix"

    # 3. Create run directory and initialize log early so pre-launch crashes
    #    (e.g. during intake/discovery) produce a resumable run.
    #    log.init + cli_args emit are idempotent — launch_run re-emits cli_args
    #    with the full goal_text once it's known.
    run_dir = RunDir.create(project_dir)
    log.init(run_dir)
    log.emit(
        "cli_args",
        **params,
        project_dir=str(project_dir),
    )
    if not args.json:
        print(f"  Run dir: {run_dir.root}")

    # Construct --improve goal and staged plan now that we have a run_dir
    # 4. Intake / goal plan
    plan: GoalPlan | None = None
    intake_session = None  # live session for SessionAdvisor reuse
    launch_prepared: LaunchAssets | None = None
    skip_launch_identity = False

    if args.improve:
        report_path = run_dir.root / "improve-report.md"
        prior = _collect_prior_needs_decision(run_dir)
        focus = args.focus
        focus_line = f"\n\n**Focus area:** {focus}" if focus else ""
        goal_text = (
            IMPROVE_GOAL.format(
                report_path=report_path,
                report_format=IMPROVE_REPORT_FORMAT,
            )
            + focus_line
        )
        run_dir.goal_file.write_text(goal_text, encoding="utf-8")
        if not args.json:
            if prior:
                print("  Carrying forward prior 'Needs decision' items.")
        if args.debug:
            if not args.json:
                print("  Debug mode; using default improve plan.")
            plan = _build_fallback_plan(str(report_path), prior, focus=focus)
        else:
            if not args.json:
                launch_prepared = build_launch_assets(
                    run_dir,
                    params,
                    goal_text,
                    json_mode=args.json,
                    debug=False,
                )
                print_launch_identity(
                    launch_prepared,
                    params,
                    log.init(run_dir),
                    json_mode=args.json,
                    debug=False,
                )
                skip_launch_identity = True
            if not args.json:
                print("  Running improve discovery...")
            plan = run_improve_discovery(run_dir, str(report_path), prior, focus=focus)
            if plan is None:
                if not args.json:
                    print("  Discovery unavailable; using default plan.")
                plan = _build_fallback_plan(str(report_path), prior, focus=focus)
    elif args.test:
        report_path = run_dir.root / "test-report.md"
        prior_test_work = _collect_prior_test_work(run_dir)
        focus = args.focus
        targets = args.target
        focus_line = f"\n\n**Focus area:** {focus}" if focus else ""
        target_line = ""
        if targets:
            target_list = ", ".join(f"`{t}`" for t in targets)
            target_line = f"\n\n**Target files/dirs:** {target_list}"
        goal_text = (
            TEST_GOAL.format(
                report_path=report_path,
                report_format=TEST_REPORT_FORMAT,
            )
            + focus_line
            + target_line
        )
        run_dir.goal_file.write_text(goal_text, encoding="utf-8")
        if not args.json:
            if prior_test_work:
                print("  Carrying forward context from prior test runs.")
        if args.debug:
            if not args.json:
                print("  Debug mode; using default test plan.")
            plan = _build_test_fallback_plan(
                str(report_path),
                focus=focus,
                targets=targets,
                prior_test_work=prior_test_work,
            )
        else:
            if not args.json:
                launch_prepared = build_launch_assets(
                    run_dir,
                    params,
                    goal_text,
                    json_mode=args.json,
                    debug=False,
                )
                print_launch_identity(
                    launch_prepared,
                    params,
                    log.init(run_dir),
                    json_mode=args.json,
                    debug=False,
                )
                skip_launch_identity = True
            if not args.json:
                print("  Running test discovery...")
            plan = run_test_discovery(
                run_dir,
                str(report_path),
                focus=focus,
                targets=targets,
                prior_test_work=prior_test_work,
            )
            if plan is None:
                if not args.json:
                    print("  Discovery unavailable; using default test plan.")
                plan = _build_test_fallback_plan(
                    str(report_path),
                    focus=focus,
                    targets=targets,
                    prior_test_work=prior_test_work,
                )
    elif args.fix_from:
        from kodo.cli._shared import extract_section

        source_run = log._runs_root() / args.fix_from
        # Try test-report.md first, then improve-report.md
        source_report = None
        for name in ("test-report.md", "improve-report.md"):
            candidate = source_run / name
            if candidate.exists():
                source_report = candidate
                break
        if source_report is None:
            _fail(f"No test or improve report found in run {args.fix_from}")
        try:
            report_content = source_report.read_text(encoding="utf-8")
        except OSError as exc:
            _fail(f"Cannot read report: {source_report} — {exc}")

        # Extract fixable findings
        findings: list[str] = []
        for section in (
            "Critical Findings",
            "Integration & Workflow Findings",
            "Usability Gaps",
            "Needs decision",
        ):
            body = extract_section(report_content, section)
            for line in body.strip().splitlines():
                line = line.strip()
                if line.startswith("- "):
                    findings.append(line)
        if not findings:
            _fail(f"No fixable findings in {source_report}")

        findings_block = "\n".join(findings)
        goal_text = (
            f"Fix these findings from a previous kodo run ({args.fix_from}):\n\n"
            f"{findings_block}\n\n"
            f"For each finding, write a regression test that fails, then fix the code "
            f"so the test passes. Commit test and fix separately."
        )
        if not args.json:
            print(f"  Fixing {len(findings)} findings from run {args.fix_from}")
    elif non_interactive:
        if (
            goal_text is None
        ):  # pragma: no cover – defensive; set in the non-improve branch above
            _fail("Internal error: goal_text is not set.")
        existing_plan = _load_goal_plan(run_dir)
        if existing_plan:
            plan = existing_plan
            print(f"Using existing goal plan ({len(plan.stages)} stages)")
        elif args.auto_refine:
            backend = preferred_backend()
            if backend is None:
                _fail("No backend available for auto-refine. Install a backend first.")
            refined = run_intake_auto(backend, run_dir, goal_text)
            if refined:
                goal_text = refined
        elif not args.skip_intake:
            plan = run_intake_noninteractive(run_dir, goal_text)
    else:
        if (
            goal_text is None
        ):  # pragma: no cover – defensive; set in the interactive branch above
            _fail("Internal error: goal_text is not set.")
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
                backend = preferred_backend()
                if backend is None:
                    _fail(
                        "No backend available for auto-refine. Install a backend first."
                    )
                refined = run_intake_auto(backend, run_dir, goal_text)
                if refined:
                    goal_text = refined
            else:
                intake_result, intake_session = _offer_intake(run_dir, goal_text)
                if isinstance(intake_result, GoalPlan):
                    plan = intake_result
                    skip_prompts = True  # user confirmed in _offer_intake
                elif isinstance(intake_result, str):
                    goal_text = intake_result
                    skip_prompts = True  # user confirmed in _offer_intake

    # By this point, goal_text is always a str (None paths call _fail() or
    # _fail() is unreachable in --improve where it's set on line 410).
    if (
        goal_text is None
    ):  # pragma: no cover – defensive; all code paths set goal_text or call _fail()
        _fail("Internal error: goal_text is not set.")

    # 5. Summary and confirm
    team_name = params.get("team")
    # Accept both hardcoded presets and user-defined JSON teams from ~/.kodo/teams/
    _all_team_names = set(TEAMS) | {n for n, *_ in list_available_teams()}
    if not isinstance(team_name, str) or team_name not in _all_team_names:
        _fail(
            f"Unknown team {team_name!r}. Valid teams: {', '.join(sorted(_all_team_names))}."
        )
    if not args.json:
        team_preset = get_team(team_name)
        print(f"\n  ┌{'─' * 58}┐")
        print(f"  │{'READY TO LAUNCH':^58}│")
        print(f"  ├{'─' * 58}┤")

        def _box_line(text: str, w: int = 56) -> None:
            print(f"  │ {text:<{w}} │")

        _box_line(f"Project:      {project_dir}")
        goal_display = f"{goal_text[:50]}..." if len(goal_text) > 50 else goal_text
        _box_line(f"Goal:         {goal_display}")
        if plan:
            _box_line(f"Stages:       {len(plan.stages)}")
            for s in plan.stages:
                _box_line(f"                {s.index}. {s.name}")
        team_text = f"{team_preset.name} — {team_preset.description}"
        if len(team_text) > 42:
            team_text = team_text[:41] + "…"
        _box_line(f"Team:         {team_text}")
        from kodo.models import orchestrator_emoji as _orch_emoji

        _emo = _orch_emoji(params["orchestrator"], params.get("orchestrator_model"))
        _emo_s = f"{_emo} " if _emo else ""
        _box_line(
            f"Orchestrator: {_emo_s}{params['orchestrator']} ({params['orchestrator_model']})",
        )
        _box_line(
            f"Exchanges:    {params['max_exchanges']}/cycle, {params['max_cycles']} cycles",
        )
        effort = params.get("effort", "standard")
        if effort != "standard":
            _box_line(f"Effort:       {effort}")
        if args.debug:
            _box_line("Mode:         DEBUG (all backends mocked)")
        print(f"  └{'─' * 58}┘")
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

    # Record CLI features for progressive learning tips
    if not args.json:
        try:
            from kodo.tips import record_run_start

            record_run_start(
                params,
                is_improve=args.improve,
                is_test=args.test,
                is_fix_from=bool(args.fix_from),
                has_focus=bool(args.focus),
                has_target=bool(args.target),
                has_goal_file=bool(args.goal_file),
                json_mode=args.json,
            )
        except Exception:
            pass

    # 6. Launch
    try:
        result = launch_run(
            run_dir,
            goal_text,
            params,
            plan=plan,
            json_mode=args.json,
            debug=args.debug,
            intake_session=intake_session,
            prepared=launch_prepared,
            skip_identity_print=skip_launch_identity,
        )
    except Exception as exc:
        _fail(str(exc) or type(exc).__name__)

    # 7. --improve / --test post-run: report summary
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
                ),
            )
            needs_decision = len(
                re.findall(
                    r"^- .+$",
                    _extract_section(report_content, "Needs decision"),
                    re.MULTILINE,
                ),
            )
            print(f"\n{'=' * 50}")
            print(f"Improve report: {report_path}")
            print(f"  Auto-fixed:     {auto_fixed}")
            print(f"  Needs decision: {needs_decision}")

            if needs_decision > 0 and not args.json:
                print("\n  To fix 'needs decision' items:")
                print(f"    kodo --fix-from {run_dir.run_id}")

            if args.json:
                _emit_json_and_exit(args, result, improve_report=report_content)
                return
        _emit_json_and_exit(args, result)
    elif args.test:
        report_path = run_dir.root / "test-report.md"
        if report_path.exists():
            try:
                report_content = report_path.read_text(encoding="utf-8")
            except OSError:
                report_content = ""
            summary = parse_test_report_summary(report_content)
            print(f"\n{'=' * 50}")
            print(f"Test report: {report_path}")
            if summary.get("findings_count"):
                print(f"  Findings:         {summary['findings_count']}")
            if summary.get("regression_tests") or summary.get("regression_count"):
                count = summary.get("regression_tests") or summary.get(
                    "regression_count", 0
                )
                print(f"  Regression tests: {count}")
            if summary.get("blocked_count"):
                print(f"  Blocked:          {summary['blocked_count']}")
                for detail in summary.get("blocked_details", []):
                    print(f"    {detail}")

            # Suggest fix command if there are fixable findings
            fixable = summary.get("findings_count", 0) - summary.get("blocked_count", 0)
            if fixable > 0 and not args.json:
                print("\n  To fix these findings:")
                print(f"    kodo --fix-from {run_dir.run_id}")

            if args.json:
                _emit_json_and_exit(args, result, test_report=report_content)
                return
        _emit_json_and_exit(args, result)
    else:
        _emit_json_and_exit(args, result)

    # Post-run: contextual next-step suggestion
    if not args.json:
        try:
            from kodo.tips import show_next_steps

            show_next_steps(
                run_id=run_dir.run_id,
                is_improve=args.improve,
                is_test=args.test,
            )
        except Exception:
            pass


if __name__ == "__main__":
    main()
