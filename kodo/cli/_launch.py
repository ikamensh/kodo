"""Run launch and resume logic, plus JSON output helpers."""

from __future__ import annotations

import contextlib
import json
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

# Suppress noisy SDK info messages ("Using bundled Claude Code CLI: ...")
# that break the clean progress output.
logging.getLogger("claude_agent_sdk").setLevel(logging.WARNING)

from kodo import log
from kodo.cli._intake import _load_goal_plan
from kodo.cli._ui import _atomic_write, _backend_label, _plural
from kodo.factory import (
    DEFAULT_MAX_CYCLES,
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
    team_name: str,
    project_dir: Path,
    exc: Exception,
) -> tuple[dict, str, dict | None]:
    """Offer to run 'kodo teams auto' when team build fails."""
    print(f"\n  Team {team_name!r} could not be built: {exc}", file=sys.stderr)
    print(
        "  This usually means some backends in the team config aren't installed.",
        file=sys.stderr,
    )
    try:
        answer = input("\n  Run 'kodo teams auto' to generate a working config? [Y/n] ")
    except (EOFError, KeyboardInterrupt, OSError):
        answer = "n"
    if answer.strip().lower() not in ("", "y"):
        _fail(f"Team {team_name!r} could not be built: {exc}")

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

    team = team_preset.build_team()
    return team, team_preset.system_prompt, None


def _build_team_from_config(
    team_config: dict | None,
    team_preset,
    team_name: str,
    project_dir: Path,
) -> tuple[dict, str, dict | None]:
    """Build team from JSON config or built-in preset, with auto-fix on failure."""
    try:
        if team_config:
            team = build_team_from_json(team_config)
            system_prompt = (
                team_config.get("orchestrator_prompt") or team_preset.system_prompt
            )
            verifiers = validate_verifiers(team_config.get("verifiers"), team)
            return team, system_prompt, verifiers
        else:
            team = team_preset.build_team()
            return team, team_preset.system_prompt, None
    except RuntimeError as exc:
        return _try_auto_fix_team(team_name, project_dir, exc)
    except (ValueError, KeyError, OSError) as exc:
        _fail(f"Invalid team config: {exc}")


# Map kodo effort levels to Claude SDK effort parameter.
# "standard" → None (don't override SDK default).
_EFFORT_TO_SDK: dict[str, str | None] = {
    "low": "low",
    "standard": None,
    "high": "high",
    "max": "max",
}


def _apply_effort_to_team(team: dict, effort: str) -> None:
    """Set effort on worker sessions that support it (currently Claude SDK)."""
    sdk_effort = _EFFORT_TO_SDK.get(effort)
    if sdk_effort is None:
        return
    from kodo.sessions.claude import ClaudeSession

    for agent in team.values():
        if isinstance(agent.session, ClaudeSession):
            agent.session.effort = sdk_effort


def _resolve_auto_commit(params: dict, project_dir: Path, quiet: bool = False) -> bool:
    """Resolve auto_commit, disabling it when project_dir has no .git root."""
    auto_commit = params.get("auto_commit", True)
    if auto_commit and not (project_dir / ".git").exists():
        auto_commit = False
        if not quiet:
            print("  ℹ  Auto-commit disabled (no .git in project directory)")
        log.emit("auto_commit_disabled", reason="no_git_root")
    return auto_commit


def _build_advisor(params: dict):
    """Create an Advisor for adaptive planning, or None if no API key."""
    import os

    from kodo.models import GEMINI_API_FLASH, resolve_model

    # Prefer Gemini Flash (cheapest)
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        advisor_model = f"google-gla:{GEMINI_API_FLASH}"
    elif os.environ.get("ANTHROPIC_API_KEY"):
        # Fall back to orchestrator model
        orch_model = params.get("orchestrator_model", "")
        advisor_model = resolve_model(orch_model)
    else:
        return None

    from kodo.orchestrators.advisor import Advisor

    return Advisor(model=advisor_model)


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


def _fail(msg: str, code: int = 1, *, prefix: str = "Error: ") -> NoReturn:
    """Print error and exit. In JSON mode, outputs JSON to original stdout."""
    if _original_stdout is not None:
        sys.stdout = _original_stdout
        print(json.dumps(_format_json_output(error=msg)))
        sys.exit(EXIT_ERROR)
    print(f"{prefix}{msg}", file=sys.stderr)
    sys.exit(code)


def _cancel(msg: str = "Cancelled.") -> NoReturn:
    """Print cancellation message to stderr and exit."""
    _fail(msg, prefix="")


def _emit_json_and_exit(
    args,
    result,
    improve_report: str | None = None,
    test_report: str | None = None,
) -> None:
    """If --json, emit result JSON to stdout and exit. Otherwise no-op."""
    if not args.json:
        return
    sys.stdout = _original_stdout
    print(
        json.dumps(
            _format_json_output(
                result,
                improve_report=improve_report,
                test_report=test_report,
            ),
            indent=2,
        ),
    )
    sys.exit(EXIT_SUCCESS if result.finished else EXIT_PARTIAL)


def _format_json_output(
    result: RunResult | None = None,
    error: str | None = None,
    improve_report: str | None = None,
    test_report: str | None = None,
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

    if test_report is not None:
        output["test_report"] = test_report

    return output


# ---------------------------------------------------------------------------
# Team / orchestrator (shared: pre-discovery for test|improve, or launch_run)
# ---------------------------------------------------------------------------


@dataclass
class LaunchAssets:
    """Resolved team + orchestrator after preflight (no run yet)."""

    team_preset: object
    team: dict
    orchestrator: object
    verifiers: dict | None
    debug_sessions: dict | None = None
    debug_letter_assignments: list[tuple[str, str]] | None = None


def build_launch_assets(
    run_dir: RunDir,
    params: dict,
    goal_text: str,
    *,
    json_mode: bool,
    debug: bool,
) -> LaunchAssets:
    """Build team, orchestrator, run preflight, snapshot team file (same as launch_run)."""
    project_dir = run_dir.project_dir
    team_preset = get_team(params["team"])
    verifiers = None
    team_config = None

    if debug:
        from kodo.debug import build_debug_team, build_mock_orchestrator, _allocator

        _allocator.reset()
        orch_letter = _allocator.next("orchestrator")
        team, debug_sessions = build_debug_team(params["team"])
        system_prompt = team_preset.system_prompt
        orchestrator, orch_session = build_mock_orchestrator(
            orch_letter,
            team,
            system_prompt=system_prompt,
        )
        debug_sessions["orchestrator"] = orch_session

        log.emit(
            "debug_run_start",
            mode="debug",
            goal=goal_text,
            letter_assignments=_allocator.assignments,
        )

        return LaunchAssets(
            team_preset=team_preset,
            team=team,
            orchestrator=orchestrator,
            verifiers=verifiers,
            debug_sessions=debug_sessions,
            debug_letter_assignments=list(_allocator.assignments),
        )

    try:
        team_config = load_team_config(params["team"], project_dir)
    except (ValueError, KeyError, OSError) as exc:
        _fail(f"Invalid team config: {exc}")
    team, system_prompt, verifiers = _build_team_from_config(
        team_config,
        team_preset,
        params["team"],
        project_dir,
    )

    effort = params.get("effort", "standard")
    if effort != "standard":
        from kodo.prompts.roles import build_orchestrator_prompt

        system_prompt = build_orchestrator_prompt(system_prompt, effort=effort)

    _apply_effort_to_team(team, effort)

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

    preflight_warnings = preflight_check_backends(team)
    if preflight_warnings:
        if len(preflight_warnings) == len(team):
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

    return LaunchAssets(
        team_preset=team_preset,
        team=team,
        orchestrator=orchestrator,
        verifiers=verifiers,
        debug_sessions=None,
        debug_letter_assignments=None,
    )


def print_launch_identity(
    assets: LaunchAssets,
    params: dict,
    log_path: Path,
    *,
    json_mode: bool,
    debug: bool,
) -> None:
    """Human-readable Team / Orchestrator / Agents / Log (same lines as launch_run)."""
    if json_mode:
        return
    if debug:
        print("\n  [DEBUG MODE — mocked backends]")
        print("  Letter assignments:")
        for letter, role in assets.debug_letter_assignments or ():
            print(f"    {letter} = {role}")
    print(f"  Team: {assets.team_preset.name}")
    _emo = getattr(assets.orchestrator, "_emoji", "")
    _emo_s = f"{_emo} " if _emo else ""
    print(f"  Orchestrator: {_emo_s}{params['orchestrator']} ({assets.orchestrator.model})")
    print("  Agents:")
    for k, a in assets.team.items():
        print(f"    {k} ({_backend_label(a)} / {a.session.model})")
    print(f"  Log: {log_path}")
    from kodo.cli._interactive import is_interactive

    if is_interactive(json_mode):
        from kodo.formatting import DIM, RESET

        print(f"  {DIM}Type feedback anytime. /stop to end gracefully.{RESET}")
    print()


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
    intake_session=None,
    prepared: LaunchAssets | None = None,
    skip_identity_print: bool = False,
):
    """Build team + orchestrator and run. Returns the RunResult."""
    # Snapshot config and goal into the run directory
    _atomic_write(run_dir.config_file, json.dumps(params, indent=2))
    if not run_dir.goal_file.exists():
        _atomic_write(run_dir.goal_file, goal_text)

    log_path = log.init(run_dir)
    log.emit(
        "cli_args",
        **params,
        goal_text=goal_text,
        project_dir=str(run_dir.project_dir),
        has_plan=plan is not None,
        debug=debug,
    )

    project_dir = run_dir.project_dir
    max_exchanges = params["max_exchanges"]
    max_cycles = params["max_cycles"]

    if prepared is None:
        assets = build_launch_assets(
            run_dir, params, goal_text, json_mode=json_mode, debug=debug
        )
    else:
        assets = prepared

    if not json_mode and not skip_identity_print:
        print_launch_identity(
            assets, params, log_path, json_mode=json_mode, debug=debug
        )

    team = assets.team
    orchestrator = assets.orchestrator
    verifiers = assets.verifiers
    debug_sessions = assets.debug_sessions

    auto_commit = (
        False if debug else _resolve_auto_commit(params, project_dir, quiet=json_mode)
    )
    effort = params.get("effort", "standard")

    # Create advisor for adaptive planning when plan has stages
    # Priority: intake session (full context) → pydantic-ai advisor → None
    advisor = None
    if plan and plan.stages and not debug:
        if intake_session is not None:
            from kodo.orchestrators.advisor import SessionAdvisor

            advisor = SessionAdvisor(intake_session, project_dir)
            log.emit("advisor_type", type="session")
        else:
            advisor = _build_advisor(params)
            if advisor:
                log.emit("advisor_type", type="pydantic-ai")
        # Adaptive mode: bump cycle cap if user didn't explicitly set --cycles
        if advisor and max_cycles == DEFAULT_MAX_CYCLES:
            max_cycles = max(max_cycles, 50)

    from kodo.advisory import AdvisoryQueue
    from kodo.cli._interactive import is_interactive, run_with_interactive_input

    advisory_queue = AdvisoryQueue()
    run_args = (goal_text, project_dir, team)
    run_kwargs = dict(
        max_exchanges=max_exchanges,
        max_cycles=max_cycles,
        plan=plan,
        verifiers=verifiers,
        auto_commit=auto_commit,
        effort=effort,
        advisor=advisor,
        enable_coach=params.get("enable_coach", False),
    )

    try:
        if is_interactive(json_mode):
            result = run_with_interactive_input(
                orchestrator, run_args, run_kwargs, advisory_queue
            )
        else:
            result = orchestrator.run(
                *run_args, **run_kwargs, advisory_queue=advisory_queue
            )
    finally:
        if advisor is not None:
            advisor.close()

    if not json_mode:
        log.print_stats_table(final=True)
        _print_run_summary(result)
        log_file = log.get_log_file()
        if log_file and log_file.exists():
            print(f"\n  View run: uv run python -m kodo.viewer {log_file}\n")

    # Debug mode: print token flow summary
    if debug and debug_sessions is not None:
        _print_debug_summary(debug_sessions)

    return result


def _print_run_summary(result: RunResult, total_cycles: int | None = None) -> None:
    """Print end-of-run summary with stages and wall-clock time."""
    cycles = total_cycles if total_cycles is not None else len(result.cycles)
    elapsed = log._fmt_time(time.monotonic() - (log._start_time or time.monotonic()))

    print(f"{'=' * 50}")
    if result.stage_results:
        completed = sum(1 for sr in result.stage_results if sr.finished)
        print(
            f"Done: {completed}/{_plural(len(result.stage_results), 'stage')} completed, "
            f"{_plural(cycles, 'cycle')}, {_plural(result.total_exchanges, 'exchange')}, "
            f"${result.total_cost_usd:.4f}, {elapsed}",
        )
        for sr in result.stage_results:
            mark = "+" if sr.finished else "-"
            label = f"  {mark} Stage {sr.stage_index}: {sr.stage_name}"
            if sr.summary:
                label += f" — {sr.summary[:120]}"
            print(label)
    else:
        print(
            f"Done: {_plural(cycles, 'cycle')}, {_plural(result.total_exchanges, 'exchange')}, "
            f"${result.total_cost_usd:.4f}, {elapsed}",
        )

    if result.summary:
        print(f"  {result.summary[:300]}")


def _print_debug_summary(debug_sessions: dict) -> None:
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
    run_dir: RunDir,
    state: log.RunState,
    *,
    team_override: str | None = None,
    orchestrator_override: str | None = None,
    exchanges_override: int | None = None,
    cycles_override: int | None = None,
    effort_override: str | None = None,
    debug: bool = False,
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

    # Apply CLI overrides on top of saved config
    overrides: dict[str, str] = {}
    if orchestrator_override:
        from kodo.cli._params import _parse_orchestrator_flag

        explicit_backend, orch_model = _parse_orchestrator_flag(orchestrator_override)
        if explicit_backend:
            params["orchestrator"] = explicit_backend
            if orch_model:
                params["orchestrator_model"] = orch_model
        elif orch_model:
            from kodo.models import implied_orchestrator_from_model

            inferred = implied_orchestrator_from_model(orch_model)
            params["orchestrator"] = inferred or "api"
            params["orchestrator_model"] = orch_model
        overrides["orchestrator"] = (
            f"{params['orchestrator']}:{params['orchestrator_model']}"
        )
        if not debug:
            from kodo.factory import check_api_key

            key_err = check_api_key(
                params["orchestrator"], params["orchestrator_model"]
            )
            if key_err:
                _fail(key_err)
    if exchanges_override is not None and exchanges_override > 0:
        params["max_exchanges"] = exchanges_override
        overrides["max_exchanges"] = str(exchanges_override)
    if cycles_override is not None and cycles_override > 0:
        params["max_cycles"] = cycles_override
        overrides["max_cycles"] = str(cycles_override)
    if effort_override:
        params["effort"] = effort_override
        overrides["effort"] = effort_override

    try:
        team_preset = get_team(params["team"])
    except KeyError:
        logging.warning(
            "Saved team %r no longer exists, falling back to 'full'",
            params["team"],
        )
        if _original_stdout is None:
            print(f"  Warning: team {params['team']!r} no longer exists, using 'full'.")
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

    if debug:
        # --- Debug mode: mock everything (same as launch_run) ---
        from kodo.debug import build_debug_team, build_mock_orchestrator, _allocator

        verifiers = None
        _allocator.reset()
        orch_letter = _allocator.next("orchestrator")
        team, _ = build_debug_team(params["team"])
        system_prompt = team_preset.system_prompt
        orchestrator, _ = build_mock_orchestrator(
            orch_letter,
            team,
            system_prompt=system_prompt,
        )
        if _original_stdout is None:
            print("\n  [DEBUG MODE — mocked backends]")
    else:
        team, system_prompt, verifiers = _build_team_from_config(
            team_config,
            team_preset,
            params["team"],
            project_dir,
        )

    # Apply effort-level adjustments (same as launch_run)
    effort = params.get("effort", "standard")
    if effort != "standard":
        from kodo.prompts.roles import build_orchestrator_prompt

        system_prompt = build_orchestrator_prompt(system_prompt, effort=effort)
    _apply_effort_to_team(team, effort)

    if not debug:
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

    if overrides:
        log.emit("resume_overrides", **overrides)

    if _original_stdout is None:
        print(f"\nResuming run: {state.run_id}")
        if overrides:
            print(f"Overrides: {', '.join(f'{k}={v}' for k, v in overrides.items())}")
        print(f"Team: {team_preset.name}")
        _emo = getattr(orchestrator, "_emoji", "")
        _emo_s = f"{_emo} " if _emo else ""
        print(f"Orchestrator: {_emo_s}{params['orchestrator']} ({orchestrator.model})")
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

    auto_commit = _resolve_auto_commit(
        params, project_dir, quiet=_original_stdout is not None
    )
    effort = params.get("effort", "standard")

    # Create fresh advisor for adaptive planning on resume
    advisor = None
    if plan and plan.stages:
        advisor = _build_advisor(params)
        if advisor and max_cycles == DEFAULT_MAX_CYCLES:
            max_cycles = max(max_cycles, 50)

    from kodo.advisory import AdvisoryQueue
    from kodo.cli._interactive import is_interactive, run_with_interactive_input

    advisory_queue = AdvisoryQueue()
    json_mode = _original_stdout is not None
    run_args = (state.goal, Path(state.project_dir), team)
    run_kwargs = dict(
        max_exchanges=max_exchanges,
        max_cycles=max_cycles,
        resume=resume,
        plan=plan,
        verifiers=verifiers,
        auto_commit=auto_commit,
        effort=effort,
        advisor=advisor,
        enable_coach=params.get("enable_coach", False),
    )

    if is_interactive(json_mode):
        result = run_with_interactive_input(
            orchestrator, run_args, run_kwargs, advisory_queue
        )
    else:
        result = orchestrator.run(
            *run_args, **run_kwargs, advisory_queue=advisory_queue
        )

    if not json_mode:
        log.print_stats_table(final=True)
        total_cycles = state.completed_cycles + len(result.cycles)
        _print_run_summary(result, total_cycles=total_cycles)
        log_file = log.get_log_file()
        if log_file and log_file.exists():
            print(f"\n  View run: uv run python -m kodo.viewer {log_file}\n")

    return result
