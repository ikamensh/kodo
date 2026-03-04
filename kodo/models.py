"""Centralized model name constants.

Single source of truth for every model string used across kodo.
Import from here instead of scattering raw literals.
"""

# ---------------------------------------------------------------------------
# Claude
# ---------------------------------------------------------------------------
CLAUDE_OPUS = "opus"
CLAUDE_SONNET = "sonnet"
CLAUDE_OPUS_FULL = "claude-opus-4-6"
CLAUDE_SONNET_FULL = "claude-sonnet-4-5-20250929"

# ---------------------------------------------------------------------------
# Cursor
# ---------------------------------------------------------------------------
CURSOR_COMPOSER = "composer-1.5"

# ---------------------------------------------------------------------------
# Codex
# ---------------------------------------------------------------------------
CODEX_DEFAULT = "gpt-5.3-codex"
CODEX_WORKER = "gpt-5.3-codex"
CODEX_O3 = "o3"

# ---------------------------------------------------------------------------
# Gemini CLI (agent backend)
# ---------------------------------------------------------------------------
GEMINI_CLI_FLASH = "gemini-2.5-flash"
GEMINI_CLI_FLASH_V3 = "gemini-3-flash"
GEMINI_CLI_PRO = "gemini-3-pro"

# ---------------------------------------------------------------------------
# Gemini API (orchestrator)
# ---------------------------------------------------------------------------
# Short aliases (user-facing CLI names that map to full model IDs)
GEMINI_ALIAS_PRO = "gemini-pro"
GEMINI_ALIAS_FLASH = "gemini-flash"

GEMINI_API_PRO = "gemini-3.1-pro-preview"
GEMINI_API_PRO_V3 = "gemini-3-pro-preview"
GEMINI_API_FLASH = "gemini-3-flash-preview"

# ---------------------------------------------------------------------------
# Gemini API (summarizer — lightweight, direct REST)
# ---------------------------------------------------------------------------
GEMINI_SUMMARIZER = "gemini-2.5-flash-lite"

# ---------------------------------------------------------------------------
# Kimi (Moonshot AI)
# ---------------------------------------------------------------------------
KIMI_K2_5 = "kimi-k2.5"
KIMI_K2 = "kimi-k2"
