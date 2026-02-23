"""Team and orchestrator construction helpers.

Centralises the duplicated team-building logic from main.py and cli.py.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable

from kodo import (
    TESTER_PROMPT,
    TESTER_BROWSER_PROMPT,
    ARCHITECT_PROMPT,
    make_session,
)
from kodo.agent import Agent
from kodo.orchestrators.base import ORCHESTRATOR_SYSTEM_PROMPT, TeamConfig


# ---------------------------------------------------------------------------
# Backend availability detection
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def available_backends() -> dict[str, bool]:
    """Detect which worker backends are installed and on PATH."""
    return {
        "claude": shutil.which("claude") is not None,
        "codex": shutil.which("codex") is not None,
        "cursor": shutil.which("cursor-agent") is not None,
        "gemini-cli": shutil.which("gemini") is not None,
    }


def has_claude() -> bool:
    return available_backends()["claude"]


def has_codex() -> bool:
    return available_backends()["codex"]


def has_cursor() -> bool:
    return available_backends()["cursor"]


def has_gemini_cli() -> bool:
    return available_backends()["gemini-cli"]


def check_api_key(orchestrator: str, model: str) -> str | None:
    """Return an error message if the required API key is missing, else None."""
    import os

    if orchestrator == "claude-code":
        return None

    _GEMINI_ALIASES = {
        "gemini-pro",
        "gemini-flash",
        "gemini-3-pro-preview",
        "gemini-3.1-pro-preview",
        "gemini-3-flash-preview",
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
# Mode
# ---------------------------------------------------------------------------


@dataclass
class Mode:
    """Bundles a team composition, orchestrator prompt, and default params."""

    name: str
    description: str
    system_prompt: str
    build_team: Callable[..., TeamConfig]
    default_max_exchanges: int
    default_max_cycles: int


# ---------------------------------------------------------------------------
# Shared agent descriptions (used by both saga and mission team builders)
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

_SAGA_EXTRA = "\nEach task: ONE independently testable feature or change."

_WORKER_FAST_SAGA_EXTRA = _SAGA_EXTRA

_WORKER_SMART_SAGA_EXTRA = (
    _SAGA_EXTRA + "\nIf result contains [PROPOSED PLAN], approve or request changes."
)


# ---------------------------------------------------------------------------
# Team builders
# ---------------------------------------------------------------------------


def _build_team_saga(
    *,
    worker_timeout_s: float | None = 1800,
    tester_timeout_s: float | None = 1800,
    architect_timeout_s: float | None = 600,
) -> TeamConfig:
    """Create the saga team, skipping workers whose backends are unavailable."""
    _has_cursor = has_cursor()
    _has_codex = has_codex()
    _has_gemini_cli = has_gemini_cli()
    _has_claude = has_claude()
    if not _has_cursor and not _has_codex and not _has_gemini_cli and not _has_claude:
        raise RuntimeError(
            "No worker backends available. Install at least one of: "
            "claude, cursor-agent, codex, or gemini."
        )

    team: TeamConfig = {}

    if _has_cursor:
        worker_fast_session = make_session("cursor", "composer-1.5")
        team["worker_fast"] = Agent(
            worker_fast_session,
            _WORKER_FAST_DESC + _WORKER_FAST_SAGA_EXTRA,
            max_turns=30,
            timeout_s=worker_timeout_s,
        )

        tester_session = make_session(
            "cursor", "composer-1.5", system_prompt=TESTER_PROMPT
        )
        team["tester"] = Agent(
            tester_session,
            "Verifies features end-to-end. Give it a user-experience description to check.\n"
            "Reports what works and what's broken. Does not fix anything.",
            max_turns=20,
            timeout_s=tester_timeout_s,
        )

        tester_browser_session = make_session(
            "cursor",
            "composer-1.5",
            system_prompt=TESTER_BROWSER_PROMPT,
            chrome=True,
        )
        team["tester_browser"] = Agent(
            tester_browser_session,
            "Tester with real browser access — use for web UI verification.\n"
            "Reports issues but does not fix anything.",
            max_turns=20,
            timeout_s=tester_timeout_s,
        )

    if _has_codex and "worker_fast" not in team:
        worker_fast_session = make_session("codex", "gpt-5.2-codex")
        team["worker_fast"] = Agent(
            worker_fast_session,
            _WORKER_FAST_DESC + _WORKER_FAST_SAGA_EXTRA,
            max_turns=30,
            timeout_s=worker_timeout_s,
        )

    if _has_gemini_cli and "worker_fast" not in team:
        worker_fast_session = make_session("gemini-cli", "gemini-2.5-flash")
        team["worker_fast"] = Agent(
            worker_fast_session,
            _WORKER_FAST_DESC + _WORKER_FAST_SAGA_EXTRA,
            max_turns=30,
            timeout_s=worker_timeout_s,
        )

    if _has_claude:
        worker_smart_session = make_session("claude", "opus", fallback_model="sonnet")
        team["worker_smart"] = Agent(
            worker_smart_session,
            _WORKER_SMART_DESC + _WORKER_SMART_SAGA_EXTRA,
            max_turns=30,
            timeout_s=worker_timeout_s,
        )

        architect_session = make_session(
            "claude",
            "opus",
            system_prompt=ARCHITECT_PROMPT,
            fallback_model="sonnet",
        )
        team["architect"] = Agent(
            architect_session,
            "Code reviewer. Updates .kodo/architecture.md with decisions.\n"
            "Does not implement features.",
            max_turns=10,
            timeout_s=architect_timeout_s,
        )

    return team


def _build_team_mission() -> TeamConfig:
    """Create a mission team, skipping workers whose backends are unavailable."""
    _has_cursor = has_cursor()
    _has_codex = has_codex()
    _has_gemini_cli = has_gemini_cli()
    _has_claude = has_claude()
    if not _has_cursor and not _has_codex and not _has_gemini_cli and not _has_claude:
        raise RuntimeError(
            "No worker backends available. Install at least one of: "
            "claude, cursor-agent, codex, or gemini."
        )

    team: TeamConfig = {}

    if _has_cursor:
        worker_fast_session = make_session("cursor", "composer-1.5")
        team["worker_fast"] = Agent(
            worker_fast_session,
            _WORKER_FAST_DESC,
            max_turns=30,
            timeout_s=1800,
        )

    if _has_codex and "worker_fast" not in team:
        worker_fast_session = make_session("codex", "gpt-5.2-codex")
        team["worker_fast"] = Agent(
            worker_fast_session,
            _WORKER_FAST_DESC,
            max_turns=30,
            timeout_s=1800,
        )

    if _has_gemini_cli and "worker_fast" not in team:
        worker_fast_session = make_session("gemini-cli", "gemini-2.5-flash")
        team["worker_fast"] = Agent(
            worker_fast_session,
            _WORKER_FAST_DESC,
            max_turns=30,
            timeout_s=1800,
        )

    if _has_claude:
        worker_smart_session = make_session(
            "claude",
            "opus",
            fallback_model="sonnet",
        )
        team["worker_smart"] = Agent(
            worker_smart_session,
            _WORKER_SMART_DESC,
            max_turns=30,
            timeout_s=1800,
        )

    return team


# ---------------------------------------------------------------------------
# Mission orchestrator prompt
# ---------------------------------------------------------------------------


def _mission_system_prompt() -> str:
    """Build the mission system prompt based on available backends."""
    _has_fast = has_cursor() or has_codex() or has_gemini_cli()
    _has_claude = has_claude()

    if _has_fast and _has_claude:
        workers_desc = (
            "You have a fast worker and a smart worker. "
            "Use fast for straightforward tasks, smart for complex reasoning."
        )
    elif _has_fast:
        workers_desc = "You have a fast worker."
    else:
        workers_desc = "You have a smart worker."

    return f"""\
You are an orchestrator solving one focused issue. {workers_desc}

Tell workers WHAT outcome you want, not HOW. Over-specifying makes results worse.

Delegate, verify, send back with specific feedback if wrong. Call done when solved."""


# ---------------------------------------------------------------------------
# Mode registry
# ---------------------------------------------------------------------------


def _describe_backends() -> str:
    """Human-readable summary of available backends for mode descriptions."""
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


def _saga_description() -> str:
    agents = []
    if has_cursor() or has_codex() or has_gemini_cli():
        agents.append("fast worker")
    if has_claude():
        agents.append("smart worker")
    if has_cursor():
        agents.append("tester")
        agents.append("browser tester")
    if has_claude():
        agents.append("architect")
    return f"Full team ({_describe_backends()}): {', '.join(agents)}"


def _mission_description() -> str:
    workers = []
    if has_cursor() or has_codex() or has_gemini_cli():
        workers.append("fast")
    if has_claude():
        workers.append("smart")
    label = " + ".join(workers) if workers else "no"
    return f"{label.title()} worker(s) ({_describe_backends()}) solving one issue, orchestrator as quality gate"


def get_modes() -> dict[str, Mode]:
    """Build the mode registry based on available backends."""
    mission = Mode(
        name="mission",
        description=_mission_description(),
        system_prompt=_mission_system_prompt(),
        build_team=_build_team_mission,
        default_max_exchanges=20,
        default_max_cycles=1,
    )
    return {
        "saga": Mode(
            name="saga",
            description=_saga_description(),
            system_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
            build_team=_build_team_saga,
            default_max_exchanges=30,
            default_max_cycles=5,
        ),
        "mission": mission,
        "quick": mission,  # alias for mission
    }


# Keep MODES as a lazy accessor for backward compat
MODES = get_modes()


def get_mode(name: str) -> Mode:
    """Look up a mode by name. Raises KeyError if not found."""
    return MODES[name]


# ---------------------------------------------------------------------------
# Orchestrator construction
# ---------------------------------------------------------------------------

# Maps short names ("opus", "sonnet") to full API model IDs.
_MODEL_ALIASES: dict[str, str] = {
    "opus": "claude-opus-4-6",
    "sonnet": "claude-sonnet-4-5-20250929",
    "gemini-pro": "gemini-3.1-pro-preview",
    "gemini-flash": "gemini-3-flash-preview",
}


def default_fallback(model: str) -> str | None:
    """Return a sensible fallback model for *model*, or None."""
    full = _MODEL_ALIASES.get(model, model)
    if full.startswith("claude"):
        return "gemini-3.1-pro-preview"
    if full.startswith("gemini"):
        return "claude-sonnet-4-5-20250929"
    return None


def model_alias_for_display(full_model_id: str) -> str:
    """Return short alias for display, or full ID if no alias."""
    rev = {v: k for k, v in _MODEL_ALIASES.items()}
    return rev.get(full_model_id, full_model_id)


def build_orchestrator(
    name: str,
    model: str | None = None,
    system_prompt: str | None = None,
    fallback_model: str | None = None,
):
    """Construct an orchestrator by name ('api' or 'claude-code').

    *model* can be a short alias ("opus") or a full model ID.
    *system_prompt* is forwarded to the orchestrator; defaults to the base prompt.
    *fallback_model* is used when the primary model returns 529.
    """
    if name == "api":
        from kodo.orchestrators.api import ApiOrchestrator

        orch_model = _MODEL_ALIASES.get(model, model) if model else "claude-opus-4-6"
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

    from kodo.orchestrators.claude_code import ClaudeCodeOrchestrator

    orch_model = model or "opus"
    return ClaudeCodeOrchestrator(model=orch_model, system_prompt=system_prompt)
