"""Worker backend metadata shared by discovery, team configuration, and preflight."""

from dataclasses import dataclass

from kodo.models import (
    CLAUDE_OPUS,
    CODEX_WORKER,
    CURSOR_COMPOSER,
    GEMINI_CLI_FLASH,
    KIMI_DEFAULT,
    KIRO_DEFAULT,
    OPENCODE_DEFAULT,
)


@dataclass(frozen=True)
class Backend:
    display_name: str
    command: str
    session_class: str
    smart_model: str
    install_hint: str
    orchestrator: str | None = None


# Workers can exist without a corresponding CLI orchestrator.
BACKENDS: dict[str, Backend] = {
    "claude": Backend(
        "Claude", "claude", "ClaudeSession", CLAUDE_OPUS,
        "install Claude Code: https://code.claude.com/docs/en/setup", "claude-code",
    ),
    "cursor": Backend(
        "Cursor", "cursor-agent", "CursorSession", CURSOR_COMPOSER,
        "install Cursor CLI: https://cursor.com/docs/cli/installation", "cursor",
    ),
    "codex": Backend(
        "Codex", "codex", "CodexSession", CODEX_WORKER,
        "install Codex: https://github.com/openai/codex/blob/main/docs/install.md", "codex",
    ),
    "opencode": Backend(
        "OpenCode", "opencode", "OpenCodeSession", OPENCODE_DEFAULT,
        "install opencode and ensure `opencode` is on PATH",
    ),
    "kiro": Backend(
        "Kiro", "kiro-cli", "KiroSession", KIRO_DEFAULT,
        "install Kiro CLI: https://kiro.dev/docs/cli/installation/",
    ),
    "gemini-cli": Backend(
        "Gemini CLI", "gemini", "GeminiCliSession", GEMINI_CLI_FLASH,
        "install Gemini CLI: https://geminicli.com/docs/get-started/installation/", "gemini-cli",
    ),
    "kimi": Backend(
        "Kimi", "kimi", "KimiSession", KIMI_DEFAULT,
        "install Kimi CLI: https://moonshotai.github.io/kimi-cli/en/guides/getting-started.html#installation",
        "kimi-code",
    ),
}


def backend_for_session(session: object) -> str | None:
    """Identify a session without importing or initializing every backend SDK."""
    class_name = type(session).__name__
    return next(
        (
            name
            for name, backend in BACKENDS.items()
            if backend.session_class == class_name
        ),
        None,
    )
