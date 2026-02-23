"""kodo — autonomous goal-driven coding agent."""

__version__ = "0.4.36"

from kodo import log
from kodo.agent import Agent, AgentResult
from kodo.sessions.base import QueryResult, Session, SessionStats
from kodo.orchestrators.base import (
    CycleResult,
    Orchestrator,
    ResumeState,
    RunResult,
    TeamConfig,
)

# ---------------------------------------------------------------------------
# Shared agent prompts — imported by factory.py
# ---------------------------------------------------------------------------

from kodo.orchestrators.base import PASS_SIGNAL, MINOR_SIGNAL

_VERIFIER_SUFFIX = (
    f"Fix minor issues yourself. Only report blocking issues with specific error messages.\n"
    f"Say '{PASS_SIGNAL}' if clean, '{MINOR_SIGNAL}' if you only fixed cosmetics."
)

TESTER_PROMPT = (
    "You are a tester agent. Verify the desired user experience works end-to-end — "
    "run the app, call APIs, check files, verify imports, run scripts.\n"
    + _VERIFIER_SUFFIX
)

TESTER_BROWSER_PROMPT = (
    "You are a tester agent with browser access. Verify the app works by opening it "
    "in a real browser — navigate the UI, click buttons, fill forms, check rendering.\n"
    + _VERIFIER_SUFFIX
)

ARCHITECT_PROMPT = (
    "You are the architect. When reviewing code, update .kodo/architecture.md with "
    "key decisions, component boundaries, and lessons learned. Keep it concise.\n"
    "Workers read this file before coding and may append critique there.\n"
    "Identify bugs and structural issues with specific file/line references.\n"
    + _VERIFIER_SUFFIX
)


def make_session(
    backend: str,
    model: str,
    system_prompt: str | None = None,
    chrome: bool = False,
    fallback_model: str | None = None,
    use_api_key: bool = False,
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

    if backend == "gemini-cli":
        return GeminiCliSession(model=model, system_prompt=system_prompt)
    if backend == "codex":
        return CodexSession(model=model, system_prompt=system_prompt)
    if backend == "cursor":
        return CursorSession(model=model, system_prompt=system_prompt)
    return ClaudeSession(
        model=model,
        system_prompt=system_prompt,
        chrome=chrome,
        fallback_model=fallback_model,
        use_api_key=use_api_key,
    )


__all__ = [
    "log",
    "Agent",
    "AgentResult",
    "QueryResult",
    "Session",
    "SessionStats",
    "CycleResult",
    "ResumeState",
    "RunResult",
    "Orchestrator",
    "TeamConfig",
    "TESTER_PROMPT",
    "TESTER_BROWSER_PROMPT",
    "ARCHITECT_PROMPT",
    "make_session",
]
