"""Model-name constants referenced by worker sessions.

Kept deliberately small: only the string identifiers that session modules
import directly.  The full provider registry, pydantic-ai mapping, and
pricing tables live in kodo proper — worker sessions do not need them.
"""

from __future__ import annotations

# Claude
CLAUDE_OPUS = "opus"
CLAUDE_SONNET = "sonnet"
CLAUDE_OPUS_FULL = "claude-opus-4-6"
CLAUDE_SONNET_FULL = "claude-sonnet-4-6"

# Cursor
CURSOR_COMPOSER = "composer-2"

# Codex
CODEX_DEFAULT = "gpt-5.4"
CODEX_WORKER = "gpt-5.4"

# Gemini CLI
GEMINI_CLI_FLASH = "gemini-3-flash"
GEMINI_CLI_FLASH_V3 = "gemini-3-flash"
GEMINI_CLI_PRO = "gemini-3-pro"

# Kimi
KIMI_K2_5 = "kimi-k2.5"

# Kiro
KIRO_DEFAULT = "default"

# OpenCode
OPENCODE_DEFAULT = "default"
