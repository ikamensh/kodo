"""Team and orchestrator construction helpers.

Centralises the duplicated team-building logic from main.py and cli.py.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from functools import lru_cache
from typing import Callable

from kodo import (
    ARCHITECT_PROMPT,
    TESTER_BROWSER_PROMPT,
    TESTER_PROMPT,
    make_session,
)
from kodo.agent import Agent
from kodo.models import (
    CLAUDE_OPUS,
    CLAUDE_OPUS_FULL,
    CLAUDE_SONNET,
    CLAUDE_SONNET_FULL,
    CODEX_DEFAULT,
    CODEX_WORKER,
    CURSOR_COMPOSER,
    GEMINI_ALIAS_FLASH,
    GEMINI_ALIAS_PRO,
    GEMINI_API_FLASH,
    GEMINI_API_PRO,
    GEMINI_API_PRO_V3,
    GEMINI_CLI_FLASH,
    GEMINI_CLI_FLASH_V3,
    GEMINI_CLI_PRO,
)
from kodo.orchestrators.base import ORCHESTRATOR_SYSTEM_PROMPT, TeamConfig

# ---------------------------------------------------------------------------
# Backend availability detection
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def available_backends() -> dict[str, bool]:
    """Detect which worker backends are installed and on PATH.

    Result is cached. Call clear_backend_cache() to invalidate (e.g. after
    env changes or in tests).
    """
    return {
        "claude": shutil.which("claude") is not None,
        "codex": shutil.which("codex") is not None,
        "cursor": shutil.which("cursor-agent") is not None,
        "gemini-cli": shutil.which("gemini") is not None,
    }


def clear_backend_cache() -> None:
    """Invalidate the available_backends() cache. Call after env changes or in tests."""
    available_backends.cache_clear()


def has_claude() -> bool:
    return available_backends()["claude"]


def has_codex() -> bool:
    return available_backends()["codex"]


def has_cursor() -> bool:
    return available_backends()["cursor"]


def has_gemini_cli() -> bool:
    return available_backends()["gemini-cli"]


def _gemini_only() -> bool:
    """True when gemini-cli is the only available backend."""
    return (
        has_gemini_cli() and not has_claude() and not has_cursor() and not has_codex()
    )


# Central backend preference order for "pick the best available".
# Used for intake, auto-refine, and any other "give me a backend" logic.
# Ordering rationale: claude (strongest reasoning) > cursor > codex > gemini-cli.
_BACKEND_PREFERENCE: list[str] = ["claude", "cursor", "codex", "gemini-cli"]


def preferred_backend() -> str | None:
    """Return the best available backend key, or None if none are installed."""
    for backend in _BACKEND_PREFERENCE:
        if _is_available(backend):
            return backend
    return None


def available_backend_names() -> list[str]:
    """Return display names of all available backends, in preference order."""
    _DISPLAY_NAMES = {
        "claude": "Claude",
        "cursor": "Cursor",
        "codex": "Codex",
        "gemini-cli": "Gemini CLI",
    }
    return [
        _DISPLAY_NAMES[b] for b in _BACKEND_PREFERENCE if _is_available(b)
    ]


# Default "smart" model per backend — used for intake, refine, plan generation.
_BACKEND_SMART_MODEL: dict[str, str] = {
    "claude": CLAUDE_OPUS,
    "cursor": CURSOR_COMPOSER,
    "codex": CODEX_WORKER,
    "gemini-cli": GEMINI_CLI_FLASH_V3,
}


def smart_model_for_backend(backend: str) -> str:
    """Return the best model for a backend (for intake/analysis tasks)."""
    return _BACKEND_SMART_MODEL[backend]


# Maps backend key → orchestrator name for CLI-based orchestrators.
_BACKEND_TO_ORCHESTRATOR: dict[str, str] = {
    "claude": "claude-code",
    "cursor": "cursor",
    "codex": "codex",
    "gemini-cli": "gemini-cli",
}


def preferred_orchestrator() -> str:
    """Return the best CLI orchestrator for the current environment.

    Falls back to 'api' if no CLI backends are installed.
    """
    backend = preferred_backend()
    if backend:
        return _BACKEND_TO_ORCHESTRATOR[backend]
    return "api"


# CLI-based orchestrators that don't need API keys
_CLI_ORCHESTRATORS = {"claude-code", "gemini-cli", "codex", "cursor"}


def check_api_key(orchestrator: str, model: str) -> str | None:
    """Return an error message if the required API key is missing, else None."""
    import os

    if orchestrator in _CLI_ORCHESTRATORS:
        return None

    _GEMINI_ALIASES = {
        GEMINI_ALIAS_PRO,
        GEMINI_ALIAS_FLASH,
        GEMINI_API_PRO_V3,
        GEMINI_API_PRO,
        GEMINI_API_FLASH,
    }
    if model in _GEMINI_ALIASES or model.startswith("gemini"):
        if not os.environ.get("GEMINI_API_KEY") and not os.environ.get(
            "GOOGLE_API_KEY"
        ):
            return "GEMINI_API_KEY (or GOOGLE_API_KEY) not set — required for Gemini models"
    else:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return "ANTHROPIC_API_KEY not set — required for API orchestrator with Claude models"
    return None


# ---------------------------------------------------------------------------
# Backend preflight checks
# ---------------------------------------------------------------------------

# Binary → version/help command to test viability
_PREFLIGHT_CMDS: dict[str, list[str]] = {
    "claude": ["claude", "--version"],
    "cursor": ["cursor-agent", "--version"],
    "codex": ["codex", "--version"],
    "gemini-cli": ["gemini", "--version"],
}

# Session class → backend key for preflight
_SESSION_BACKEND_MAP: dict[str, str] = {
    "ClaudeSession": "claude",
    "CursorSession": "cursor",
    "CodexSession": "codex",
    "GeminiCliSession": "gemini-cli",
}


def _detect_backend(agent: "Agent") -> str | None:
    """Infer the backend key from an agent's session type."""
    cls_name = type(agent.session).__name__
    return _SESSION_BACKEND_MAP.get(cls_name)


def preflight_check_backends(team: "TeamConfig") -> list[str]:
    """Run a lightweight smoke test on each backend in the team.

    Returns a list of warning strings (empty = all OK).
    Checks are best-effort — a passing preflight doesn't guarantee the
    backend will work for real queries, but catches obvious issues like
    expired subscriptions, unlinked auth, or broken binaries.
    """
    warnings: list[str] = []
    checked: set[str] = set()

    for _, agent in team.items():
        backend = _detect_backend(agent)
        if backend is None or backend in checked:
            continue
        checked.add(backend)

        cmd = _PREFLIGHT_CMDS.get(backend)
        if cmd is None:
            continue

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
            )
            if result.returncode != 0:
                combined = f"{result.stderr}\n{result.stdout}".strip()
                # Truncate to keep warning readable
                snippet = (
                    combined[:200] if combined else f"exit code {result.returncode}"
                )
                warnings.append(f"  {backend}: preflight failed — {snippet}")
        except FileNotFoundError:
            warnings.append(f"  {backend}: binary not found on PATH")
        except subprocess.TimeoutExpired:
            warnings.append(f"  {backend}: preflight timed out (15s)")
        except OSError as exc:
            warnings.append(f"  {backend}: {exc}")

    return warnings


# ---------------------------------------------------------------------------
# Team preset
# ---------------------------------------------------------------------------


@dataclass
class TeamPreset:
    """Bundles a team composition, orchestrator prompt, and default params."""

    name: str
    description: str
    system_prompt: str
    build_team: Callable[..., TeamConfig]
    default_max_exchanges: int
    default_max_cycles: int


# ---------------------------------------------------------------------------
# Shared agent descriptions (used by both full and quick team builders)
# ---------------------------------------------------------------------------

_WORKER_COMMON = (
    "Directive: 1-3 sentences describing desired BEHAVIOR, not implementation.\n"
    "If stuck, set new_conversation=true with a fresh directive."
)

_WORKER_FAST_DESC = (
    "Fast coding agent — use for straightforward tasks where speed matters.\n"
    + _WORKER_COMMON
)

_WORKER_SMART_DESC = (
    "Powerful reasoning agent — use for complex tasks, debugging, or when the fast worker struggled.\n"
    + _WORKER_COMMON
)

_FULL_EXTRA = "\nEach task: ONE independently testable feature or change."

_WORKER_FAST_FULL_EXTRA = _FULL_EXTRA

_WORKER_SMART_FULL_EXTRA = (
    _FULL_EXTRA + "\nIf result contains [PROPOSED PLAN], approve or request changes."
)


# ---------------------------------------------------------------------------
# Team builders
# ---------------------------------------------------------------------------

_ARCHITECT_DESC = (
    "Code reviewer. Updates .kodo/architecture.md with decisions.\n"
    "Does not implement features."
)
_TESTER_DESC = (
    "Verifies features end-to-end. Give it a user-experience description to check.\n"
    "Reports what works and what's broken. Does not fix anything."
)
_TESTER_BROWSER_DESC = (
    "Tester with real browser access — use for web UI verification.\n"
    "Reports issues but does not fix anything."
)


@dataclass(frozen=True)
class _BackendOption:
    """One candidate backend+model for a team role."""

    backend: str
    model: str
    session_kwargs: dict | None = None  # extra kwargs for make_session


# Priority tables: first available backend wins for each role.
# Order = preference (best first).
_ROLE_PRIORITIES: dict[str, list[_BackendOption]] = {
    "worker_fast": [
        _BackendOption("cursor", CURSOR_COMPOSER),
        _BackendOption("codex", CODEX_WORKER),
        _BackendOption("gemini-cli", GEMINI_CLI_FLASH),
        _BackendOption("claude", CLAUDE_SONNET),
    ],
    "worker_smart": [
        _BackendOption("claude", CLAUDE_OPUS, {"fallback_model": CLAUDE_SONNET}),
        _BackendOption("gemini-cli", GEMINI_CLI_PRO),
        _BackendOption("codex", CODEX_WORKER),
        _BackendOption("cursor", CURSOR_COMPOSER),
    ],
    "architect": [
        _BackendOption("claude", CLAUDE_OPUS, {"fallback_model": CLAUDE_SONNET}),
        _BackendOption("gemini-cli", GEMINI_CLI_PRO),
    ],
    "tester": [
        _BackendOption("cursor", CURSOR_COMPOSER),
        _BackendOption("gemini-cli", GEMINI_CLI_FLASH),
        _BackendOption("claude", CLAUDE_SONNET),
        _BackendOption("codex", CODEX_WORKER),
    ],
    "tester_browser": [
        _BackendOption("cursor", CURSOR_COMPOSER),
    ],
}

# Role-specific config: (system_prompt, max_turns)
_ROLE_CONFIG: dict[str, tuple[str | None, int]] = {
    "worker_fast": (None, 30),
    "worker_smart": (None, 30),
    "architect": (ARCHITECT_PROMPT, 10),
    "tester": (TESTER_PROMPT, 20),
    "tester_browser": (TESTER_BROWSER_PROMPT, 20),
}

def _is_available(backend: str) -> bool:
    """Check backend availability via has_* functions (respects test patching)."""
    import kodo.factory as _mod

    _checkers = {
        "claude": _mod.has_claude,
        "codex": _mod.has_codex,
        "cursor": _mod.has_cursor,
        "gemini-cli": _mod.has_gemini_cli,
    }
    return _checkers[backend]()


def _pick_backend(role: str) -> _BackendOption | None:
    """Return the highest-priority available backend for *role*, or None."""
    for opt in _ROLE_PRIORITIES.get(role, []):
        if _is_available(opt.backend):
            return opt
    return None


def _build_team_core(
    *,
    worker_fast_desc: str,
    worker_smart_desc: str,
    worker_timeout_s: float = 1800,
    architect_desc: str | None = None,
    architect_timeout_s: float = 600,
    tester_desc: str | None = None,
    tester_timeout_s: float = 1800,
    tester_browser_desc: str | None = None,
) -> TeamConfig:
    """Build team from available backends using priority tables.

    For each role, the first available backend in its priority list is chosen.
    Roles without a description (architect, tester, tester_browser) are skipped.
    """
    if not any(_is_available(b) for b in ("claude", "codex", "cursor", "gemini-cli")):
        raise RuntimeError(
            "No worker backends available. Install at least one of: "
            "claude, cursor, codex, or gemini-cli."
        )

    # Map role name → (description, timeout)
    role_descs: dict[str, tuple[str, float]] = {
        "worker_fast": (worker_fast_desc, worker_timeout_s),
        "worker_smart": (worker_smart_desc, worker_timeout_s),
    }
    if architect_desc:
        role_descs["architect"] = (architect_desc, architect_timeout_s)
    if tester_desc:
        role_descs["tester"] = (tester_desc, tester_timeout_s)
    if tester_browser_desc:
        role_descs["tester_browser"] = (tester_browser_desc, tester_timeout_s)

    team: TeamConfig = {}
    for role, (desc, timeout) in role_descs.items():
        pick = _pick_backend(role)
        if pick is None:
            continue
        sys_prompt, max_turns = _ROLE_CONFIG[role]
        session_kwargs = dict(pick.session_kwargs) if pick.session_kwargs else {}
        if sys_prompt:
            session_kwargs["system_prompt"] = sys_prompt
        if role == "tester_browser":
            session_kwargs["chrome"] = True
        session = make_session(pick.backend, pick.model, **session_kwargs)
        team[role] = Agent(session, desc, max_turns=max_turns, timeout_s=timeout)

    return team


def _build_team_full(
    *,
    worker_timeout_s: float | None = 1800,
    tester_timeout_s: float | None = 1800,
    architect_timeout_s: float | None = 600,
) -> TeamConfig:
    """Create the full team, skipping workers whose backends are unavailable."""
    return _build_team_core(
        worker_fast_desc=_WORKER_FAST_DESC + _WORKER_FAST_FULL_EXTRA,
        worker_smart_desc=_WORKER_SMART_DESC + _WORKER_SMART_FULL_EXTRA,
        worker_timeout_s=worker_timeout_s or 1800,
        architect_desc=_ARCHITECT_DESC,
        architect_timeout_s=architect_timeout_s or 600,
        tester_desc=_TESTER_DESC,
        tester_timeout_s=tester_timeout_s or 1800,
        tester_browser_desc=_TESTER_BROWSER_DESC,
    )


# Backward-compat alias
_build_team_saga = _build_team_full


def _build_team_quick() -> TeamConfig:
    """Create a quick team, skipping workers whose backends are unavailable."""
    return _build_team_core(
        worker_fast_desc=_WORKER_FAST_DESC,
        worker_smart_desc=_WORKER_SMART_DESC,
    )


# Backward-compat alias
_build_team_mission = _build_team_quick


# ---------------------------------------------------------------------------
# Quick orchestrator prompt
# ---------------------------------------------------------------------------


def _quick_system_prompt() -> str:
    """Build the quick system prompt based on available backends."""
    _has_fast = _pick_backend("worker_fast") is not None
    _has_smart = _pick_backend("worker_smart") is not None

    if _has_fast and _has_smart:
        workers_desc = (
            "You have a fast worker and a smart worker. "
            "Use fast for straightforward tasks, smart for complex reasoning."
        )
    elif _has_fast and not _has_smart:
        workers_desc = "You have a fast worker."
    else:
        workers_desc = "You have a smart worker."

    return f"""\
You are an orchestrator solving one focused issue. {workers_desc}

Tell workers WHAT outcome you want, not HOW. Over-specifying makes results worse.

Delegate, verify, send back with specific feedback if wrong. Call done when solved."""


# ---------------------------------------------------------------------------
# Team registry
# ---------------------------------------------------------------------------


def _describe_backends() -> str:
    """Human-readable summary of available backends for team descriptions."""
    parts = []
    if has_cursor():
        parts.append("Cursor")
    if has_codex():
        parts.append("Codex")
    if has_gemini_cli():
        parts.append("Gemini CLI")
    if has_claude():
        parts.append("Claude Code")
    return " + ".join(parts) if parts else "none"


def _full_description() -> str:
    agents = []
    for role in ("worker_fast", "worker_smart", "tester", "tester_browser", "architect"):
        if _pick_backend(role) is not None:
            agents.append(role.replace("_", " "))
    return f"Full team ({_describe_backends()}): {', '.join(agents)}"


def _quick_description() -> str:
    workers = []
    if _pick_backend("worker_fast"):
        workers.append("fast")
    if _pick_backend("worker_smart"):
        workers.append("smart")
    label = " + ".join(workers) if workers else "no"
    return f"{label.title()} worker(s) ({_describe_backends()}) solving one issue, orchestrator as quality gate"


# Backward-compat aliases
_saga_description = _full_description
_mission_description = _quick_description
_mission_system_prompt = _quick_system_prompt


def get_team_presets() -> dict[str, TeamPreset]:
    """Build the team preset registry based on available backends."""
    quick = TeamPreset(
        name="quick",
        description=_quick_description(),
        system_prompt=_quick_system_prompt(),
        build_team=_build_team_quick,
        default_max_exchanges=20,
        default_max_cycles=1,
    )
    return {
        "full": TeamPreset(
            name="full",
            description=_full_description(),
            system_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
            build_team=_build_team_full,
            default_max_exchanges=30,
            default_max_cycles=5,
        ),
        "quick": quick,
        # Backward-compat aliases
        "saga": TeamPreset(
            name="full",
            description=_full_description(),
            system_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
            build_team=_build_team_full,
            default_max_exchanges=30,
            default_max_cycles=5,
        ),
        "mission": quick,
    }


TEAMS = get_team_presets()


def get_team(name: str) -> TeamPreset:
    """Look up a team preset by name.

    For user-defined JSON teams (``~/.kodo/teams/*.json``) that don't match
    a built-in preset, returns a lightweight fallback preset whose
    ``build_team`` always raises — the caller is expected to use
    ``build_team_from_json`` instead.
    """
    if name in TEAMS:
        return TEAMS[name]

    # Check if a user JSON team exists for this name

    # We can't resolve project_dir here, but user-level teams live at
    # ~/.kodo/teams/{name}.json which load_team_config checks anyway.
    user_json = Path.home() / ".kodo" / "teams" / f"{name}.json"
    if user_json.is_file():
        import json

        try:
            cfg = json.loads(user_json.read_text())
        except (json.JSONDecodeError, OSError):
            cfg = {}

        def _no_build(**_kw):
            raise RuntimeError(
                f"Team {name!r} is a JSON team — should be loaded via build_team_from_json"
            )

        return TeamPreset(
            name=name,
            description=cfg.get("description", f"User team: {name}"),
            system_prompt=cfg.get("orchestrator_prompt", ORCHESTRATOR_SYSTEM_PROMPT),
            build_team=_no_build,
            default_max_exchanges=cfg.get("max_exchanges", 30),
            default_max_cycles=cfg.get("max_cycles", 5),
        )

    raise KeyError(name)


# ---------------------------------------------------------------------------
# Orchestrator construction
# ---------------------------------------------------------------------------

# Maps short names ("opus", "sonnet") to full API model IDs.
_MODEL_ALIASES: dict[str, str] = {
    CLAUDE_OPUS: CLAUDE_OPUS_FULL,
    CLAUDE_SONNET: CLAUDE_SONNET_FULL,
    GEMINI_ALIAS_PRO: GEMINI_API_PRO,
    GEMINI_ALIAS_FLASH: GEMINI_API_FLASH,
}


def build_orchestrator(
    name: str,
    model: str | None = None,
    system_prompt: str | None = None,
    fallback_model: str | None = None,
):
    """Construct an orchestrator by name.

    Supported names: 'api', 'claude-code', 'gemini-cli', 'codex', 'cursor'.
    *model* can be a short alias ("opus") or a full model ID.
    *system_prompt* is forwarded to the orchestrator; defaults to the base prompt.
    *fallback_model* is used when the primary model returns 529 (API only).
    """
    if name == "api":
        from kodo.orchestrators.api import ApiOrchestrator

        orch_model = _MODEL_ALIASES.get(model, model) if model else CLAUDE_OPUS_FULL
        fb_model = (
            _MODEL_ALIASES.get(fallback_model, fallback_model)
            if fallback_model
            else None
        )
        return ApiOrchestrator(
            model=orch_model,
            system_prompt=system_prompt,
            fallback_model=fb_model,
        )

    if name == "gemini-cli":
        from kodo.orchestrators.gemini_cli import GeminiCliOrchestrator

        orch_model = model or GEMINI_CLI_FLASH
        return GeminiCliOrchestrator(model=orch_model, system_prompt=system_prompt)

    if name == "codex":
        from kodo.orchestrators.codex_cli import CodexOrchestrator

        orch_model = model or CODEX_DEFAULT
        return CodexOrchestrator(model=orch_model, system_prompt=system_prompt)

    if name == "cursor":
        from kodo.orchestrators.cursor_cli import CursorOrchestrator

        orch_model = model or CURSOR_COMPOSER
        return CursorOrchestrator(model=orch_model, system_prompt=system_prompt)

    from kodo.orchestrators.claude_code import ClaudeCodeOrchestrator

    orch_model = model or CLAUDE_OPUS
    return ClaudeCodeOrchestrator(model=orch_model, system_prompt=system_prompt)
