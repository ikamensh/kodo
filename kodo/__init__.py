"""kodo — autonomous goal-driven coding agent."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kodo.sessions.base import Session

__version__ = "0.5.1"

from kodo import log


def make_session(
    backend: str,
    model: str,
    system_prompt: str | None = None,
    chrome: bool = False,
    fallback_model: str | None = None,
    use_api_key: bool = False,
    session_timeout_s: int = 7200,
    effort: str | None = None,
) -> "Session":
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
    if backend == "kiro":
        from kodo.sessions.kiro import KiroSession

        return KiroSession(
            model=model,
            system_prompt=system_prompt,
            timeout_s=session_timeout_s,
        )
    if backend == "opencode":
        from kodo.sessions.opencode import OpenCodeSession

        return OpenCodeSession(
            model=model,
            system_prompt=system_prompt,
            timeout_s=session_timeout_s,
        )
    if backend == "gemini-cli":
        return GeminiCliSession(
            model=model,
            system_prompt=system_prompt,
            timeout_s=session_timeout_s,
        )
    if backend == "codex":
        return CodexSession(
            model=model,
            system_prompt=system_prompt,
            timeout_s=session_timeout_s,
        )
    if backend == "cursor":
        return CursorSession(
            model=model,
            system_prompt=system_prompt,
            timeout_s=session_timeout_s,
        )
    if backend != "claude":
        raise ValueError(f"Unknown worker backend: {backend!r}")
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
    "__version__",
    "cli",
    "log",
    "make_session",
]


def __getattr__(name: str):
    """Lazy ``kodo.cli`` so ``patch('kodo.cli._params.…')`` resolves after ``import kodo`` only."""
    if name == "cli":
        import importlib

        return importlib.import_module("kodo.cli")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
