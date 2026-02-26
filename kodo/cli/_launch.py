"""Run launch and resume logic, plus JSON output helpers."""

import json
import logging
import sys
from pathlib import Path

# Suppress noisy SDK info messages ("Using bundled Claude Code CLI: ...")
# that break the clean progress output.
logging.getLogger("claude_agent_sdk").setLevel(logging.WARNING)

from kodo import log
from kodo.cli._intake import _load_goal_plan
from kodo.cli._ui import _atomic_write, _backend_label
from kodo.factory import (
    build_orchestrator,
    get_team,
    preflight_check_backends,
)
from kodo.log import RunDir
from kodo.orchestrators.base import GoalPlan, ResumeState, RunResult
from kodo.team_config import build_team_from_json, load_team_config

# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------

EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_PARTIAL = 2


def _try_auto_fix_team(team_name: str, project_dir: Path, exc: Exception):
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

# Will be set to the real stdout when --json redirects sys.stdout to stderr
_original_stdout = None


# ---------------------------------------------------------------------------
# Error / output helpers
# ---------------------------------------------------------------------------


def _fail(msg: str, code: int = 1) -> None:
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
        json.dumps(_format_json_output(result, improve_report=improve_report), indent=2)
    )
    sys.exit(EXIT_SUCCESS if result.finished else EXIT_PARTIAL)


def _format_json_output(
    result=None, error: str | None = None, improve_report: str | None = None
) -> dict:
    """Build the structured JSON output dict."""
    if error is not None:
        return {"status": "error", "error": error}
    if result.finished:
        status = "completed"
    elif result.cycles is not None:
        status = "partial"
    else:
        status = "failed"

    output = {
        "status": status,
        "finished": result.finished,
        "cycles": len(result.cycles),
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
):
    """Build team + orchestrator and run. Returns the RunResult."""
    # Snapshot config and goal into the run directory
    _atomic_write(run_dir.config_file, json.dumps(params, indent=2))
    if not run_dir.goal_file.exists():
        _atomic_write(run_dir.goal_file, goal_text)

    log_path = log.init(run_dir)
    log.emit("cli_args", **params, goal_text=goal_text, has_plan=plan is not None)

    project_dir = run_dir.project_dir

    team_preset = get_team(params["team"])
    verifiers = None

    # Try loading a team JSON config; fall back to built-in preset
    try:
        team_config = load_team_config(params["team"], project_dir)
        if team_config:
            team = build_team_from_json(team_config)
            system_prompt = (
                team_config.get("orchestrator_prompt") or team_preset.system_prompt
            )
            verifiers = team_config.get("verifiers")
            max_exchanges = team_config.get("max_exchanges", params["max_exchanges"])
            max_cycles = team_config.get("max_cycles", params["max_cycles"])
        else:
            team = team_preset.build_team()
            system_prompt = team_preset.system_prompt
            max_exchanges = params["max_exchanges"]
            max_cycles = params["max_cycles"]
    except RuntimeError as exc:
        result = _try_auto_fix_team(params["team"], project_dir, exc)
        team, system_prompt, verifiers = result
        max_exchanges = params["max_exchanges"]
        max_cycles = params["max_cycles"]
    except (ValueError, KeyError, OSError) as exc:
        _fail(f"Invalid team config: {exc}")

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
                + "\nFix the issues above or install a working backend."
            )
        if not json_mode:
            print("\n⚠ Backend preflight warnings:")
            for w in preflight_warnings:
                print(w)
            print("  (Continuing — some backends may fail at runtime)\n")
        log.emit("preflight_warnings", warnings=preflight_warnings)

    if not json_mode:
        print(f"\nTeam: {team_preset.name} — {team_preset.description}")
        if team_config:
            team_name = team_config.get("name", "custom")
            print(f"Team config: {team_name}")
        print(f"Orchestrator: {params['orchestrator']} ({orchestrator.model})")
        print("Team:")
        for k, a in team.items():
            print(f"  {k} ({_backend_label(a)} / {a.session.model})")
        print(f"Project dir: {project_dir}")
        print(f"Max: {max_exchanges} exchanges/cycle, {max_cycles} cycles")
        if plan:
            print(f"Stages: {len(plan.stages)}")
        print(f"Log: {log_path}")
        print()

    result = orchestrator.run(
        goal_text,
        project_dir,
        team,
        max_exchanges=max_exchanges,
        max_cycles=max_cycles,
        plan=plan,
        verifiers=verifiers,
        auto_commit=params.get("auto_commit", True),
    )

    if not json_mode:
        print(f"\n{'=' * 50}")
        if result.stage_results:
            completed = sum(1 for sr in result.stage_results if sr.finished)
            print(
                f"Done: {completed}/{len(result.stage_results)} stage(s) completed, "
                f"{len(result.cycles)} cycle(s), {result.total_exchanges} exchanges, "
                f"${result.total_cost_usd:.4f}"
            )
        else:
            print(
                f"Done: {len(result.cycles)} cycle(s), {result.total_exchanges} exchanges, ${result.total_cost_usd:.4f}"
            )
        if result.summary:
            print(f"  {result.summary[:300]}")

    return result


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------


def launch_resume(run_dir: RunDir, state: log.RunState) -> RunResult:
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
            # Accept old configs with "mode" key
            if isinstance(loaded, dict) and "mode" in loaded and "team" not in loaded:
                loaded["team"] = loaded.pop("mode")
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

    team_preset = get_team(params["team"])
    verifiers = None

    try:
        team_config = load_team_config(params["team"], project_dir)
        if team_config:
            team = build_team_from_json(team_config)
            system_prompt = (
                team_config.get("orchestrator_prompt") or team_preset.system_prompt
            )
            verifiers = team_config.get("verifiers")
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
            + (f"/{plan and len(plan.stages)}" if plan else "")
        )
    if state.agent_session_ids:
        print(f"Resuming sessions: {', '.join(state.agent_session_ids.keys())}")
    if state.pending_exchanges:
        print(
            f"Resuming mid-cycle: {len(state.pending_exchanges)} exchange(s) to restore"
        )
    print(f"Log: {state.log_file}")
    print()

    result = orchestrator.run(
        state.goal,
        Path(state.project_dir),
        team,
        max_exchanges=params["max_exchanges"],
        max_cycles=params["max_cycles"],
        resume=resume,
        plan=plan,
        verifiers=verifiers,
        auto_commit=params.get("auto_commit", True),
    )

    total_cycles = state.completed_cycles + len(result.cycles)
    print(f"\n{'=' * 50}")
    print(
        f"Done: {total_cycles} total cycle(s), {result.total_exchanges} exchanges (this session), "
        f"${result.total_cost_usd:.4f}"
    )
    if result.summary:
        print(f"  {result.summary[:300]}")

    return result
