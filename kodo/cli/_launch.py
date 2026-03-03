"""Run launch and resume logic, plus JSON output helpers."""

from __future__ import annotations

import contextlib
import json
import logging
import sys
from pathlib import Path
from typing import NoReturn

# Suppress noisy SDK info messages ("Using bundled Claude Code CLI: ...")
# that break the clean progress output.
logging.getLogger("claude_agent_sdk").setLevel(logging.WARNING)

from kodo import log
from kodo.cli._intake import _load_goal_plan
from kodo.cli._ui import _atomic_write, _backend_label, _plural
from kodo.factory import (
    build_orchestrator,
    get_team,
    preflight_check_backends,
)
from kodo.log import RunDir
from kodo.orchestrators.base import GoalPlan, ResumeState, RunResult
from kodo.team_config import (
    build_team_from_json,
    load_team_config,
    team_to_json,
    validate_verifiers,
)

# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------

EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_PARTIAL = 2


def _try_auto_fix_team(
    team_name: str, project_dir: Path, exc: Exception,
) -> tuple[dict, str, dict | None]:
    """Offer to run 'kodo teams auto' when team build fails."""
    print(f"\n  Team {team_name!r} could not be built: {exc}", file=sys.stderr)
    print(
        "  This usually means some backends in the team config aren't installed.",
        file=sys.stderr,
    )
    try:
        answer = input("\n  Run 'kodo teams auto' to generate a working config? [Y/n] ")
    except (EOFError, KeyboardInterrupt):
        answer = "n"
    if answer.strip().lower() in ("", "y"):
        from kodo.cli._subcommands import _cmd_teams_auto_all

        _cmd_teams_auto_all()
        # Retry loading after auto-fix
        team_config = load_team_config(team_name, project_dir)
        team_preset = get_team(team_name)
        if team_config:
            team = build_team_from_json(team_config)
            system_prompt = (
                team_config.get("orchestrator_prompt") or team_preset.system_prompt
            )
            verifiers = team_config.get("verifiers")
            return team, system_prompt, verifiers
        else:
            team = team_preset.build_team()
            return team, team_preset.system_prompt, None
    else:
        _fail(f"Team {team_name!r} could not be built: {exc}")

# Will be set to the real stdout when --json redirects sys.stdout to stderr.
# Prefer using json_output_redirect() context manager for new code;
# the module-level variable is kept for backward compatibility with _main.py.
_original_stdout = None


@contextlib.contextmanager
def json_output_redirect():
    """Context manager that redirects stdout to stderr for JSON mode.

    Saves the real stdout so that JSON output can be emitted to it later.
    Restores sys.stdout on exit for test isolation.
    """
    global _original_stdout
    saved = sys.stdout
    _original_stdout = saved
    try:
        sys.stdout = sys.stderr
        yield saved
    finally:
        sys.stdout = saved
        _original_stdout = None


# ---------------------------------------------------------------------------
# Error / output helpers
# ---------------------------------------------------------------------------


def _fail(msg: str, code: int = 1) -> NoReturn:
    """Print error and exit. In JSON mode, outputs JSON to original stdout."""
    if _original_stdout is not None:
        sys.stdout = _original_stdout
        print(json.dumps(_format_json_output(error=msg)))
        sys.exit(EXIT_ERROR)
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(code)


def _emit_json_and_exit(args, result, improve_report: str | None = None) -> None:
    """If --json, emit result JSON to stdout and exit. Otherwise no-op."""
    if not args.json:
        return
    sys.stdout = _original_stdout
    print(
        json.dumps(_format_json_output(result, improve_report=improve_report), indent=2),
    )
    sys.exit(EXIT_SUCCESS if result.finished else EXIT_PARTIAL)


def _format_json_output(
    result: RunResult | None = None,
    error: str | None = None,
    improve_report: str | None = None,
) -> dict:
    """Build the structured JSON output dict."""
    if error is not None:
        return {"status": "error", "error": error}
    if result is None:
        return {"status": "error", "error": "No result available"}
    if result.finished:
        status = "completed"
    elif result.cycles:
        status = "partial"
    else:
        status = "failed"

    output = {
        "status": status,
        "finished": result.finished,
        "cycles": len(result.cycles) if result.cycles else 0,
        "exchanges": result.total_exchanges,
        "cost_usd": round(result.total_cost_usd, 4),
        "summary": result.summary,
    }

    if result.stage_results:
        output["stages"] = [
            {
                "index": sr.stage_index,
                "name": sr.stage_name,
                "finished": sr.finished,
                "summary": sr.summary,
                "cycles": len(sr.cycles),
            }
            for sr in result.stage_results
        ]

    if improve_report is not None:
        output["improve_report"] = improve_report

    return output


# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------


def launch_run(
    run_dir: RunDir,
    goal_text: str,
    params: dict,
    plan: GoalPlan | None = None,
    json_mode: bool = False,
    debug: bool = False,
):
    """Build team + orchestrator and run. Returns the RunResult."""
    # Snapshot config and goal into the run directory
    _atomic_write(run_dir.config_file, json.dumps(params, indent=2))
    if not run_dir.goal_file.exists():
        _atomic_write(run_dir.goal_file, goal_text)

    log_path = log.init(run_dir)
    log.emit(
        "cli_args", **params,
        goal_text=goal_text,
        project_dir=str(run_dir.project_dir),
        has_plan=plan is not None,
        debug=debug,
    )

    project_dir = run_dir.project_dir
    max_exchanges = params["max_exchanges"]
    max_cycles = params["max_cycles"]

    team_preset = get_team(params["team"])
    verifiers = None
    team_config = None

    if debug:
        # --- Debug mode: mock everything ---
        from kodo.debug import build_debug_team, build_mock_orchestrator, _allocator

        _allocator.reset()
        orch_letter = _allocator.next("orchestrator")
        team, debug_sessions = build_debug_team(params["team"])
        system_prompt = team_preset.system_prompt
        orchestrator, orch_session = build_mock_orchestrator(
            orch_letter, team, system_prompt=system_prompt,
        )
        debug_sessions["orchestrator"] = orch_session

        log.emit(
            "debug_run_start",
            mode="debug",
            goal=goal_text,
            letter_assignments=_allocator.assignments,
        )

        if not json_mode:
            print("\n  [DEBUG MODE — mocked backends]")
            print("  Letter assignments:")
            for letter, role in _allocator.assignments:
                print(f"    {letter} = {role}")
    else:
        # --- Normal mode: real backends ---
        debug_sessions = None

        # Try loading a team JSON config; fall back to built-in preset
        try:
            team_config = load_team_config(params["team"], project_dir)
            if team_config:
                team = build_team_from_json(team_config)
                system_prompt = (
                    team_config.get("orchestrator_prompt") or team_preset.system_prompt
                )
                verifiers = validate_verifiers(team_config.get("verifiers"), team)
            else:
                team = team_preset.build_team()
                system_prompt = team_preset.system_prompt
        except RuntimeError as exc:
            result = _try_auto_fix_team(params["team"], project_dir, exc)
            team, system_prompt, verifiers = result
        except (ValueError, KeyError, OSError) as exc:
            _fail(f"Invalid team config: {exc}")

        # Snapshot resolved team config for deterministic resume
        if team_config:
            _atomic_write(run_dir.team_file, json.dumps(team_config, indent=2))
        else:
            snapshot = team_to_json(
                team,
                orchestrator_prompt=system_prompt,
                verifiers=verifiers,
            )
            _atomic_write(run_dir.team_file, json.dumps(snapshot, indent=2))

        orchestrator = build_orchestrator(
            params["orchestrator"],
            params["orchestrator_model"],
            system_prompt=system_prompt,
        )

        # Preflight: smoke-test backends before committing to a long run
        preflight_warnings = preflight_check_backends(team)
        if preflight_warnings:
            if len(preflight_warnings) == len(team):
                # ALL backends failed — abort
                _fail(
                    "All backends failed preflight checks:\n"
                    + "\n".join(preflight_warnings)
                    + "\nFix the issues above or install a working backend.",
                )
            if not json_mode:
                print("\n  Backend preflight warnings:")
                for w in preflight_warnings:
                    print(w)
                print("  (Continuing — some backends may fail at runtime)\n")
            log.emit("preflight_warnings", warnings=preflight_warnings)

    if not json_mode:
        print(f"  Log: {log_path}")
        print()

    # Only allow auto-commit when project_dir is a git repo root.
    # If launched inside a subfolder of another repo, committing would
    # land changes in the parent — which is never what we want.
    auto_commit = params.get("auto_commit", True)
    is_own_git_repo = (project_dir / ".git").exists()
    if auto_commit and not is_own_git_repo:
        auto_commit = False
        if not json_mode:
            print("  ℹ  Auto-commit disabled (no .git in project directory)")
        log.emit("auto_commit_disabled", reason="no_git_root")

    result = orchestrator.run(
        goal_text,
        project_dir,
        team,
        max_exchanges=max_exchanges,
        max_cycles=max_cycles,
        plan=plan,
        verifiers=verifiers,
        auto_commit=auto_commit,
    )

    if not json_mode:
        print(f"\n{'=' * 50}")
        if result.stage_results:
            completed = sum(1 for sr in result.stage_results if sr.finished)
            print(
                f"Done: {completed}/{_plural(len(result.stage_results), 'stage')} completed, "
                f"{_plural(len(result.cycles), 'cycle')}, {_plural(result.total_exchanges, 'exchange')}, "
                f"${result.total_cost_usd:.4f}",
            )
        else:
            print(
                f"Done: {_plural(len(result.cycles), 'cycle')}, {_plural(result.total_exchanges, 'exchange')}, ${result.total_cost_usd:.4f}",
            )
        if result.summary:
            print(f"  {result.summary[:300]}")

    # Debug mode: print token flow summary
    if debug and debug_sessions is not None:
        _print_debug_summary(orchestrator, debug_sessions)

    return result


def _print_debug_summary(orchestrator, debug_sessions: dict) -> None:
    """Print the debug token flow summary after a mock run."""
    print()
    print("=" * 60)
    print("  DEBUG SUMMARY")
    print("=" * 60)

    for role, session in sorted(debug_sessions.items(), key=lambda x: x[1].letter):
        print(
            f"  {session.letter} ({role}): "
            f"generated {session.generated_tokens}, "
            f"saw {session.seen_tokens}",
        )
    print()


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------


def launch_resume(
    run_dir: RunDir, state: log.RunState, *, team_override: str | None = None,
) -> RunResult:
    """Resume an interrupted run from its parsed RunState. Returns the RunResult."""
    log.init_append(state.log_file)

    project_dir = run_dir.project_dir

    # Load params from run config if available; otherwise reconstruct from RunState
    required_keys = {
        "team",
        "orchestrator",
        "orchestrator_model",
        "max_exchanges",
        "max_cycles",
    }
    params = {}
    if run_dir.config_file.exists():
        try:
            loaded = json.loads(run_dir.config_file.read_text(encoding="utf-8"))
            # Accept old configs with "mode" key and re-save migrated version
            if isinstance(loaded, dict) and "mode" in loaded and "team" not in loaded:
                loaded["team"] = loaded.pop("mode")
                try:
                    _atomic_write(
                        run_dir.config_file,
                        json.dumps(loaded, indent=2),
                    )
                except (PermissionError, OSError):
                    pass  # best-effort
            if isinstance(loaded, dict) and required_keys <= loaded.keys():
                params = loaded
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            pass
    if not params:
        params = {
            "team": state.team_preset or "full",
            "orchestrator": "api" if state.orchestrator == "api" else "claude-code",
            "orchestrator_model": state.model,
            "max_exchanges": state.max_exchanges,
            "max_cycles": state.max_cycles,
        }

    try:
        team_preset = get_team(params["team"])
    except KeyError:
        logging.warning(
            "Saved team %r no longer exists, falling back to 'full'",
            params["team"],
        )
        if _original_stdout is None:
            print(
                f"  Warning: team {params['team']!r} no longer exists, using 'full'."
            )
        params["team"] = "full"
        team_preset = get_team("full")
    verifiers = None
    max_exchanges = params["max_exchanges"]
    max_cycles = params["max_cycles"]

    # Load team: --team override > snapshot from run dir > current config
    team_config = None
    if team_override:
        # User explicitly chose a different team for this resume
        try:
            team_config = load_team_config(team_override, project_dir)
        except (ValueError, KeyError, OSError):
            team_config = None
        if team_config is None:
            # Try built-in preset (will be handled below as team_config=None)
            try:
                team_preset = get_team(team_override)
            except KeyError:
                _fail(f"Unknown team: {team_override!r}")
    elif run_dir.team_file.exists():
        try:
            team_config = json.loads(run_dir.team_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            team_config = None

    if team_config is None and not team_override:
        # Backward compat: old runs without team.json
        try:
            team_config = load_team_config(params["team"], project_dir)
        except (ValueError, KeyError, OSError):
            team_config = None

    try:
        if team_config:
            team = build_team_from_json(team_config)
            system_prompt = (
                team_config.get("orchestrator_prompt") or team_preset.system_prompt
            )
            verifiers = validate_verifiers(team_config.get("verifiers"), team)
        else:
            team = team_preset.build_team()
            system_prompt = team_preset.system_prompt
    except RuntimeError as exc:
        result = _try_auto_fix_team(params["team"], project_dir, exc)
        team, system_prompt, verifiers = result
    except (ValueError, KeyError, OSError) as exc:
        _fail(f"Invalid team config: {exc}")

    orchestrator = build_orchestrator(
        params["orchestrator"],
        params["orchestrator_model"],
        system_prompt=system_prompt,
    )

    resume = ResumeState(
        completed_cycles=state.completed_cycles,
        prior_summary=state.last_summary,
        agent_session_ids=state.agent_session_ids,
        completed_stages=state.completed_stages,
        stage_summaries=state.stage_summaries,
        current_stage_cycles=state.current_stage_cycles,
        pending_exchanges=state.pending_exchanges,
    )

    # Load goal plan if this was a staged run
    plan: GoalPlan | None = None
    if state.has_stages:
        plan = _load_goal_plan(run_dir)
        if plan is None:
            _fail("Cannot resume: staged run but goal-plan.json not found or invalid.")

    if _original_stdout is None:
        print(f"\nResuming run: {state.run_id}")
        print(f"Team: {team_preset.name} — {team_preset.description}")
        print(f"Orchestrator: {params['orchestrator']} ({orchestrator.model})")
        print("Team:")
        for k, a in team.items():
            print(f"  {k} ({_backend_label(a)} / {a.session.model})")
        print(f"Completed cycles: {state.completed_cycles}/{state.max_cycles}")
        if state.has_stages:
            print(
                f"Completed stages: {len(state.completed_stages)}"
                + (f"/{plan and len(plan.stages)}" if plan else ""),
            )
        if state.agent_session_ids:
            print(f"Resuming sessions: {', '.join(state.agent_session_ids.keys())}")
        if state.pending_exchanges:
            print(
                f"Resuming mid-cycle: {_plural(len(state.pending_exchanges), 'exchange')} to restore",
            )
        print(f"Log: {state.log_file}")
        print()

    auto_commit = params.get("auto_commit", True)
    is_own_git_repo = (project_dir / ".git").exists()
    if auto_commit and not is_own_git_repo:
        auto_commit = False
        if _original_stdout is None:
            print("  ℹ  Auto-commit disabled (no .git in project directory)")
        log.emit("auto_commit_disabled", reason="no_git_root")

    result = orchestrator.run(
        state.goal,
        Path(state.project_dir),
        team,
        max_exchanges=max_exchanges,
        max_cycles=max_cycles,
        resume=resume,
        plan=plan,
        verifiers=verifiers,
        auto_commit=auto_commit,
    )

    total_cycles = state.completed_cycles + len(result.cycles)
    if _original_stdout is None:
        print(f"\n{'=' * 50}")
        print(
            f"Done: {_plural(total_cycles, 'total cycle')}, {_plural(result.total_exchanges, 'exchange')} (this session), "
            f"${result.total_cost_usd:.4f}",
        )
        if result.summary:
            print(f"  {result.summary[:300]}")

    return result
