"""kodo-workers — backend-agnostic worker sessions extracted from kodo."""

from __future__ import annotations

__version__ = "0.1.0"

from kodo_workers import log
from kodo_workers.sessions.base import (
    QueryResult,
    Session,
    SessionStats,
    SubprocessSession,
    classify_session_error,
)


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

    Supported backends: ``claude``, ``cursor``, ``codex``, ``gemini-cli``,
    ``kimi``, ``kiro``, ``opencode``.

    *use_api_key*: when False (default), ``ANTHROPIC_API_KEY`` is stripped
    from the environment before spawning the Claude SDK client so the
    session bills through the Claude.ai subscription, not the API.  Set
    True only when API billing is explicitly wanted.
    """
    if backend == "kimi":
        from kodo_workers.sessions.kimi import KimiSession

        return KimiSession(
            model=model,
            system_prompt=system_prompt,
            session_timeout_s=session_timeout_s,
        )
    if backend == "kiro":
        from kodo_workers.sessions.kiro import KiroSession

        return KiroSession(
            model=model,
            system_prompt=system_prompt,
            timeout_s=session_timeout_s,
        )
    if backend == "opencode":
        from kodo_workers.sessions.opencode import OpenCodeSession

        return OpenCodeSession(
            model=model,
            system_prompt=system_prompt,
            timeout_s=session_timeout_s,
        )
    if backend == "gemini-cli":
        from kodo_workers.sessions.gemini_cli import GeminiCliSession

        return GeminiCliSession(
            model=model,
            system_prompt=system_prompt,
            timeout_s=session_timeout_s,
        )
    if backend == "codex":
        from kodo_workers.sessions.codex import CodexSession

        return CodexSession(
            model=model,
            system_prompt=system_prompt,
            timeout_s=session_timeout_s,
        )
    if backend == "cursor":
        from kodo_workers.sessions.cursor import CursorSession

        return CursorSession(
            model=model,
            system_prompt=system_prompt,
            timeout_s=session_timeout_s,
        )
    from kodo_workers.sessions.claude import ClaudeSession

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
    "QueryResult",
    "Session",
    "SessionStats",
    "SubprocessSession",
    "classify_session_error",
    "log",
    "make_session",
]
