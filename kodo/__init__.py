"""kodo — autonomous goal-driven coding agent."""

__version__ = "0.4.180"

# ---------------------------------------------------------------------------
# Compatibility shim: pydantic-ai 1.20 imports ``UserLocation`` from anthropic
# but anthropic 0.84 renamed it to ``BetaUserLocationParam``.  Alias it so
# pydantic-ai's ``from ... import UserLocation`` succeeds without upgrading
# mcp (blocked by kimi-agent-sdk's mcp<1.17 pin).
# Remove once kimi-agent-sdk lifts its mcp cap and we can upgrade pydantic-ai.
# ---------------------------------------------------------------------------
try:
    from anthropic.types.beta import beta_web_search_tool_20250305_param as _ws_mod
    if not hasattr(_ws_mod, "UserLocation") and hasattr(_ws_mod, "BetaUserLocationParam"):
        _ws_mod.UserLocation = _ws_mod.BetaUserLocationParam  # type: ignore[attr-defined]
except ImportError:
    pass

from kodo import log
from kodo.agent import Agent, AgentResult

from kodo.orchestrators.base import (
    CycleResult,
    GoalPlan,
    GoalStage,
    Orchestrator,
    ResumeState,
    RunResult,
    TeamConfig,
)
from kodo.prompts.roles import (
    ARCHITECT_PROMPT,
    TESTER_BROWSER_PROMPT,
    TESTER_PROMPT,
)
from kodo.sessions.base import QueryResult, Session, SessionStats


def make_session(
    backend: str,
    model: str,
    system_prompt: str | None = None,
    chrome: bool = False,
    fallback_model: str | None = None,
    use_api_key: bool = False,
    session_timeout_s: int = 7200,
    effort: str | None = None,
) -> Session:
    """Create a worker session for the given backend.

    *use_api_key*: when False (default), ANTHROPIC_API_KEY is stripped from the
    environment before spawning the Claude SDK client so the session bills
    through the Claude.ai subscription, not the API.  Set True only when you
    explicitly want API billing for this session.
    """
    from kodo.sessions.claude import ClaudeSession
    from kodo.sessions.codex import CodexSession
    from kodo.sessions.cursor import CursorSession
    from kodo.sessions.gemini_cli import GeminiCliSession

    if backend == "kimi":
        from kodo.sessions.kimi import KimiSession

        return KimiSession(
            model=model,
            system_prompt=system_prompt,
            session_timeout_s=session_timeout_s,
        )
    if backend == "gemini-cli":
        return GeminiCliSession(
            model=model, system_prompt=system_prompt, timeout_s=session_timeout_s,
        )
    if backend == "codex":
        return CodexSession(
            model=model, system_prompt=system_prompt, timeout_s=session_timeout_s,
        )
    if backend == "cursor":
        return CursorSession(
            model=model, system_prompt=system_prompt, timeout_s=session_timeout_s,
        )
    return ClaudeSession(
        model=model,
        system_prompt=system_prompt,
        chrome=chrome,
        fallback_model=fallback_model,
        use_api_key=use_api_key,
        session_timeout_s=session_timeout_s,
        effort=effort,
    )


__all__ = [
    "log",
    "Agent",
    "AgentResult",
    "QueryResult",
    "Session",
    "SessionStats",
    "CycleResult",
    "GoalPlan",
    "GoalStage",
    "ResumeState",
    "RunResult",
    "Orchestrator",
    "TeamConfig",
    "TESTER_PROMPT",
    "TESTER_BROWSER_PROMPT",
    "ARCHITECT_PROMPT",
    "make_session",
]
