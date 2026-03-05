"""Centralized model name constants and mappings.

Single source of truth for every model string used across kodo.
Import from here instead of scattering raw literals.
"""

from __future__ import annotations

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


# ---------------------------------------------------------------------------
# Per-1M-token pricing: (input, output)
# ---------------------------------------------------------------------------
MODEL_PRICING: dict[str, tuple[float, float]] = {
    CLAUDE_OPUS_FULL: (5, 25),
    CLAUDE_SONNET_FULL: (3, 15),
    GEMINI_API_PRO: (2.0, 12.0),
    GEMINI_API_PRO_V3: (2.0, 12.0),
    GEMINI_API_FLASH: (0.50, 3.0),
}

# ---------------------------------------------------------------------------
# Map our model IDs → pydantic-ai model strings (provider:model).
# ---------------------------------------------------------------------------
PYDANTIC_MODEL_MAP: dict[str, str] = {
    CLAUDE_OPUS_FULL: f"anthropic:{CLAUDE_OPUS_FULL}",
    CLAUDE_SONNET_FULL: f"anthropic:{CLAUDE_SONNET_FULL}",
    GEMINI_API_PRO: f"google-gla:{GEMINI_API_PRO}",
    GEMINI_API_PRO_V3: f"google-gla:{GEMINI_API_PRO_V3}",
    GEMINI_API_FLASH: f"google-gla:{GEMINI_API_FLASH}",
}


# ---------------------------------------------------------------------------
# Fresh pydantic-ai model construction (avoids shared httpx client cache)
# ---------------------------------------------------------------------------
def make_fresh_model(model_str: str):
    """Create a pydantic-ai Model with a fresh httpx client.

    pydantic-ai's ``cached_async_http_client()`` shares one
    ``httpx.AsyncClient`` per provider.  That client's transport holds
    asyncio primitives bound to whichever event loop first used it.
    When running agents in threads with their own event loops, we need
    a fresh client per thread to avoid cross-loop ``Event`` conflicts.
    """
    import httpx as _httpx

    try:
        provider_name, model_name = model_str.split(":", maxsplit=1)
    except ValueError:
        return model_str

    if provider_name in ("google-gla", "google-vertex"):
        from pydantic_ai.providers.google import GoogleProvider
        from pydantic_ai.models.google import GoogleModel

        fresh_client = _httpx.AsyncClient(
            timeout=_httpx.Timeout(timeout=600, connect=5),
        )
        provider = GoogleProvider(
            vertexai=(provider_name == "google-vertex"),
            http_client=fresh_client,
        )
        return GoogleModel(model_name, provider=provider)

    # Non-Google models: return the string, let pydantic-ai handle it
    return model_str
