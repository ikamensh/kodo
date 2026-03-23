"""Subcommand handlers: runs, backends, teams, update, issue."""

import argparse
import json
import os
import platform
import subprocess
import sys
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Any

import questionary

from kodo import __version__, log
from kodo.cli._launch import _cancel, _fail
from kodo.cli._teams_delete_pick import _ask_teams_delete_checkbox
from kodo.formatting import BOLD, CYAN, DIM, GREEN, RED, RESET
from kodo.models import (
    CLAUDE_OPUS,
    CLAUDE_SONNET,
    CODEX_WORKER,
    CURSOR_COMPOSER,
    GEMINI_CLI_FLASH,
    GEMINI_CLI_PRO,
)


def _truncate_word(text: str, width: int) -> str:
    """Truncate *text* to at most *width* chars on a word boundary."""
    if len(text) <= width:
        return text
    cut = text[:width].rsplit(" ", 1)[0]
    # If the very first word is longer than width, hard-cut it.
    if not cut:
        cut = text[:width]
    return cut + "..."


def _pick_run(
    runs: list["log.RunState"],
    *,
    prompt: str = "Select run:",
    skip_prompts: bool = False,
) -> "log.RunState | None":
    """Let user pick from runs. Returns selected RunState or None if cancelled.
    When skip_prompts or single run, returns runs[0] without prompting."""
    if not runs:
        return None
    if len(runs) == 1 or skip_prompts:
        return runs[0]
    choices = [
        questionary.Choice(
            title=f"{r.run_id}  {'done' if r.finished else f'cycle {r.completed_cycles}/{r.max_cycles}'}  {_truncate_word(r.goal.replace('\n', ' '), 50)}",
            value=r.run_id,
        )
        for r in runs
    ]
    selected_id = questionary.select(prompt, choices=choices).ask()
    if selected_id is None:
        return None
    return next(r for r in runs if r.run_id == selected_id)


# ---------------------------------------------------------------------------
# kodo runs
# ---------------------------------------------------------------------------


def _cmd_runs() -> None:
    """List all known runs from ~/.kodo/runs/."""
    parser = argparse.ArgumentParser(description="List kodo runs")
    parser.add_argument(
        "project_dir",
        nargs="?",
        default=None,
        help="Filter to runs for this project directory",
    )
    args = parser.parse_args(sys.argv[2:])

    project = Path(args.project_dir).resolve() if args.project_dir else None
    runs = log.list_runs(project)

    if not runs:
        print("No runs found.")
        return

    id_w = max(len(r.run_id) for r in runs)
    dir_w = max(len(r.project_dir) for r in runs)

    header = f"  {'RUN ID':<{id_w}}  {'STATUS':<10}  {'PROJECT':<{dir_w}}  GOAL"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for r in runs:
        status = "done" if r.finished else f"cycle {r.completed_cycles}/{r.max_cycles}"
        goal_snippet = _truncate_word(r.goal.replace("\n", " "), 60)
        print(
            f"  {r.run_id:<{id_w}}  {status:<10}  {r.project_dir:<{dir_w}}  {goal_snippet}",
        )


# ---------------------------------------------------------------------------
# kodo logs
# ---------------------------------------------------------------------------


def _cmd_logs() -> None:
    """Open the log viewer in a browser. Serves logs on a local HTTP port."""
    parser = argparse.ArgumentParser(
        prog="kodo logs",
        description="Open log viewer in browser",
    )
    parser.add_argument("logfile", nargs="?", help="Path to a specific .jsonl log file")
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="HTTP port (default: 8080)",
    )
    args = parser.parse_args(sys.argv[2:])

    from kodo.viewer import _serve

    log_path = None
    if args.logfile:
        log_path = Path(args.logfile)
        if not log_path.exists():
            _fail(f"File not found: {log_path}")

    _serve(args.port, log_path)


# ---------------------------------------------------------------------------
# kodo issue
# ---------------------------------------------------------------------------

_ISSUE_REPO = "ikamensh/kodo"


def _open_folder(path: Path) -> bool:
    """Open the folder in the system file manager. Returns True if successful."""
    path = path.resolve()
    if not path.is_dir():
        return False
    try:
        system = platform.system()
        if system == "Darwin":
            subprocess.run(["open", str(path)], check=True, capture_output=True)
        elif system == "Windows":
            subprocess.run(["explorer", str(path)], check=True, capture_output=True)
        else:
            subprocess.run(["xdg-open", str(path)], check=True, capture_output=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _cmd_issue() -> None:
    """Open GitHub new-issue page with run context pre-filled."""
    parser = argparse.ArgumentParser(
        prog="kodo issue",
        description="Report a bug: open GitHub issues with run context pre-filled",
    )
    parser.add_argument(
        "run_id",
        nargs="?",
        default=None,
        help="Run ID (default: latest run for project)",
    )
    parser.add_argument(
        "--project",
        type=str,
        default=".",
        help="Project directory (default: current)",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Print URL only, do not open browser",
    )
    args = parser.parse_args(sys.argv[2:])

    project_dir = Path(args.project).resolve()
    if not project_dir.exists() or not project_dir.is_dir():
        _fail(f"Project path does not exist or is not a directory: {project_dir}")

    state: log.RunState
    if args.run_id:
        run_dir = log._runs_root() / args.run_id
        run_log = run_dir / "log.jsonl"
        if not run_log.exists():
            run_log = run_dir / "run.jsonl"  # legacy
        if not run_log.exists():
            _fail(f"Run not found: {args.run_id}")
        state = log.parse_run(run_log)
        if state is None:
            _fail(f"Could not parse run: {run_log}")
    else:
        runs = log.list_runs(project_dir)
        if not runs:
            _fail("No runs found. Specify a run ID or run kodo first.")
        state = _pick_run(runs, prompt="Select run to report:")
        if state is None:
            _cancel()

    desc = questionary.text(
        "Describe what went wrong (leave empty if crash/error is obvious):",
        default="",
    ).ask()
    if desc is None:
        _cancel()

    status = (
        "done"
        if state.finished
        else f"interrupted at cycle {state.completed_cycles}/{state.max_cycles}"
    )
    body_parts = [
        f"**Run:** {state.run_id}",
        f"**Goal:** {state.goal[:500]}{'...' if len(state.goal) > 500 else ''}",
        f"**Status:** {status}",
        f"**kodo:** {__version__}",
        "",
    ]
    if desc.strip():
        body_parts.append(desc.strip())
        body_parts.append("")
    run_dir = log._runs_root() / state.run_id
    from kodo.trace_upload import pack_run_archive

    archive = pack_run_archive(run_dir)
    archive_path = archive.path
    body_parts.append("---")
    body_parts.append("")
    body_parts.append(
        "**[TODO] Please attach the run archive:** drag and drop `run.tar.gz` from the folder that opened "
        f"(or from `~/.kodo/runs/{state.run_id}/`) into this issue. The archive contains log, config, goal, and conversations — essential for debugging."
    )
    body_parts.append("")
    body_parts.append(
        "The archive is scrubbed for common secrets and PII, but still verify it manually before submitting."
    )

    title = f"Bug report: run {state.run_id}"
    body = "\n".join(body_parts)
    url = (
        f"https://github.com/{_ISSUE_REPO}/issues/new"
        f"?title={urllib.parse.quote(title)}"
        f"&body={urllib.parse.quote(body)}"
    )

    if not args.no_open:
        webbrowser.open(url)
        _open_folder(run_dir)
        print()
        print("  GitHub issue form opened in your browser.")
        print(
            "  Run folder opened — attach run.tar.gz to the issue (drag & drop or click to add)."
        )
        print(
            "  Archive scrubbed for common secrets/PII; verify manually before submitting."
        )
    else:
        print()
        print("  To report this bug:")
        print("  1. Open the URL below in your browser")
        print(
            "  2. Attach run.tar.gz from the run folder (drag & drop or click to add)"
        )
        print(
            "  3. Archive scrubbed for common secrets/PII; verify manually before submitting"
        )
    print()
    print(f"  Archive: {archive_path}")
    print(
        f"  Scrubbed: {archive.stats.redactions} sensitive values across {archive.stats.files_changed} file(s)"
    )
    print()
    print("  Issue URL:")
    print(f"  {url}")


# ---------------------------------------------------------------------------
# kodo update
# ---------------------------------------------------------------------------


def _cmd_update() -> None:
    """Reinstall kodo from the latest version on GitHub."""
    import shutil

    if not shutil.which("uv"):
        _fail("uv is required for updating. Install it: https://docs.astral.sh/uv/")

    print("Updating kodo...")
    result = subprocess.run(["uv", "tool", "upgrade", "kodo", "--reinstall"])
    sys.exit(result.returncode)


# ---------------------------------------------------------------------------
# kodo backends
# ---------------------------------------------------------------------------


def _cmd_backends() -> None:
    """List available backends (CLI agents and API orchestrator models)."""
    import os
    import sys
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from kodo.factory import (
        available_backends,
        check_backend_status,
    )
    from kodo.formatting import (
        DIM as _DIM,
        GREEN as _GRN,
        RESET as _RST,
        YELLOW as _YLW,
    )
    from kodo.models import PROVIDER_REGISTRY

    available_backends.cache_clear()
    backends = available_backends()

    _INSTALL_LINKS: dict[str, str] = {
        "claude": "https://docs.anthropic.com/en/docs/claude-code",
        "codex": "https://github.com/openai/codex",
        "cursor": "https://docs.cursor.com/agent",
        "gemini-cli": "https://github.com/google-gemini/gemini-cli",
        "kimi": "https://platform.moonshot.cn",
    }

    # --- CLI backends (agents) ---
    installed = [name for name, present in backends.items() if present]
    missing = [name for name, present in backends.items() if not present]

    is_tty = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()

    # Show a spinner line while checking installed backends in parallel
    if installed and is_tty:
        print(f"  checking {len(installed)} backends...", end="\r", flush=True)

    # Check all installed backends in parallel
    status_results: dict[str, tuple[str, str | None]] = {}
    with ThreadPoolExecutor(max_workers=len(installed) or 1) as pool:
        futures = {
            pool.submit(check_backend_status, name): name for name in installed
        }
        for future in as_completed(futures):
            status_results[futures[future]] = future.result()

    # Clear the spinner line
    if installed and is_tty:
        print(" " * 40, end="\r", flush=True)

    print("CLI backends (agents):")
    for name, present in backends.items():
        if not present:
            link = _INSTALL_LINKS.get(name, "")
            print(f"  {_DIM}{name:<12}  not found  {link}{_RST}")
            continue

        version, warning = status_results[name]

        if warning:
            print(f"  {_YLW}{name:<12}  {version}{_RST}")
            print(f"  {_YLW}{'':14}{warning}{_RST}")
        else:
            print(f"  {_GRN}{name:<12}{_RST}  {version}")

    # --- API key status ---
    print("\nAPI keys:")

    def _masked(val: str) -> str:
        return f"{val[:4]}...{val[-4:]}" if len(val) > 12 else "***"

    for provider in PROVIDER_REGISTRY:
        key_val = None
        key_source = None
        for var in provider.env_vars:
            val = os.environ.get(var)
            if val:
                key_val = val
                key_source = var
                break
        if key_val:
            print(
                f"  {_GRN}{provider.name:<22}{_RST} set via {key_source} ({_masked(key_val)})"
            )
        else:
            key_names = ", ".join(provider.env_vars)
            print(f"  {_YLW}{provider.name:<22} not set  ({key_names}){_RST}")


# ---------------------------------------------------------------------------
# kodo teams
# ---------------------------------------------------------------------------


def _print_team_blocks(
    teams: list[tuple[str, str, dict[str, Any], Path]],
    backends: dict[str, bool],
) -> bool:
    """Print the same team cards as ``kodo teams``. Returns True if any backend is missing."""
    from kodo.factory import smart_model_for_backend
    from kodo.team_config import _BACKEND_MAP

    try:
        term_width = os.get_terminal_size().columns
    except (OSError, ValueError):
        term_width = 120
    _PREFIX_WIDTH = 4 + 20 + 2 + 12 + 2 + 20 + 2 + 9 + 2
    desc_width = max(term_width - _PREFIX_WIDTH, 10)

    has_missing = False
    for name, source, cfg, path in teams:
        desc = cfg.get("description", "")
        agents = cfg.get("agents", {})

        print(f"{BOLD}{CYAN}{name}{RESET}")
        if desc:
            print(f"  {desc}")
        if source != "built-in":
            print(f"  {DIM}{path}{RESET}")

        for akey, acfg in agents.items():
            backend = acfg.get("backend", "?")
            raw_model = acfg.get("model")
            if raw_model:
                model = raw_model
            else:
                bkey = _BACKEND_MAP.get(backend, "")
                try:
                    model = f"default ({smart_model_for_backend(bkey)})"
                except KeyError:
                    model = "default"
            raw_desc = acfg.get("description", "").split("\n")[0].strip()
            adesc = (
                f"  {DIM}{_truncate_word(raw_desc, desc_width)}{RESET}"
                if raw_desc
                else ""
            )
            backend_key = _BACKEND_MAP.get(backend, "")
            ok = backends.get(backend_key, False)
            if ok:
                status = f"{GREEN}ok{RESET}"
            else:
                status = f"{RED}missing{RESET}"
                has_missing = True
            print(f"    {akey:<20}  {backend:<12}  {model:<20}  [{status}]{adesc}")
        print()

    return has_missing


def _cmd_teams() -> None:
    """Dispatch `kodo teams [add|edit|delete|auto] ...`."""
    args = sys.argv[2:]

    if not args:
        _cmd_teams_list()
        return

    subcmd = args[0]
    if subcmd in ("--help", "-h"):
        print("Usage: kodo teams [add <name> | edit <name> | delete | auto [mode]]")
        print()
        print("  (no args)       List all available teams")
        print("  add <name>      Create a new team configuration")
        print("  edit <name>     Edit an existing team configuration")
        print(
            "  delete          Interactively remove ~/.kodo/teams/*.json (built-ins unchanged)"
        )
        print("  remove          Same as delete")
        print("  auto            Generate teams adapted to installed backends")
        return
    if subcmd == "add":
        if len(args) < 2:
            _fail("Usage: kodo teams add <name>")
        try:
            from kodo.tips import record_subcommand

            record_subcommand("teams_add")
        except Exception:
            pass
        _cmd_teams_add(args[1])
    elif subcmd == "edit":
        if len(args) < 2:
            _fail("Usage: kodo teams edit <name>")
        try:
            from kodo.tips import record_subcommand

            record_subcommand("teams_edit")
        except Exception:
            pass
        _cmd_teams_edit(args[1])
    elif subcmd in ("delete", "remove"):
        if len(args) > 1:
            _fail("Usage: kodo teams delete")
        try:
            from kodo.tips import record_subcommand

            record_subcommand("teams_delete")
        except Exception:
            pass
        _cmd_teams_delete()
    elif subcmd == "auto":
        try:
            from kodo.tips import record_subcommand

            record_subcommand("teams_auto")
        except Exception:
            pass
        mode_name = args[1] if len(args) >= 2 else None
        if mode_name:
            _cmd_teams_auto(mode_name)
        else:
            _cmd_teams_auto_all()
    else:
        _fail(
            f"Unknown teams subcommand: {subcmd}\n"
            "Usage: kodo teams [add <name> | edit <name> | delete | auto [mode]]"
        )


def _cmd_teams_list() -> None:
    """List all available teams (built-in and user-defined)."""
    from kodo.factory import available_backends
    from kodo.team_config import list_available_teams

    available_backends.cache_clear()
    backends = available_backends()

    teams = list_available_teams()
    if not teams:
        print("No teams found.")
        return

    has_missing = _print_team_blocks(teams, backends)

    # Hint if any agents have missing backends
    if has_missing:
        print(
            "Hint: Run 'kodo teams auto' to generate teams adapted to your installed backends.",
        )
        print()


def _cmd_teams_auto_all() -> None:
    """Generate configs for all built-in team templates."""
    from kodo.team_config import _defaults_dir

    # Read built-in templates directly (not through list_available_teams)
    # to avoid being shadowed by user teams with the same name.
    built_in_names = [
        p.stem.removeprefix("team-")
        for p in sorted(_defaults_dir().glob("team-*.json"))
    ]
    if not built_in_names:
        _fail("No built-in team templates found.")

    for name in built_in_names:
        _cmd_teams_auto(name)
        print()


def _cmd_teams_auto(mode_name: str) -> None:
    """Generate a viable team config from available backends."""
    from kodo.factory import available_backends
    from kodo.team_config import _BACKEND_MAP, list_available_teams

    available_backends.cache_clear()
    backends = available_backends()

    has = dict(backends.items())
    any_available = any(has.values())

    if not any_available:
        _fail(
            "No backends available. Install at least one of:\n"
            "  claude, cursor, codex, gemini-cli\n"
            "Run 'kodo backends' for install links."
        )

    # Find the base template (built-in or user team matching mode_name)
    base_config = None
    for tname, _tsource, tcfg, _tpath in list_available_teams():
        if tname == mode_name:
            base_config = tcfg
            break

    if base_config is None:
        available = ", ".join(t[0] for t in list_available_teams())
        _fail(
            f"No template found for mode {mode_name!r}.\nAvailable templates: {available}"
        )

    # Filter agents to only those with available backends
    src_agents = base_config.get("agents", {})
    agents = {}
    skipped = []
    for akey, acfg in src_agents.items():
        backend = acfg.get("backend", "")
        backend_key = _BACKEND_MAP.get(backend, "")
        if backends.get(backend_key, False):
            agents[akey] = acfg
        else:
            skipped.append((akey, backend))

    # Try to fill essential roles with fallback backends
    # Priority for fast worker: cursor > codex > gemini-cli > claude
    # Priority for smart worker: claude > gemini-cli
    _FAST_FALLBACKS = [
        ("cursor", CURSOR_COMPOSER),
        ("codex", CODEX_WORKER),
        ("gemini-cli", GEMINI_CLI_FLASH),
        ("claude", CLAUDE_SONNET),
    ]
    _SMART_FALLBACKS = [
        ("claude", CLAUDE_OPUS),
        ("gemini-cli", GEMINI_CLI_PRO),
        ("cursor", CURSOR_COMPOSER),
    ]

    def _find_fallback(
        fallbacks: list[tuple[str, str]],
    ) -> tuple[str, str] | None:
        for fb_backend, fb_model in fallbacks:
            if backends.get(fb_backend, False):
                return fb_backend, fb_model
        return None

    if "worker_fast" not in agents and "worker_fast" in src_agents:
        fb = _find_fallback(_FAST_FALLBACKS)
        if fb:
            agents["worker_fast"] = {
                **src_agents["worker_fast"],
                "backend": fb[0],
                "model": fb[1],
            }

    if "worker_smart" not in agents and "worker_smart" in src_agents:
        fb = _find_fallback(_SMART_FALLBACKS)
        if fb:
            agents["worker_smart"] = {
                **src_agents["worker_smart"],
                "backend": fb[0],
                "model": fb[1],
            }
            if fb[0] != "claude":
                # Remove claude-specific fields
                agents["worker_smart"].pop("fallback_model", None)

    # For tester/architect, try to fill with available backends
    for role, fallbacks in [
        ("tester", _FAST_FALLBACKS),
        ("architect", _SMART_FALLBACKS),
    ]:
        if role not in agents and role in src_agents:
            fb = _find_fallback(fallbacks)
            if fb:
                agents[role] = {
                    **src_agents[role],
                    "backend": fb[0],
                    "model": fb[1],
                }
                if fb[0] != "claude":
                    agents[role].pop("fallback_model", None)

    if not agents:
        _fail("Could not create any agents with available backends.")

    # Build verifiers from agents that are actually present
    src_verifiers = base_config.get("verifiers", {})
    verifiers = {}
    for role, agent_keys in src_verifiers.items():
        verifiers[role] = [k for k in agent_keys if k in agents]

    config = {
        "name": mode_name,
        "description": base_config.get("description", ""),
        "verifiers": verifiers,
        "agents": agents,
    }
    if "orchestrator_prompt" in base_config:
        config["orchestrator_prompt"] = base_config["orchestrator_prompt"]

    # Show what we generated
    print(f"Generated team {mode_name!r} for your setup:\n")
    for akey, acfg in agents.items():
        print(f"  {akey:<20}  {acfg['backend']:<12}  {acfg['model']}")
    if skipped:
        print(
            f"\n  Skipped (backend missing): {', '.join(f'{a} ({b})' for a, b in skipped)}",
        )
    print()

    # Confirm before overwriting an existing team config
    existing_path = _teams_dir() / f"{mode_name}.json"
    if existing_path.exists():
        confirm = (
            input(
                f"Team {mode_name!r} already exists at {existing_path}. Overwrite? [y/N] "
            )
            .strip()
            .lower()
        )
        if confirm not in ("y", "yes"):
            print("Cancelled.", file=sys.stderr)
            return

    _save_team(mode_name, config)
    print(f"\nUse with: kodo --team {mode_name}")


def _teams_dir() -> Path:
    """User teams directory, created on demand."""
    d = Path.home() / ".kodo" / "teams"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ask_agent_fields(
    defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Interactively collect fields for one agent definition."""
    from kodo.team_config import _AGENT_DEFAULTS, _BACKEND_MAP

    d = defaults or {}
    backends = list(_BACKEND_MAP.keys())

    # Place the default backend first so the pointer and highlight are in sync
    # (questionary.select has a visual glitch when default != first item).
    default_backend = d.get("backend", backends[0])
    if default_backend in backends:
        backends = [default_backend] + [b for b in backends if b != default_backend]

    backend = questionary.select(
        "Backend:",
        choices=backends,
    ).ask()
    if backend is None:
        _cancel()

    # Suggest common models for the chosen backend
    _BACKEND_MODELS: dict[str, list[str]] = {
        "claude": ["sonnet", "opus"],
        "cursor": ["composer-2", "composer-2-fast"],
        "codex": ["gpt-5.4", "gpt-5.3-codex", "o3"],
        "gemini-cli": ["gemini-2.5-flash", "gemini-3-flash", "gemini-3-pro"],
    }
    model_suggestions = _BACKEND_MODELS.get(backend, [])
    prev_model = d.get("model", "")

    if model_suggestions:
        # Build choices: previous value first (if editing), then suggestions, then custom
        model_choices = []
        if prev_model and prev_model not in model_suggestions:
            model_choices.append(prev_model)
        model_choices.extend(model_suggestions)
        model_choices.append("(custom)")
        model = questionary.select("Model:", choices=model_choices).ask()
        if model is None:
            _cancel()
        if model == "(custom)":
            model = questionary.text("Model name:", default=prev_model).ask()
            if model is None:
                _cancel()
    else:
        model = questionary.text("Model:", default=prev_model).ask()
        if model is None:
            _cancel()

    description = questionary.text(
        "Description (tool description for orchestrator):",
        default=str(d.get("description", _AGENT_DEFAULTS["description"])),
    ).ask()
    if description is None:
        _cancel()

    system_prompt = questionary.text(
        "System prompt (Enter to skip):",
        default=d.get("system_prompt") or "",
    ).ask()
    if system_prompt is None:
        _cancel()

    max_turns = questionary.text(
        "Max turns:",
        default=str(d.get("max_turns", _AGENT_DEFAULTS["max_turns"])),
    ).ask()
    if max_turns is None:
        _cancel()

    timeout_raw = questionary.text(
        "Timeout (seconds, empty for none):",
        default=str(d.get("timeout_s") or ""),
    ).ask()
    if timeout_raw is None:
        _cancel()

    try:
        max_turns_int = int(max_turns)
    except ValueError:
        _fail(f"Invalid max_turns value: {max_turns!r} (must be an integer)")
    agent: dict[str, Any] = {
        "backend": backend,
        "model": model.strip(),
        "description": description,
        "max_turns": max_turns_int,
    }
    if system_prompt.strip():
        agent["system_prompt"] = system_prompt
    if timeout_raw.strip():
        try:
            agent["timeout_s"] = int(timeout_raw)
        except ValueError:
            _fail(f"Invalid timeout value: {timeout_raw!r} (must be an integer)")

    # Optional fields
    if backend == "claude":
        fallback = questionary.text(
            "Fallback model (Enter to skip):",
            default=d.get("fallback_model") or "",
        ).ask()
        if fallback and fallback.strip():
            agent["fallback_model"] = fallback.strip()

    chrome_q = questionary.confirm(
        "Enable Chrome/browser access?",
        default=d.get("chrome", False),
    ).ask()
    if chrome_q:
        agent["chrome"] = True

    return agent


def _save_team(name: str, config: dict[str, Any]) -> Path:
    """Write team config to ~/.kodo/teams/{name}.json."""
    path = _teams_dir() / f"{name}.json"
    path.write_text(json.dumps(config, indent=2) + "\n")
    print(f"Saved to {path}")
    return path


def _cmd_teams_add(name: str) -> None:
    """Interactive wizard to create a new team."""
    path = _teams_dir() / f"{name}.json"
    if path.exists():
        _fail(
            f"Team {name!r} already exists at {path}\nUse 'kodo teams edit {name}' to modify it."
        )

    print(f"Creating team: {name}\n")

    description = questionary.text("Team description:").ask()
    if description is None:
        _cancel()

    orch_prompt = questionary.text(
        "Orchestrator prompt (Enter to use default):",
        default="",
    ).ask()
    if orch_prompt is None:
        _cancel()

    agents: dict[str, dict[str, Any]] = {}
    while True:
        print(f"\n--- Add agent ({len(agents)} so far) ---")
        agent_key = questionary.text("Agent key name (empty to finish):").ask()
        if agent_key is None:
            _cancel()
        if not agent_key.strip():
            if not agents:
                print("A team needs at least one agent.")
                continue
            break
        agent_key = agent_key.strip()
        if agent_key in agents:
            print(f"Agent {agent_key!r} already exists. Pick a different name.")
            continue
        agents[agent_key] = _ask_agent_fields()

    # Assign verifiers
    agent_keys = list(agents.keys())
    verifiers: dict[str, list[str]] = {
        "testers": [],
        "browser_testers": [],
        "reviewers": [],
    }

    if len(agent_keys) > 1:
        print("\n--- Verifier assignment ---")
        testers = questionary.checkbox(
            "Select testers (non-browser):",
            choices=agent_keys,
        ).ask()
        if testers is not None:
            verifiers["testers"] = testers

        browser_testers = questionary.checkbox(
            "Select browser testers:",
            choices=agent_keys,
        ).ask()
        if browser_testers is not None:
            verifiers["browser_testers"] = browser_testers

        reviewers = questionary.checkbox(
            "Select reviewers (architects):",
            choices=agent_keys,
        ).ask()
        if reviewers is not None:
            verifiers["reviewers"] = reviewers

    config = {
        "name": name,
        "description": description,
        "verifiers": verifiers,
        "agents": agents,
    }
    if orch_prompt.strip():
        config["orchestrator_prompt"] = orch_prompt

    _save_team(name, config)


def _cmd_teams_edit(name: str) -> None:
    """Interactive editor for an existing team."""
    from kodo.team_config import list_available_teams

    # Find the team
    config = None
    source = None
    for tname, tsource, tcfg, _tpath in list_available_teams():
        if tname == name:
            config = tcfg
            source = tsource
            break

    if config is None:
        teams_list = "\n".join(
            f"  {tname} ({tsource})" for tname, tsource, *_ in list_available_teams()
        )
        _fail(f"Team {name!r} not found.\nAvailable teams:\n{teams_list}")

    if source == "built-in":
        print(f"Copying built-in team {name!r} to user directory for editing.")

    agents = config.get("agents", {})
    verifiers = config.get(
        "verifiers",
        {"testers": [], "browser_testers": [], "reviewers": []},
    )

    while True:
        # Show current state
        print(f"\nTeam: {name}")
        print(f"  Description: {config.get('description', '')}")
        orch = config.get("orchestrator_prompt", "")
        if orch:
            snippet = orch[:80].replace("\n", " ")
            print(f"  Orchestrator prompt: {snippet}...")
        else:
            print("  Orchestrator prompt: (default)")
        print(f"  Agents ({len(agents)}):")
        for akey, acfg in agents.items():
            print(f"    {akey}: {acfg.get('backend', '?')} / {acfg.get('model', '?')}")
        print()

        actions = [
            "Add agent",
            "Edit agent",
            "Remove agent",
            "Edit team settings",
            "Edit verifiers",
            "Save & exit",
        ]
        action = questionary.select("Action:", choices=actions).ask()
        if action is None:
            print("Cancelled (changes not saved).", file=sys.stderr)
            return

        if action == "Add agent":
            agent_key = questionary.text("Agent key name:").ask()
            if agent_key and agent_key.strip():
                agent_key = agent_key.strip()
                if agent_key in agents:
                    print(f"Agent {agent_key!r} already exists.")
                else:
                    agents[agent_key] = _ask_agent_fields()

        elif action == "Edit agent":
            if not agents:
                print("No agents to edit.")
                continue
            agent_key = questionary.select(
                "Which agent?",
                choices=list(agents.keys()),
            ).ask()
            if agent_key:
                print(f"\nEditing {agent_key} (Enter to keep current value)")
                agents[agent_key] = _ask_agent_fields(defaults=agents[agent_key])

        elif action == "Remove agent":
            if not agents:
                print("No agents to remove.")
                continue
            agent_key = questionary.select(
                "Remove which agent?",
                choices=list(agents.keys()),
            ).ask()
            if agent_key:
                confirm = questionary.confirm(
                    f"Remove {agent_key}?",
                    default=False,
                ).ask()
                if confirm:
                    del agents[agent_key]
                    # Clean from verifiers
                    for role in verifiers:
                        if agent_key in verifiers[role]:
                            verifiers[role].remove(agent_key)

        elif action == "Edit team settings":
            desc = questionary.text(
                "Description:",
                default=config.get("description", ""),
            ).ask()
            if desc is not None:
                config["description"] = desc

            orch_prompt = questionary.text(
                "Orchestrator prompt (Enter to keep default):",
                default=config.get("orchestrator_prompt", ""),
            ).ask()
            if orch_prompt is not None:
                if orch_prompt.strip():
                    config["orchestrator_prompt"] = orch_prompt
                else:
                    config.pop("orchestrator_prompt", None)

        elif action == "Edit verifiers":
            agent_keys = list(agents.keys())
            if not agent_keys:
                print("No agents to assign as verifiers.")
                continue

            testers = questionary.checkbox(
                "Testers (non-browser):",
                choices=agent_keys,
                default=[k for k in verifiers.get("testers", []) if k in agent_keys],  # type: ignore[arg-type]
            ).ask()
            if testers is not None:
                verifiers["testers"] = testers

            browser_testers = questionary.checkbox(
                "Browser testers:",
                choices=agent_keys,
                default=[
                    k for k in verifiers.get("browser_testers", []) if k in agent_keys
                ],  # type: ignore[arg-type]
            ).ask()
            if browser_testers is not None:
                verifiers["browser_testers"] = browser_testers

            reviewers = questionary.checkbox(
                "Reviewers (architects):",
                choices=agent_keys,
                default=[k for k in verifiers.get("reviewers", []) if k in agent_keys],  # type: ignore[arg-type]
            ).ask()
            if reviewers is not None:
                verifiers["reviewers"] = reviewers

        elif action == "Save & exit":
            config["name"] = name
            config["agents"] = agents
            config["verifiers"] = verifiers
            _save_team(name, config)
            return


def _cmd_teams_delete() -> None:
    """List user teams like ``kodo teams``, then multi-select and confirm removal."""
    from kodo.factory import available_backends
    from kodo.team_config import list_available_teams

    available_backends.cache_clear()
    backends = available_backends()

    all_teams = list_available_teams()
    user_teams = sorted(
        (row for row in all_teams if row[1] == "user"),
        key=lambda row: row[0],
    )

    if not user_teams:
        print("No user-defined teams in ~/.kodo/teams/ to remove.")
        if all_teams:
            print(
                f"{DIM}Built-in teams are omitted here; they are not deleted from disk.{RESET}"
            )
        return

    has_missing = _print_team_blocks(user_teams, backends)
    if has_missing:
        print(
            "Hint: Run 'kodo teams auto' to generate teams adapted to your installed backends.",
        )
        print()

    print(
        f"{DIM}Select team files to remove "
        f"(↑↓ or j/k, space toggles ●/○ only on the current line, Enter confirms).{RESET}\n"
    )

    names = [n for n, *_ in user_teams]
    name_to_path = {n: p for n, _, _, p in user_teams}

    selected = _ask_teams_delete_checkbox("Teams to delete:", names)
    if selected is None:
        _cancel()

    if not selected:
        print("No teams selected.")
        return

    label = ", ".join(selected)
    ok = questionary.confirm(
        f"Permanently delete {len(selected)} file(s) from ~/.kodo/teams/: {label}?",
        default=False,
    ).ask()
    if ok is None:
        _cancel()
    if not ok:
        print("Cancelled.", file=sys.stderr)
        return

    for n in selected:
        path = name_to_path[n]
        path.unlink()
        print(f"Removed user team {n!r} ({path})")
