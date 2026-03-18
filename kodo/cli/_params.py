"""Interactive parameter selection and config persistence."""

import json
from pathlib import Path

import questionary

from kodo.cli._ui import _atomic_write
from kodo.factory import (
    TEAMS,
    check_api_key,
    get_team,
    preferred_orchestrator,
)
from kodo.models import (
    CLAUDE_OPUS,
    CLAUDE_SONNET,
    CODEX_DEFAULT,
    CURSOR_COMPOSER,
    api_orchestrator_model_options,
    available_model_choices,
    available_providers as _available_model_providers,
    GEMINI_API_FLASH,
    GEMINI_CLI_FLASH,
    GEMINI_CLI_PRO,
    implied_orchestrator_from_model,
    list_ollama_models,
)
from kodo.user_config import get_user_default

# ---------------------------------------------------------------------------
# Questionary wrappers
# ---------------------------------------------------------------------------


def _labeled_choices(
    options: list[str],
    default_index: int,
) -> list[questionary.Choice]:
    """Build Choice objects, appending '(default)' to the default item's label."""
    choices = []
    for i, opt in enumerate(options):
        label = f"{opt} (default)" if i == default_index else opt
        choices.append(questionary.Choice(title=label, value=opt))
    return choices


def _select_one(title: str, options: list[str], default_index: int = 0) -> str:
    """Arrow-key single selection. Returns the chosen string."""
    choices = _labeled_choices(options, default_index)
    result = questionary.select(title, choices=choices).ask()
    if result is None:
        _cancel()
    return result


def _select_numeric(
    title: str,
    presets: list[str],
    default_index: int = 0,
    type_fn: type = int,
) -> str:
    """Arrow-key selection with a 'Custom...' option for numeric values."""
    all_options = presets + ["Custom..."]
    choices = _labeled_choices(all_options, default_index)
    result = questionary.select(title, choices=choices).ask()
    if result is None:
        _cancel()
    if result != "Custom...":
        return result
    while True:
        raw = questionary.text("  Enter value:").ask()
        if raw is None:
            _cancel()
        raw = raw.strip()
        try:
            type_fn(raw)
            return raw
        except (ValueError, TypeError):
            print(f"  Invalid input. Expected {type_fn.__name__}.")


# ---------------------------------------------------------------------------
# Interactive param selection
# ---------------------------------------------------------------------------


def select_params() -> dict:
    """Interactive arrow-key parameter selection. Returns config dict."""
    print("\n--- Configuration ---\n")

    # Show available backends
    from kodo.factory import available_backends as _avail_backends

    _avail_backends.cache_clear()
    _backends = _avail_backends()
    if not any(_backends.values()):
        _fail(
            "No worker backends found.\n"
            "  Install at least one of:\n"
            "    Claude Code CLI  — https://docs.anthropic.com/en/docs/claude-code\n"
            "    Cursor CLI       — https://docs.cursor.com/agent\n"
            "    Codex CLI        — https://github.com/openai/codex\n"
            "    Gemini CLI       — https://github.com/google-gemini/gemini-cli"
        )
    _BACKEND_LABELS = {
        "claude": "Claude Code",
        "codex": "Codex",
        "cursor": "Cursor",
        "gemini-cli": "Gemini CLI",
    }
    parts = [
        f"{label}: {'yes' if _backends.get(key) else 'not found'}"
        for key, label in _BACKEND_LABELS.items()
    ]
    print(f"  Backends: {' | '.join(parts)}\n")

    # Team selection — built-in presets + user JSON teams
    team_options = [f"{name} — {m.description}" for name, m in TEAMS.items()]
    from kodo.team_config import list_available_teams

    _builtin_names = set(TEAMS.keys())
    for tname, _tsource, tcfg, _tpath in list_available_teams():
        if tname not in _builtin_names:
            desc = tcfg.get("description", "user team")
            team_options.append(f"{tname} — {desc}")
    team_choice = _select_one("Team:", team_options)
    team_name = team_choice.split(" — ")[0]
    team_preset = get_team(team_name)

    # Build orchestrator choices based on available backends.
    # API is recommended: CLI tools tend to solve problems themselves instead
    # of purely delegating, which makes them worse orchestrators.
    _ORCH_OPTIONS = [
        ("claude", "claude-code (free on Max subscription)"),
        ("gemini-cli", "gemini-cli (free with Google account)"),
        ("codex", "codex (free on Codex subscription)"),
        ("cursor", "cursor (free on Cursor subscription)"),
    ]
    orch_options: list[str] = []
    orch_options.append("api (recommended — delegates cleanly, pay-per-token)")
    for backend_key, label in _ORCH_OPTIONS:
        if _backends.get(backend_key):
            orch_options.append(label)

    orchestrator = _select_one("Orchestrator:", orch_options).split(" (")[0]

    # Select model appropriate for the chosen orchestrator
    if orchestrator == "gemini-cli":
        orch_model = _select_one(
            "Orchestrator model:",
            [GEMINI_CLI_FLASH, GEMINI_CLI_PRO],
        )
    elif orchestrator == "codex":
        orch_model = _select_one("Orchestrator model:", [CODEX_DEFAULT])
    elif orchestrator == "cursor":
        orch_model = _select_one(
            "Orchestrator model:",
            [CURSOR_COMPOSER, "sonnet-4-thinking", "gpt-5"],
        )
    elif orchestrator == "api":
        # Build model list from available providers, grouped by provider
        model_choices: list[str] = []
        choices_data = available_model_choices()
        current_provider = ""
        for alias, display, provider_name in choices_data:
            if provider_name != current_provider:
                current_provider = provider_name
            model_choices.append(f"{alias} — {display} ({provider_name})")
        # Add Ollama models if running
        for ollama_model in list_ollama_models():
            model_choices.append(f"ollama:{ollama_model}")
        model_choices.append("Custom...")
        if not model_choices:
            model_choices = api_orchestrator_model_options()
            model_choices.append("Custom...")

        selected = _select_one("Orchestrator model:", model_choices)
        if selected == "Custom...":
            raw = questionary.text("  Model name (provider:model or alias):").ask()
            if raw is None:
                _cancel()
            orch_model = raw.strip()
        else:
            # Extract alias from "alias — display (provider)" format
            orch_model = (
                selected.split(" — ")[0].strip() if " — " in selected else selected
            )
    else:
        # claude-code
        orch_model = _select_one("Orchestrator model:", [CLAUDE_OPUS, CLAUDE_SONNET])

    # Validate API key early
    key_err = check_api_key(orchestrator, orch_model)
    if key_err:
        _fail(
            f"{key_err}\n  Set the key in your environment or .env file and try again."
        )

    print(
        "\n  An exchange = one orchestrator turn: think, delegate to agent, read result.",
    )
    exchange_presets = ["20", "30", "50"]
    default_ex = str(team_preset.default_max_exchanges)
    ex_default_idx = (
        exchange_presets.index(default_ex) if default_ex in exchange_presets else 1
    )
    max_exchanges = _select_numeric(
        "Max exchanges per cycle:",
        exchange_presets,
        default_index=ex_default_idx,
    )

    print("\n  A cycle = one full orchestrator session. If it doesn't finish,")
    print("  a new cycle starts with a summary of prior progress.")
    cycle_presets = ["1", "3", "5", "10"]
    default_cy = str(team_preset.default_max_cycles)
    cy_default_idx = (
        cycle_presets.index(default_cy) if default_cy in cycle_presets else 2
    )
    max_cycles = _select_numeric(
        "Max cycles:",
        cycle_presets,
        default_index=cy_default_idx,
    )

    return {
        "team": team_name,
        "orchestrator": orchestrator,
        "orchestrator_model": orch_model,
        "max_exchanges": int(max_exchanges),
        "max_cycles": int(max_cycles),
    }


# ---------------------------------------------------------------------------
# Config persistence
# ---------------------------------------------------------------------------


def _config_path(project_dir: Path) -> Path:
    return project_dir / ".kodo" / "config.json"


def _save_config(project_dir: Path, params: dict) -> None:
    path = _config_path(project_dir)
    _atomic_write(path, json.dumps(params, indent=2))


def _load_or_select_params(project_dir: Path) -> dict:
    """Offer to reuse previous config, or run interactive selection."""
    cfg_path = _config_path(project_dir)
    # Legacy fallback
    if not cfg_path.exists():
        legacy = project_dir / ".kodo" / "last-config.json"
        if legacy.exists():
            cfg_path = legacy
    required_keys = {
        "team",
        "orchestrator",
        "orchestrator_model",
        "max_exchanges",
        "max_cycles",
    }
    if cfg_path.exists():
        try:
            prev = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            prev = None
        # Accept old configs with "mode" key
        was_legacy = False
        if isinstance(prev, dict) and "mode" in prev and "team" not in prev:
            prev["team"] = prev.pop("mode")
            was_legacy = True
        if isinstance(prev, dict) and required_keys <= prev.keys():
            try:
                team_preset = get_team(prev["team"])
            except KeyError:
                print(
                    f"\n  Previous config has unknown team {prev['team']!r}, ignoring."
                )
                prev = None
            else:
                print("\n  Previous config found:")
                print(
                    f"    Team:         {team_preset.name} — {team_preset.description}"
                )
                print(
                    f"    Orchestrator: {prev['orchestrator']} ({prev['orchestrator_model']})",
                )
                print(
                    f"    Exchanges:    {prev['max_exchanges']}/cycle, {prev['max_cycles']} cycles",
                )
                reuse = input("\n  Reuse this config? [Y/n] ").strip().lower()
                if not reuse or reuse == "y":
                    # Re-save migrated config to the canonical path so the
                    # legacy "mode" key doesn't persist on disk.
                    if was_legacy:
                        try:
                            _save_config(project_dir, prev)
                        except (PermissionError, OSError):
                            pass  # best-effort; config works in-memory either way
                    return prev

    params = select_params()
    try:
        _save_config(project_dir, params)
    except PermissionError:
        _fail(f"Cannot write config to {project_dir / '.kodo'} (permission denied)")
    return params


_CLI_BACKENDS = {"claude-code", "gemini-cli", "codex", "cursor"}


def _parse_orchestrator_flag(value: str | None) -> tuple[str | None, str | None]:
    """Parse --orchestrator value into (backend, model).

    Formats:
        "opus"                  → (None, "opus")           — API model
        "openai:gpt-5.4"       → (None, "openai:gpt-5.4") — API model with provider
        "claude-code:opus"      → ("claude-code", "opus")  — CLI backend + model
        "cursor:sonnet-4"       → ("cursor", "sonnet-4")
        "gemini-cli:gemini-2.5" → ("gemini-cli", "gemini-2.5")
        "codex:gpt-5.4"        → ("codex", "gpt-5.4")
    """
    if value is None:
        return None, None

    if ":" in value:
        prefix, rest = value.split(":", 1)
        if prefix in _CLI_BACKENDS:
            return prefix, rest

    # Otherwise it's a model spec (bare alias or provider:model for API)
    return None, value


def _build_params_from_flags(args, project_dir: Path) -> dict:
    """Build config dict from CLI flags, falling back to team defaults."""
    debug = getattr(args, "debug", False)
    team_name = args.team or "full"
    team_preset = get_team(team_name)

    explicit_backend, orch_model = _parse_orchestrator_flag(
        getattr(args, "orchestrator", None)
    )

    if debug:
        # Debug mode: use whatever was specified, or sensible defaults.
        # No real backends needed — everything gets mocked at launch time.
        orchestrator = explicit_backend or "api"
        if not orch_model:
            orch_model = CLAUDE_OPUS
    else:
        _has_any_api_key = bool(_available_model_providers())

        if explicit_backend:
            orchestrator = explicit_backend
        elif orch_model:
            # User gave a model — infer backend
            inferred = implied_orchestrator_from_model(orch_model)
            orchestrator = inferred or "api"
        elif _has_any_api_key:
            orchestrator = "api"
        else:
            orchestrator = preferred_orchestrator()

        # Default model per orchestrator when not explicitly specified
        if not orch_model:
            _ORCH_DEFAULT_MODELS = {
                "claude-code": CLAUDE_OPUS,
                "gemini-cli": GEMINI_CLI_FLASH,
                "codex": CODEX_DEFAULT,
                "cursor": CURSOR_COMPOSER,
            }
            if orchestrator in _ORCH_DEFAULT_MODELS:
                orch_model = _ORCH_DEFAULT_MODELS[orchestrator]
            else:
                # API orchestrator: pick first model from first available provider
                providers = _available_model_providers()
                if providers and providers[0].models:
                    orch_model = providers[0].models[0].alias
                else:
                    orch_model = GEMINI_API_FLASH

    if not debug:
        key_err = check_api_key(orchestrator, orch_model)
        if key_err:
            _fail(key_err)

    cycles_explicit = bool(args.cycles and args.cycles > 0)
    params = {
        "team": team_name,
        "orchestrator": orchestrator,
        "orchestrator_model": orch_model,
        "max_exchanges": args.exchanges
        if args.exchanges and args.exchanges > 0
        else team_preset.default_max_exchanges,
        "max_cycles": args.cycles
        if cycles_explicit
        else team_preset.default_max_cycles,
    }

    # Auto-commit: on by default, disabled with --no-auto-commit or user config
    auto_commit = get_user_default("auto_commit", True)
    if getattr(args, "no_auto_commit", False):
        auto_commit = False
    params["auto_commit"] = auto_commit

    # Effort level: CLI flag > project config > "standard"
    effort = getattr(args, "effort", None)
    if effort:
        params["effort"] = effort

    try:
        _save_config(project_dir, params)
    except PermissionError:
        _fail(f"Cannot write config to {project_dir / '.kodo'} (permission denied)")
    return params


def _fail(msg: str, code: int = 1, *, prefix: str = "Error: ") -> None:
    """Print error and exit. Defers to _launch._fail for JSON mode."""
    # Import lazily to avoid circular imports
    from kodo.cli._launch import _fail as _launch_fail

    _launch_fail(msg, code, prefix=prefix)


def _cancel(msg: str = "Cancelled.") -> None:
    """Print cancellation message to stderr and exit."""
    _fail(msg, prefix="")
