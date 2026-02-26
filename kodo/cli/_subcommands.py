"""Subcommand handlers: runs, backends, teams."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import questionary

from kodo import log
from kodo.models import (
    CLAUDE_OPUS,
    CLAUDE_SONNET,
    CODEX_DEFAULT,
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
            f"  {r.run_id:<{id_w}}  {status:<10}  {r.project_dir:<{dir_w}}  {goal_snippet}"
        )


# ---------------------------------------------------------------------------
# kodo backends
# ---------------------------------------------------------------------------


def _cmd_backends() -> None:
    """List available backends (CLI agents and API orchestrator models)."""
    import os
    import subprocess

    from kodo.factory import (
        _MODEL_ALIASES,
        _PREFLIGHT_CMDS,
        available_backends,
        check_api_key,
    )

    available_backends.cache_clear()
    backends = available_backends()

    _INSTALL_LINKS: dict[str, str] = {
        "claude": "https://docs.anthropic.com/en/docs/claude-code",
        "codex": "https://github.com/openai/codex",
        "cursor": "https://docs.cursor.com/agent",
        "gemini-cli": "https://github.com/google-gemini/gemini-cli",
    }

    # --- CLI backends (agents) ---
    print("CLI backends (agents):")
    for name, present in backends.items():
        if not present:
            link = _INSTALL_LINKS.get(name, "")
            print(f"  {name:<12}  not found  {link}")
            continue

        # Get version
        cmd = _PREFLIGHT_CMDS.get(name)
        version = "?"
        if cmd:
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if proc.returncode == 0:
                    version = proc.stdout.strip().split("\n")[0]
                else:
                    version = f"error (exit {proc.returncode})"
            except (subprocess.TimeoutExpired, OSError):
                version = "error"

        print(f"  {name:<12}  {version}")

    # --- API orchestrator models ---
    print("\nOrchestrator models (API):")
    for alias, full_id in sorted(_MODEL_ALIASES.items()):
        key_err = check_api_key("api", alias)
        status = "ready" if key_err is None else "no key"
        provider = "Gemini" if full_id.startswith("gemini") else "Anthropic"
        print(f"  {alias:<14}  {full_id:<35}  {provider:<10}  {status}")

    # --- API key status ---
    print("\nAPI keys:")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    gemini_key = os.environ.get("GEMINI_API_KEY")
    google_key = os.environ.get("GOOGLE_API_KEY")

    def _masked(val: str) -> str:
        return f"{val[:4]}...{val[-4:]}" if len(val) > 12 else "***"

    if anthropic_key:
        print(f"  ANTHROPIC_API_KEY       set ({_masked(anthropic_key)})")
    else:
        print(
            "  ANTHROPIC_API_KEY       not set  https://console.anthropic.com/settings/keys"
        )

    # GEMINI_API_KEY and GOOGLE_API_KEY are interchangeable for Gemini
    gkey = gemini_key or google_key
    if gkey:
        source = "GEMINI_API_KEY" if gemini_key else "GOOGLE_API_KEY"
        print(f"  Gemini                  set via {source} ({_masked(gkey)})")
    else:
        print("  Gemini                  not set  https://aistudio.google.com/apikey")


# ---------------------------------------------------------------------------
# kodo teams
# ---------------------------------------------------------------------------


def _cmd_teams() -> None:
    """Dispatch `kodo teams [add|edit] [name]`."""
    args = sys.argv[2:]

    if not args:
        _cmd_teams_list()
        return

    subcmd = args[0]
    if subcmd == "add":
        if len(args) < 2:
            print("Usage: kodo teams add <name>")
            sys.exit(1)
        _cmd_teams_add(args[1])
    elif subcmd == "edit":
        if len(args) < 2:
            print("Usage: kodo teams edit <name>")
            sys.exit(1)
        _cmd_teams_edit(args[1])
    elif subcmd == "auto":
        mode_name = args[1] if len(args) >= 2 else None
        if mode_name:
            _cmd_teams_auto(mode_name)
        else:
            _cmd_teams_auto_all()
    else:
        print(f"Unknown teams subcommand: {subcmd}")
        print("Usage: kodo teams [add <name> | edit <name> | auto [mode]]")
        sys.exit(1)


def _cmd_teams_list() -> None:
    """List all available teams (built-in and user-defined)."""
    from kodo.factory import available_backends
    from kodo.team_config import _BACKEND_MAP, list_available_teams

    available_backends.cache_clear()
    backends = available_backends()

    teams = list_available_teams()
    if not teams:
        print("No teams found.")
        return

    has_missing = False
    for name, source, cfg in teams:
        desc = cfg.get("description", "")
        agents = cfg.get("agents", {})
        exchanges = cfg.get("max_exchanges", "?")
        cycles = cfg.get("max_cycles", "?")
        tag = "(built-in)" if source == "built-in" else "(user)"

        # Count available agents
        available_count = sum(
            1
            for acfg in agents.values()
            if backends.get(_BACKEND_MAP.get(acfg.get("backend", ""), ""), False)
        )
        avail_str = f"{available_count}/{len(agents)} available"

        print(f"{name}  {tag}")
        if desc:
            print(f"  {desc}")
        print(
            f"  {len(agents)} agents ({avail_str}), {exchanges} exchanges, {cycles} cycles"
        )

        for akey, acfg in agents.items():
            backend = acfg.get("backend", "?")
            model = acfg.get("model", "?")
            adesc = _truncate_word(acfg.get("description", "").split("\n")[0], 60)
            backend_key = _BACKEND_MAP.get(backend, "")
            ok = backends.get(backend_key, False)
            status = "ok" if ok else "missing"
            if not ok:
                has_missing = True
            print(f"    {akey:<20}  {backend:<12}  {model:<20}  [{status}]  {adesc}")
        print()

    # Hint if any agents have missing backends
    if has_missing:
        print(
            "Hint: Run 'kodo teams auto' to generate teams adapted to your installed backends."
        )
        print()


def _cmd_teams_auto_all() -> None:
    """Generate configs for all built-in team templates."""
    from kodo.team_config import list_available_teams

    built_in_names = [
        name for name, source, _ in list_available_teams() if source == "built-in"
    ]
    if not built_in_names:
        print("No built-in team templates found.")
        sys.exit(1)

    for name in built_in_names:
        _cmd_teams_auto(name)
        print()


def _cmd_teams_auto(mode_name: str) -> None:
    """Generate a viable team config from available backends."""
    from kodo.factory import available_backends
    from kodo.team_config import _BACKEND_MAP, list_available_teams

    available_backends.cache_clear()
    backends = available_backends()

    has = {name: present for name, present in backends.items()}
    any_available = any(has.values())

    if not any_available:
        print("No backends available. Install at least one of:")
        print("  claude, cursor, codex, gemini-cli")
        print("Run 'kodo backends' for install links.")
        sys.exit(1)

    # Find the base template (built-in or user team matching mode_name)
    base_config = None
    for tname, _tsource, tcfg in list_available_teams():
        if tname == mode_name:
            base_config = tcfg
            break

    if base_config is None:
        print(f"No template found for mode {mode_name!r}.")
        print("Available templates:", ", ".join(t[0] for t in list_available_teams()))
        sys.exit(1)

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
        print("Could not create any agents with available backends.")
        sys.exit(1)

    # Build verifiers from agents that are actually present
    src_verifiers = base_config.get("verifiers", {})
    verifiers = {}
    for role, agent_keys in src_verifiers.items():
        verifiers[role] = [k for k in agent_keys if k in agents]

    config = {
        "name": mode_name,
        "description": base_config.get("description", ""),
        "max_exchanges": base_config.get("max_exchanges", 20),
        "max_cycles": base_config.get("max_cycles", 1),
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
            f"\n  Skipped (backend missing): {', '.join(f'{a} ({b})' for a, b in skipped)}"
        )
    print()

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

    backend = questionary.select(
        "Backend:",
        choices=backends,
        default=d.get("backend", backends[0]),
    ).ask()
    if backend is None:
        print("Cancelled.")
        sys.exit(1)

    model = questionary.text("Model:", default=d.get("model", "")).ask()
    if model is None:
        print("Cancelled.")
        sys.exit(1)

    description = questionary.text(
        "Description (tool description for orchestrator):",
        default=d.get("description", _AGENT_DEFAULTS["description"]),
    ).ask()
    if description is None:
        print("Cancelled.")
        sys.exit(1)

    system_prompt = questionary.text(
        "System prompt (Enter to skip):",
        default=d.get("system_prompt") or "",
    ).ask()
    if system_prompt is None:
        print("Cancelled.")
        sys.exit(1)

    max_turns = questionary.text(
        "Max turns:", default=str(d.get("max_turns", _AGENT_DEFAULTS["max_turns"]))
    ).ask()
    if max_turns is None:
        print("Cancelled.")
        sys.exit(1)

    timeout_raw = questionary.text(
        "Timeout (seconds, empty for none):",
        default=str(d.get("timeout_s") or ""),
    ).ask()
    if timeout_raw is None:
        print("Cancelled.")
        sys.exit(1)

    agent: dict[str, Any] = {
        "backend": backend,
        "model": model.strip(),
        "description": description,
        "max_turns": int(max_turns),
    }
    if system_prompt.strip():
        agent["system_prompt"] = system_prompt
    if timeout_raw.strip():
        agent["timeout_s"] = int(timeout_raw)

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
        print(f"Team {name!r} already exists at {path}")
        print(f"Use 'kodo teams edit {name}' to modify it.")
        sys.exit(1)

    print(f"Creating team: {name}\n")

    description = questionary.text("Team description:").ask()
    if description is None:
        print("Cancelled.")
        sys.exit(1)

    max_exchanges = questionary.text("Max exchanges:", default="20").ask()
    if max_exchanges is None:
        print("Cancelled.")
        sys.exit(1)

    max_cycles = questionary.text("Max cycles:", default="1").ask()
    if max_cycles is None:
        print("Cancelled.")
        sys.exit(1)

    orch_prompt = questionary.text(
        "Orchestrator prompt (Enter to use default):", default=""
    ).ask()
    if orch_prompt is None:
        print("Cancelled.")
        sys.exit(1)

    agents: dict[str, dict[str, Any]] = {}
    while True:
        print(f"\n--- Add agent ({len(agents)} so far) ---")
        agent_key = questionary.text("Agent key name (empty to finish):").ask()
        if agent_key is None:
            print("Cancelled.")
            sys.exit(1)
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
            "Select testers (non-browser):", choices=agent_keys
        ).ask()
        if testers is not None:
            verifiers["testers"] = testers

        browser_testers = questionary.checkbox(
            "Select browser testers:", choices=agent_keys
        ).ask()
        if browser_testers is not None:
            verifiers["browser_testers"] = browser_testers

        reviewers = questionary.checkbox(
            "Select reviewers (architects):", choices=agent_keys
        ).ask()
        if reviewers is not None:
            verifiers["reviewers"] = reviewers

    config = {
        "name": name,
        "description": description,
        "max_exchanges": int(max_exchanges),
        "max_cycles": int(max_cycles),
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
    for tname, tsource, tcfg in list_available_teams():
        if tname == name:
            config = tcfg
            source = tsource
            break

    if config is None:
        print(f"Team {name!r} not found.")
        print("Available teams:")
        for tname, tsource, _ in list_available_teams():
            print(f"  {tname} ({tsource})")
        sys.exit(1)

    if source == "built-in":
        print(f"Copying built-in team {name!r} to user directory for editing.")

    agents = config.get("agents", {})
    verifiers = config.get(
        "verifiers", {"testers": [], "browser_testers": [], "reviewers": []}
    )

    while True:
        # Show current state
        print(f"\nTeam: {name}")
        print(f"  Description: {config.get('description', '')}")
        print(f"  Max exchanges: {config.get('max_exchanges', '?')}")
        print(f"  Max cycles: {config.get('max_cycles', '?')}")
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
            print("Cancelled (changes not saved).")
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
                "Which agent?", choices=list(agents.keys())
            ).ask()
            if agent_key:
                print(f"\nEditing {agent_key} (Enter to keep current value)")
                agents[agent_key] = _ask_agent_fields(defaults=agents[agent_key])

        elif action == "Remove agent":
            if not agents:
                print("No agents to remove.")
                continue
            agent_key = questionary.select(
                "Remove which agent?", choices=list(agents.keys())
            ).ask()
            if agent_key:
                confirm = questionary.confirm(
                    f"Remove {agent_key}?", default=False
                ).ask()
                if confirm:
                    del agents[agent_key]
                    # Clean from verifiers
                    for role in verifiers:
                        if agent_key in verifiers[role]:
                            verifiers[role].remove(agent_key)

        elif action == "Edit team settings":
            desc = questionary.text(
                "Description:", default=config.get("description", "")
            ).ask()
            if desc is not None:
                config["description"] = desc

            exc = questionary.text(
                "Max exchanges:",
                default=str(config.get("max_exchanges", 20)),
            ).ask()
            if exc is not None:
                config["max_exchanges"] = int(exc)

            cyc = questionary.text(
                "Max cycles:",
                default=str(config.get("max_cycles", 1)),
            ).ask()
            if cyc is not None:
                config["max_cycles"] = int(cyc)

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
                default=[k for k in verifiers.get("testers", []) if k in agent_keys],
            ).ask()
            if testers is not None:
                verifiers["testers"] = testers

            browser_testers = questionary.checkbox(
                "Browser testers:",
                choices=agent_keys,
                default=[
                    k for k in verifiers.get("browser_testers", []) if k in agent_keys
                ],
            ).ask()
            if browser_testers is not None:
                verifiers["browser_testers"] = browser_testers

            reviewers = questionary.checkbox(
                "Reviewers (architects):",
                choices=agent_keys,
                default=[k for k in verifiers.get("reviewers", []) if k in agent_keys],
            ).ask()
            if reviewers is not None:
                verifiers["reviewers"] = reviewers

        elif action == "Save & exit":
            config["name"] = name
            config["agents"] = agents
            config["verifiers"] = verifiers
            _save_team(name, config)
            return
