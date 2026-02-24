"""Interactive parameter selection and config persistence."""

import json
import os
import sys
from pathlib import Path

import questionary

from kodo.factory import (
    TEAMS,
    get_team,
    has_claude,
    has_codex,
    has_cursor,
    has_gemini_cli,
    check_api_key,
)
from kodo.user_config import get_user_default

from kodo.cli._ui import _atomic_write


# ---------------------------------------------------------------------------
# Questionary wrappers
# ---------------------------------------------------------------------------


def _labeled_choices(
    options: list[str], default_index: int
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
        print("Cancelled.")
        sys.exit(1)
    return result


def _select_numeric(
    title: str, presets: list[str], default_index: int = 0, type_fn: type = int
) -> str:
    """Arrow-key selection with a 'Custom...' option for numeric values."""
    all_options = presets + ["Custom..."]
    choices = _labeled_choices(all_options, default_index)
    result = questionary.select(title, choices=choices).ask()
    if result is None:
        print("Cancelled.")
        sys.exit(1)
    if result != "Custom...":
        return result
    while True:
        raw = questionary.text("  Enter value:").ask()
        if raw is None:
            print("Cancelled.")
            sys.exit(1)
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
    _claude = has_claude()
    _cursor = has_cursor()
    _gemini = has_gemini_cli()
    if not _claude and not _cursor and not _gemini:
        print("Error: no worker backends found.", file=sys.stderr)
        print("  Install at least one of:", file=sys.stderr)
        print(
            "    Claude Code CLI  — https://docs.anthropic.com/en/docs/claude-code",
            file=sys.stderr,
        )
        print("    Cursor CLI       — https://docs.cursor.com/agent", file=sys.stderr)
        print(
            "    Gemini CLI       — https://github.com/google-gemini/gemini-cli",
            file=sys.stderr,
        )
        sys.exit(1)
    parts = []
    parts.append(f"Claude Code: {'yes' if _claude else 'not found'}")
    parts.append(f"Cursor: {'yes' if _cursor else 'not found'}")
    parts.append(f"Gemini CLI: {'yes' if _gemini else 'not found'}")
    print(f"  Backends: {' | '.join(parts)}\n")

    # Team selection
    team_options = [f"{name} — {m.description}" for name, m in TEAMS.items()]
    team_choice = _select_one("Team:", team_options)
    team_name = team_choice.split(" — ")[0]
    team_preset = get_team(team_name)

    # Build orchestrator choices based on available backends.
    # API is recommended: CLI tools tend to solve problems themselves instead
    # of purely delegating, which makes them worse orchestrators.
    orch_options: list[str] = []
    orch_options.append("api (recommended — delegates cleanly, pay-per-token)")
    if _claude:
        orch_options.append("claude-code (free on Max subscription)")
    if _gemini:
        orch_options.append("gemini-cli (free with Google account)")
    if has_codex():
        orch_options.append("codex (free on Codex subscription)")
    if _cursor:
        orch_options.append("cursor (free on Cursor subscription)")

    orchestrator = _select_one("Orchestrator:", orch_options).split(" (")[0]

    # Select model appropriate for the chosen orchestrator
    if orchestrator == "gemini-cli":
        orch_model = _select_one(
            "Orchestrator model:",
            ["gemini-2.5-flash", "gemini-2.5-pro"],
        )
    elif orchestrator == "codex":
        orch_model = _select_one("Orchestrator model:", ["o3", "o4-mini"])
    elif orchestrator == "cursor":
        orch_model = _select_one(
            "Orchestrator model:",
            ["sonnet-4", "sonnet-4-thinking", "gpt-5"],
        )
    elif orchestrator == "api":
        orch_model = _select_one(
            "Orchestrator model:",
            ["opus", "sonnet", "gemini-pro", "gemini-flash"],
        )
    else:
        # claude-code
        orch_model = _select_one("Orchestrator model:", ["opus", "sonnet"])

    # Validate API key early
    key_err = check_api_key(orchestrator, orch_model)
    if key_err:
        print(f"\n  Error: {key_err}")
        print("  Set the key in your environment or .env file and try again.")
        sys.exit(1)

    print(
        "\n  An exchange = one orchestrator turn: think, delegate to agent, read result."
    )
    exchange_presets = ["20", "30", "50"]
    default_ex = str(team_preset.default_max_exchanges)
    ex_default_idx = (
        exchange_presets.index(default_ex) if default_ex in exchange_presets else 1
    )
    max_exchanges = _select_numeric(
        "Max exchanges per cycle:", exchange_presets, default_index=ex_default_idx
    )

    print("\n  A cycle = one full orchestrator session. If it doesn't finish,")
    print("  a new cycle starts with a summary of prior progress.")
    cycle_presets = ["1", "3", "5", "10"]
    default_cy = str(team_preset.default_max_cycles)
    cy_default_idx = (
        cycle_presets.index(default_cy) if default_cy in cycle_presets else 2
    )
    max_cycles = _select_numeric(
        "Max cycles:", cycle_presets, default_index=cy_default_idx
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
        if isinstance(prev, dict) and "mode" in prev and "team" not in prev:
            prev["team"] = prev.pop("mode")
        if isinstance(prev, dict) and required_keys <= prev.keys():
            team_preset = get_team(prev["team"])
            print("\n  Previous config found:")
            print(f"    Team:         {team_preset.name} — {team_preset.description}")
            print(
                f"    Orchestrator: {prev['orchestrator']} ({prev['orchestrator_model']})"
            )
            print(
                f"    Exchanges:    {prev['max_exchanges']}/cycle, {prev['max_cycles']} cycles"
            )
            reuse = input("\n  Reuse this config? [Y/n] ").strip().lower()
            if not reuse or reuse == "y":
                return prev

    params = select_params()
    try:
        _save_config(project_dir, params)
    except PermissionError:
        _fail(f"Cannot write config to {project_dir / '.kodo'} (permission denied)")
    return params


def _build_params_from_flags(args, project_dir: Path) -> dict:
    """Build config dict from CLI flags, falling back to team defaults."""
    team_name = args.team or "saga"
    team_preset = get_team(team_name)

    orch_model = args.orchestrator_model  # may be None

    _has_gemini_key = bool(
        os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    )

    if args.orchestrator:
        orchestrator = args.orchestrator
    elif not getattr(args, "improve", False) and _has_gemini_key:
        orchestrator = "api"
    elif has_claude():
        orchestrator = "claude-code"
    elif has_gemini_cli():
        orchestrator = "gemini-cli"
    elif has_codex():
        orchestrator = "codex"
    elif has_cursor():
        orchestrator = "cursor"
    else:
        orchestrator = "api"

    # Default model per orchestrator when not explicitly specified
    if not orch_model:
        _ORCH_DEFAULT_MODELS = {
            "claude-code": "opus",
            "gemini-cli": "gemini-2.5-flash",
            "codex": "o3",
            "cursor": "sonnet-4",
            "api": "gemini-flash",
        }
        orch_model = _ORCH_DEFAULT_MODELS.get(orchestrator, "gemini-flash")

    key_err = check_api_key(orchestrator, orch_model)
    if key_err:
        _fail(key_err)

    params = {
        "team": team_name,
        "orchestrator": orchestrator,
        "orchestrator_model": orch_model,
        "max_exchanges": args.exchanges
        if args.exchanges and args.exchanges > 0
        else team_preset.default_max_exchanges,
        "max_cycles": args.cycles
        if args.cycles and args.cycles > 0
        else team_preset.default_max_cycles,
    }

    # Auto-commit: on by default, disabled with --no-auto-commit or user config
    auto_commit = get_user_default("auto_commit", True)
    if getattr(args, "no_auto_commit", False):
        auto_commit = False
    params["auto_commit"] = auto_commit

    try:
        _save_config(project_dir, params)
    except PermissionError:
        _fail(f"Cannot write config to {project_dir / '.kodo'} (permission denied)")
    return params


def _fail(msg: str, code: int = 1) -> None:
    """Print error and exit. Defers to _launch._fail for JSON mode."""
    # Import lazily to avoid circular imports
    from kodo.cli._launch import _fail as _launch_fail

    _launch_fail(msg, code)
