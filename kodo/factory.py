"""Team and orchestrator construction helpers.

Centralises the duplicated team-building logic from main.py and cli.py.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from functools import lru_cache
from typing import Callable

from kodo import make_session
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
    GEMINI_CLI_FLASH,
    GEMINI_CLI_FLASH_V3,
    GEMINI_CLI_PRO,
    KIMI_K2_5,
    KIRO_DEFAULT,
    OPENCODE_DEFAULT,
    all_aliases,
    check_api_key_for_model,
    is_ollama_model,
    normalize_ollama_model,
)
from kodo.orchestrators.base import TeamConfig
from kodo.prompts.roles import (
    AGENT_NOTES_INSTRUCTION,
    ARCHITECT_PROMPT,
    ORCHESTRATOR_SYSTEM_PROMPT,
    TEST_ORCHESTRATOR_SYSTEM_PROMPT,
    TESTER_BROWSER_PROMPT,
    TESTER_PROMPT,
)

# ---------------------------------------------------------------------------
# Backend availability detection
# ---------------------------------------------------------------------------


def _module_available(name: str) -> bool:
    """Return True when an optional Python dependency can be imported."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


@lru_cache(maxsize=1)
def available_backends() -> dict[str, bool]:
    """Detect which worker backends are installed and on PATH.

    Result is cached. Call clear_backend_cache() to invalidate (e.g. after
    env changes or in tests).
    """
    has_claude_sdk = _module_available("claude_agent_sdk")
    has_kimi_sdk = _module_available("kimi_agent_sdk")
    return {
        "claude": shutil.which("claude") is not None and has_claude_sdk,
        "codex": shutil.which("codex") is not None,
        "cursor": shutil.which("cursor-agent") is not None,
        "gemini-cli": shutil.which("gemini") is not None,
        "kimi": shutil.which("kimi") is not None and has_kimi_sdk,
        "kiro": shutil.which("kiro-cli") is not None,
        "opencode": shutil.which("opencode") is not None,
    }


def clear_backend_cache() -> None:
    """Invalidate the available_backends() cache and regenerate TEAMS.

    Call after env changes or in tests.
    """
    available_backends.cache_clear()
    refresh_teams()


def has_claude() -> bool:
    return available_backends()["claude"]


def has_codex() -> bool:
    return available_backends()["codex"]


def has_cursor() -> bool:
    return available_backends()["cursor"]


def has_gemini_cli() -> bool:
    return available_backends()["gemini-cli"]


def has_kimi() -> bool:
    return available_backends()["kimi"]


def has_kiro() -> bool:
    return available_backends()["kiro"]


def has_opencode() -> bool:
    return available_backends()["opencode"]


def _gemini_only() -> bool:
    """True when gemini-cli is the only available backend."""
    return (
        has_gemini_cli()
        and not has_claude()
        and not has_cursor()
        and not has_codex()
        and not has_kimi()
        and not has_kiro()
        and not has_opencode()
    )


# Central backend preference order for "pick the best available".
# Used for intake, auto-refine, and any other "give me a backend" logic.
# Ordering rationale: claude (strongest reasoning) > cursor > kimi > codex > gemini-cli.
_BACKEND_PREFERENCE: list[str] = ["claude", "cursor", "kimi", "codex", "opencode", "kiro", "gemini-cli"]


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
        "kimi": "Kimi",
        "codex": "Codex",
        "opencode": "OpenCode",
        "kiro": "Kiro",
        "gemini-cli": "Gemini CLI",
    }
    return [_DISPLAY_NAMES[b] for b in _BACKEND_PREFERENCE if _is_available(b)]


# Default "smart" model per backend — used for intake, refine, plan generation.
_BACKEND_SMART_MODEL: dict[str, str] = {
    "claude": CLAUDE_OPUS,
    "cursor": CURSOR_COMPOSER,
    "kimi": KIMI_K2_5,
    "codex": CODEX_WORKER,
    "kiro": KIRO_DEFAULT,
    "opencode": OPENCODE_DEFAULT,
    "gemini-cli": GEMINI_CLI_FLASH_V3,
}


def smart_model_for_backend(backend: str) -> str:
    """Return the best model for a backend (for intake/analysis tasks)."""
    return _BACKEND_SMART_MODEL[backend]


# Maps backend key → orchestrator name for CLI-based orchestrators.
_BACKEND_TO_ORCHESTRATOR: dict[str, str] = {
    "claude": "claude-code",
    "cursor": "cursor",
    "kimi": "kimi-code",
    "codex": "codex",
    "kiro": "kiro-cli",
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
_CLI_ORCHESTRATORS = {"claude-code", "gemini-cli", "codex", "cursor", "kimi-code", "kiro-cli"}


def check_api_key(orchestrator: str, model: str) -> str | None:
    """Return an error message if the required API key is missing, else None."""
    if orchestrator in _CLI_ORCHESTRATORS:
        return None

    return check_api_key_for_model(model)


# ---------------------------------------------------------------------------
# Backend preflight checks
# ---------------------------------------------------------------------------

# Binary → version/help command to test viability
_PREFLIGHT_CMDS: dict[str, list[str]] = {
    "claude": ["claude", "--version"],
    "cursor": ["cursor-agent", "--version"],
    "codex": ["codex", "--version"],
    "gemini-cli": ["gemini", "--version"],
    "kimi": ["kimi", "--version"],
    "kiro": ["kiro-cli", "--version"],
    "opencode": ["opencode", "--version"],
}

# Session class → backend key for preflight
_SESSION_BACKEND_MAP: dict[str, str] = {
    "ClaudeSession": "claude",
    "CursorSession": "cursor",
    "CodexSession": "codex",
    "GeminiCliSession": "gemini-cli",
    "KimiSession": "kimi",
    "KiroSession": "kiro",
    "OpenCodeSession": "opencode",
}


def _detect_backend(agent: "Agent") -> str | None:
    """Infer the backend key from an agent's session type."""
    cls_name = type(agent.session).__name__
    return _SESSION_BACKEND_MAP.get(cls_name)


def check_backend_status(name: str) -> tuple[str, str | None]:
    """Run the preflight command and classify output for health issues.

    Returns ``(version_string, warning_or_none)``.  A non-None warning
    means the backend is *installed* but may have auth / quota / billing
    problems (detected from stderr patterns).
    """
    cmd = _PREFLIGHT_CMDS.get(name)
    if cmd is None:
        return ("?", None)

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        return ("timeout", "Version check timed out (15 s)")
    except OSError as exc:
        return ("error", str(exc))

    combined = f"{proc.stderr}\n{proc.stdout}"

    # Extract version string
    if proc.returncode == 0:
        version = proc.stdout.strip().split("\n")[0] or "ok"
    else:
        version = f"error (exit {proc.returncode})"

    # Scan for auth / quota problems (may appear even on rc 0)
    from kodo.sessions.base import (
        _AUTH_PATTERNS,
        _SUBSCRIPTION_PATTERNS,
        classify_session_error,
    )

    if _AUTH_PATTERNS.search(combined):
        return (version, "Authentication issue — check your API key or login status")
    if _SUBSCRIPTION_PATTERNS.search(combined):
        return (version, "Quota/billing issue — check your account status")

    if proc.returncode != 0:
        hint = classify_session_error(
            proc.returncode,
            proc.stderr,
            proc.stdout,
            name,
        )
        if hint:
            return (version, hint)
        snippet = combined.strip()[:200]
        return (
            version,
            snippet or f"Preflight failed with exit code {proc.returncode}",
        )

    return (version, None)


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


DEFAULT_MAX_EXCHANGES: int = 30
DEFAULT_MAX_CYCLES: int = 5


@dataclass
class TeamPreset:
    """Bundles a team composition, orchestrator prompt, and default params."""

    name: str
    description: str
    system_prompt: str
    build_team: Callable[..., TeamConfig]


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
    "Code reviewer. Focuses on key decisions and structural issues.\n"
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
        _BackendOption("kimi", KIMI_K2_5),
        _BackendOption("codex", CODEX_WORKER),
        _BackendOption("opencode", OPENCODE_DEFAULT),
        _BackendOption("kiro", KIRO_DEFAULT),
        _BackendOption("gemini-cli", GEMINI_CLI_FLASH),
        _BackendOption("claude", CLAUDE_SONNET),
    ],
    "worker_smart": [
        _BackendOption("claude", CLAUDE_OPUS, {"fallback_model": CLAUDE_SONNET}),
        _BackendOption("kimi", KIMI_K2_5),
        _BackendOption("opencode", OPENCODE_DEFAULT),
        _BackendOption("kiro", KIRO_DEFAULT),
        _BackendOption("gemini-cli", GEMINI_CLI_PRO),
        _BackendOption("codex", CODEX_WORKER),
        _BackendOption("cursor", CURSOR_COMPOSER),
    ],
    "architect": [
        _BackendOption("claude", CLAUDE_OPUS, {"fallback_model": CLAUDE_SONNET}),
        _BackendOption("kimi", KIMI_K2_5),
        _BackendOption("opencode", OPENCODE_DEFAULT),
        _BackendOption("kiro", KIRO_DEFAULT),
        _BackendOption("gemini-cli", GEMINI_CLI_PRO),
    ],
    "tester": [
        _BackendOption("cursor", CURSOR_COMPOSER),
        _BackendOption("kimi", KIMI_K2_5),
        _BackendOption("opencode", OPENCODE_DEFAULT),
        _BackendOption("kiro", KIRO_DEFAULT),
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
    return {
        "claude": has_claude,
        "codex": has_codex,
        "cursor": has_cursor,
        "gemini-cli": has_gemini_cli,
        "kimi": has_kimi,
        "kiro": has_kiro,
        "opencode": has_opencode,
    }[backend]()


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
    if not any(
        _is_available(b) for b in ("claude", "codex", "cursor", "gemini-cli", "kimi", "opencode")
    ):
        raise RuntimeError(
            "No worker backends available. Install at least one of: "
            "claude, cursor, kimi, codex, opencode, or gemini-cli.",
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
        notes_instruction = AGENT_NOTES_INSTRUCTION.format(role=role)
        sys_prompt = (
            (sys_prompt + notes_instruction)
            if sys_prompt
            else notes_instruction.strip()
        )
        session_kwargs = dict(pick.session_kwargs) if pick.session_kwargs else {}
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


def _build_team_quick() -> TeamConfig:
    """Create a quick team, skipping workers whose backends are unavailable."""
    return _build_team_core(
        worker_fast_desc=_WORKER_FAST_DESC,
        worker_smart_desc=_WORKER_SMART_DESC,
    )


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
    if has_kimi():
        parts.append("Kimi")
    if has_codex():
        parts.append("Codex")
    if has_opencode():
        parts.append("OpenCode")
    if has_kiro():
        parts.append("Kiro")
    if has_gemini_cli():
        parts.append("Gemini CLI")
    if has_claude():
        parts.append("Claude Code")
    return " + ".join(parts) if parts else "none"


def _full_description() -> str:
    agents = []
    for role in (
        "worker_fast",
        "worker_smart",
        "tester",
        "tester_browser",
        "architect",
    ):
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


def get_team_presets() -> dict[str, TeamPreset]:
    """Build the team preset registry based on available backends."""
    quick = TeamPreset(
        name="quick",
        description=_quick_description(),
        system_prompt=_quick_system_prompt(),
        build_team=_build_team_quick,
    )
    return {
        "full": TeamPreset(
            name="full",
            description=_full_description(),
            system_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
            build_team=_build_team_full,
        ),
        "quick": quick,
        "test": TeamPreset(
            name="test",
            description=f"Test generation ({_describe_backends()}): iterative write/run/fix loop",
            system_prompt=TEST_ORCHESTRATOR_SYSTEM_PROMPT,
            build_team=_build_team_full,
        ),
    }


TEAMS: dict[str, "TeamPreset"] = get_team_presets()


def refresh_teams() -> None:
    """Regenerate the TEAMS registry (e.g. after backend availability changes)."""
    global TEAMS
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
                f"Team {name!r} is a JSON team — should be loaded via build_team_from_json",
            )

        return TeamPreset(
            name=name,
            description=cfg.get("description", f"User team: {name}"),
            system_prompt=cfg.get("orchestrator_prompt", ORCHESTRATOR_SYSTEM_PROMPT),
            build_team=_no_build,
        )

    raise KeyError(name)


# ---------------------------------------------------------------------------
# Orchestrator construction
# ---------------------------------------------------------------------------

# Maps short names ("opus", "sonnet") to full API model IDs.
# Generated from the provider registry; kept as a module-level dict for
# backward compatibility (imported by _subcommands.py and tests).
_MODEL_ALIASES: dict[str, str] = {
    alias: pydantic_id for alias, pydantic_id in all_aliases().items()
}
# Ensure legacy aliases that map to bare model IDs still work
_MODEL_ALIASES.update(
    {
        CLAUDE_OPUS: CLAUDE_OPUS_FULL,
        CLAUDE_SONNET: CLAUDE_SONNET_FULL,
        GEMINI_ALIAS_PRO: GEMINI_API_PRO,
        GEMINI_ALIAS_FLASH: GEMINI_API_FLASH,
    }
)


def _best_available_api_model() -> str:
    """Pick the best API orchestrator model based on available API keys.

    Preference: OpenAI gpt-5.4 > Gemini Flash > Claude Opus.
    Claude Code subscription should be used via 'claude-code' orchestrator,
    not the API orchestrator — so Claude is the last resort here.
    """
    import os

    if os.environ.get("OPENAI_API_KEY"):
        return CODEX_DEFAULT  # gpt-5.4
    if os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY"):
        return GEMINI_API_FLASH
    return CLAUDE_OPUS_FULL


def build_orchestrator(
    name: str,
    model: str | None = None,
    system_prompt: str | None = None,
    fallback_model: str | None = None,
):
    """Construct an orchestrator by name.

    Supported names: 'api', 'claude-code', 'kimi-code', 'gemini-cli', 'codex', 'cursor'.
    *model* can be a short alias ("opus") or a full model ID.
    *system_prompt* is forwarded to the orchestrator; defaults to the base prompt.
    *fallback_model* is used when the primary model returns 529 (API only).
    """
    if name == "api":
        from kodo.orchestrators.api import ApiOrchestrator

        orch_model = model or _best_available_api_model()
        if is_ollama_model(orch_model):
            orch_model = normalize_ollama_model(orch_model)
        fb_model = fallback_model
        if fb_model and is_ollama_model(fb_model):
            fb_model = normalize_ollama_model(fb_model)
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

    if name == "kimi-code":
        from kodo.orchestrators.kimi_code import KimiCodeOrchestrator

        orch_model = model or KIMI_K2_5
        return KimiCodeOrchestrator(model=orch_model, system_prompt=system_prompt)

    from kodo.orchestrators.claude_code import ClaudeCodeOrchestrator

    orch_model = model or CLAUDE_OPUS
    return ClaudeCodeOrchestrator(model=orch_model, system_prompt=system_prompt)
