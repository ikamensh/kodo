"""Centralized model name constants and mappings.

Single source of truth for every model string used across kodo.
Import from here instead of scattering raw literals.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------
OLLAMA_LOCAL = "ollama-local"
OLLAMA_DEFAULT_BASE_URL = "http://localhost:11434/v1"


def list_ollama_models() -> list[str]:
    """Return available Ollama model names from the local server."""
    import json
    import urllib.error
    import urllib.request

    try:
        req = urllib.request.Request("http://localhost:11434/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read())
    except (
        urllib.error.URLError,
        urllib.error.HTTPError,
        json.JSONDecodeError,
        OSError,
        TimeoutError,
    ):
        return []

    models: list[str] = []
    for item in data.get("models", []):
        name = item.get("name") or item.get("model")
        if isinstance(name, str) and name and name not in models:
            models.append(name)
    return models


def is_ollama_model(model: str | None) -> bool:
    """Return True for Ollama model aliases and provider-qualified strings."""
    return bool(model) and (
        model == OLLAMA_LOCAL
        or model.startswith("ollama:")
        or model.startswith("ollama/")
    )


def implied_orchestrator_from_model(model: str | None) -> str | None:
    """Infer the orchestrator when the model makes it unambiguous."""
    if is_ollama_model(model):
        return "api"
    return None


def normalize_ollama_model(model: str) -> str:
    """Resolve Ollama aliases to a provider-qualified model string."""
    if model == OLLAMA_LOCAL:
        models = list_ollama_models()
        if not models:
            raise ValueError(
                "No local Ollama model detected at http://localhost:11434. "
                "Run `ollama pull <model>` first.",
            )
        return f"ollama:{models[0]}"

    if model.startswith("ollama/"):
        return f"ollama:{model.split('/', maxsplit=1)[1]}"

    return model


def ensure_ollama_base_url() -> str:
    """Set the default Ollama OpenAI-compatible endpoint if absent."""
    import os

    return os.environ.setdefault("OLLAMA_BASE_URL", OLLAMA_DEFAULT_BASE_URL)


def api_orchestrator_model_options() -> list[str]:
    """Return user-facing model options for the API orchestrator."""
    options = [CLAUDE_OPUS, CLAUDE_SONNET, GEMINI_ALIAS_PRO, GEMINI_ALIAS_FLASH]
    options.extend(f"ollama:{model}" for model in list_ollama_models())
    return options


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
CODEX_DEFAULT = "gpt-5.4"
CODEX_WORKER = "gpt-5.4"

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

    if provider_name == "ollama":
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.ollama import OllamaProvider

        fresh_client = _httpx.AsyncClient(
            timeout=_httpx.Timeout(timeout=600, connect=5),
        )
        provider = OllamaProvider(
            base_url=ensure_ollama_base_url(),
            http_client=fresh_client,
        )
        return OpenAIChatModel(model_name, provider=provider)

    # Non-Google models: return the string, let pydantic-ai handle it
    return model_str
