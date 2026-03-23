"""Centralized model name constants and mappings.

Single source of truth for every model string used across kodo.
Import from here instead of scattering raw literals.
"""

from __future__ import annotations

from dataclasses import dataclass, field

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
    """Return user-facing model options for the API orchestrator.

    Legacy function — prefer available_model_choices() for richer data.
    """
    choices = available_model_choices()
    options = [alias for alias, _display, _provider in choices]
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
CURSOR_COMPOSER = "composer-2"

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
# Kiro (Amazon)
# ---------------------------------------------------------------------------
KIRO_DEFAULT = "default"


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
# Provider registry (for API orchestrator model selection)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelInfo:
    """Metadata for a single model in the provider registry."""

    alias: str  # short CLI name, e.g. "opus"
    pydantic_id: str  # full pydantic-ai model string, e.g. "anthropic:claude-opus-4-6"
    full_model_id: str  # bare model ID, e.g. "claude-opus-4-6"
    display_name: str  # human-friendly, e.g. "Claude Opus"
    pricing: tuple[float, float] = (0.0, 0.0)  # (input, output) per 1M tokens


@dataclass(frozen=True)
class Provider:
    """A cloud LLM provider with its models and API key detection."""

    name: str  # display name, e.g. "Anthropic"
    env_vars: tuple[str, ...]  # any of these set → provider is available
    pydantic_prefix: str  # e.g. "anthropic", "google-gla", "openai"
    models: tuple[ModelInfo, ...] = field(default_factory=tuple)


def _build_registry() -> tuple[Provider, ...]:
    """Build the static provider registry. Called once at module load."""
    return (
        Provider(
            name="Anthropic",
            env_vars=("ANTHROPIC_API_KEY",),
            pydantic_prefix="anthropic",
            models=(
                ModelInfo(
                    "opus",
                    "anthropic:claude-opus-4-6",
                    "claude-opus-4-6",
                    "Claude Opus",
                    (5.0, 25.0),
                ),
                ModelInfo(
                    "sonnet",
                    "anthropic:claude-sonnet-4-6",
                    "claude-sonnet-4-6",
                    "Claude Sonnet",
                    (3.0, 15.0),
                ),
                ModelInfo(
                    "haiku",
                    "anthropic:claude-haiku-4-5",
                    "claude-haiku-4-5",
                    "Claude Haiku",
                    (1.0, 5.0),
                ),
            ),
        ),
        Provider(
            name="Google",
            env_vars=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
            pydantic_prefix="google-gla",
            models=(
                ModelInfo(
                    "gemini-pro",
                    "google-gla:gemini-3.1-pro-preview",
                    "gemini-3.1-pro-preview",
                    "Gemini Pro",
                    (2.0, 12.0),
                ),
                ModelInfo(
                    "gemini-flash",
                    "google-gla:gemini-3-flash-preview",
                    "gemini-3-flash-preview",
                    "Gemini Flash",
                    (0.50, 3.0),
                ),
                ModelInfo(
                    "gemini-flash-lite",
                    "google-gla:gemini-3.1-flash-lite-preview",
                    "gemini-3.1-flash-lite-preview",
                    "Gemini Flash Lite",
                    (0.07, 0.30),
                ),
            ),
        ),
        Provider(
            name="OpenAI",
            env_vars=("OPENAI_API_KEY",),
            pydantic_prefix="openai",
            models=(
                ModelInfo(
                    "gpt-5.4", "openai:gpt-5.4", "gpt-5.4", "GPT-5.4", (2.0, 8.0)
                ),
                ModelInfo(
                    "gpt-5.4-mini",
                    "openai:gpt-5.4-mini",
                    "gpt-5.4-mini",
                    "GPT-5.4 Mini",
                    (0.40, 1.60),
                ),
                ModelInfo(
                    "gpt-5.4-nano",
                    "openai:gpt-5.4-nano",
                    "gpt-5.4-nano",
                    "GPT-5.4 Nano",
                    (0.10, 0.40),
                ),
            ),
        ),
        Provider(
            name="DeepSeek",
            env_vars=("DEEPSEEK_API_KEY",),
            pydantic_prefix="deepseek",
            models=(
                ModelInfo(
                    "deepseek",
                    "deepseek:deepseek-chat",
                    "deepseek-chat",
                    "DeepSeek Chat",
                    (0.55, 2.19),
                ),
                ModelInfo(
                    "deepseek-reasoner",
                    "deepseek:deepseek-reasoner",
                    "deepseek-reasoner",
                    "DeepSeek Reasoner",
                    (0.55, 2.19),
                ),
            ),
        ),
        Provider(
            name="Groq",
            env_vars=("GROQ_API_KEY",),
            pydantic_prefix="groq",
            models=(
                ModelInfo(
                    "llama-4-scout",
                    "groq:meta-llama/llama-4-scout-17b-16e-instruct",
                    "meta-llama/llama-4-scout-17b-16e-instruct",
                    "Llama 4 Scout",
                    (0.11, 0.34),
                ),
                ModelInfo(
                    "llama-70b",
                    "groq:llama-3.3-70b-versatile",
                    "llama-3.3-70b-versatile",
                    "Llama 3.3 70B",
                    (0.59, 0.79),
                ),
            ),
        ),
        Provider(
            name="OpenRouter",
            env_vars=("OPENROUTER_API_KEY",),
            pydantic_prefix="openrouter",
            models=(
                ModelInfo(
                    "openrouter-auto",
                    "openrouter:openrouter/auto",
                    "openrouter/auto",
                    "OpenRouter Auto",
                    (0.0, 0.0),
                ),
            ),
        ),
        Provider(
            name="Mistral",
            env_vars=("MISTRAL_API_KEY",),
            pydantic_prefix="mistral",
            models=(
                ModelInfo(
                    "codestral",
                    "mistral:codestral-2508",
                    "codestral-2508",
                    "Codestral",
                    (0.30, 0.90),
                ),
                ModelInfo(
                    "mistral-large",
                    "mistral:mistral-large-latest",
                    "mistral-large-latest",
                    "Mistral Large",
                    (2.0, 6.0),
                ),
            ),
        ),
        Provider(
            name="xAI",
            env_vars=("XAI_API_KEY",),
            pydantic_prefix="xai",
            models=(
                ModelInfo("grok-3", "xai:grok-3", "grok-3", "Grok 3", (3.0, 15.0)),
            ),
        ),
    )


PROVIDER_REGISTRY: tuple[Provider, ...] = _build_registry()


def _provider_has_key(provider: Provider) -> bool:
    """Return True if any of the provider's env vars are set."""
    import os

    return any(os.environ.get(v) for v in provider.env_vars)


def available_providers() -> list[Provider]:
    """Return providers whose API key env vars are set."""
    return [p for p in PROVIDER_REGISTRY if _provider_has_key(p)]


def available_model_choices() -> list[tuple[str, str, str]]:
    """Return (alias, display_name, provider_name) tuples from available providers."""
    choices: list[tuple[str, str, str]] = []
    for provider in available_providers():
        for m in provider.models:
            choices.append((m.alias, m.display_name, provider.name))
    return choices


def _all_model_infos() -> dict[str, ModelInfo]:
    """Build a lookup from alias → ModelInfo across all providers."""
    result: dict[str, ModelInfo] = {}
    for provider in PROVIDER_REGISTRY:
        for m in provider.models:
            result[m.alias] = m
    return result


def _all_model_infos_by_full_id() -> dict[str, ModelInfo]:
    """Build a lookup from full_model_id → ModelInfo across all providers."""
    result: dict[str, ModelInfo] = {}
    for provider in PROVIDER_REGISTRY:
        for m in provider.models:
            result[m.full_model_id] = m
    return result


def resolve_model(model: str | None) -> str:
    """Resolve a model alias or provider:model string to a pydantic-ai model string.

    - Alias (e.g. "opus") → pydantic ID from registry
    - provider:model passthrough (e.g. "anthropic:claude-opus-4-6")
    - Legacy bare IDs (e.g. "claude-opus-4-6") → lookup in PYDANTIC_MODEL_MAP
    - Unknown → passthrough
    """
    if not model:
        return PYDANTIC_MODEL_MAP.get(CLAUDE_OPUS_FULL, f"anthropic:{CLAUDE_OPUS_FULL}")

    # Check registry aliases first
    infos = _all_model_infos()
    if model in infos:
        return infos[model].pydantic_id

    # Already a provider:model string — passthrough
    if ":" in model:
        return model

    # Legacy bare model IDs (e.g. "claude-opus-4-6", "gemini-3-flash-preview")
    if model in PYDANTIC_MODEL_MAP:
        return PYDANTIC_MODEL_MAP[model]

    # Check if it matches a full_model_id in the registry
    by_full = _all_model_infos_by_full_id()
    if model in by_full:
        return by_full[model].pydantic_id

    # Unknown — passthrough (let pydantic-ai figure it out)
    return model


def get_model_info(model: str | None) -> ModelInfo | None:
    """Look up ModelInfo by alias or full model ID. Returns None if not found."""
    if not model:
        return None
    infos = _all_model_infos()
    if model in infos:
        return infos[model]
    by_full = _all_model_infos_by_full_id()
    if model in by_full:
        return by_full[model]
    return None


def get_pricing(model: str | None) -> tuple[float, float]:
    """Return (input_per_1M, output_per_1M) pricing for a model.

    Checks the registry first, then falls back to the legacy MODEL_PRICING dict.
    """
    if not model:
        return (0.0, 0.0)
    info = get_model_info(model)
    if info is not None:
        return info.pricing
    # Legacy: bare model IDs in MODEL_PRICING
    return MODEL_PRICING.get(model, (0.0, 0.0))


def model_display_name(model: str | None) -> str:
    """Return a short display name for a model. Falls back to the raw string."""
    if not model:
        return "unknown"
    info = get_model_info(model)
    if info is not None:
        return info.display_name
    return model


def check_api_key_for_model(model: str | None) -> str | None:
    """Return an error message if the required API key is missing, else None.

    Works for registry models and Ollama models.
    """
    import os

    if not model:
        return None

    if is_ollama_model(model):
        if model == OLLAMA_LOCAL:
            if not list_ollama_models():
                return (
                    "No local Ollama model detected at http://localhost:11434 — "
                    "run `ollama pull <model>` first."
                )
        return None

    # Check registry by alias
    infos = _all_model_infos()
    if model in infos:
        info = infos[model]
        for provider in PROVIDER_REGISTRY:
            if info in provider.models:
                if _provider_has_key(provider):
                    return None
                key_names = " or ".join(provider.env_vars)
                return f"{key_names} not set — required for {provider.name} models"

    # Check registry by full model ID
    by_full = _all_model_infos_by_full_id()
    if model in by_full:
        info = by_full[model]
        for provider in PROVIDER_REGISTRY:
            if info in provider.models:
                if _provider_has_key(provider):
                    return None
                key_names = " or ".join(provider.env_vars)
                return f"{key_names} not set — required for {provider.name} models"

    # provider:model format — check the prefix
    if ":" in model:
        prefix = model.split(":", 1)[0]
        for provider in PROVIDER_REGISTRY:
            if provider.pydantic_prefix == prefix:
                if _provider_has_key(provider):
                    return None
                key_names = " or ".join(provider.env_vars)
                return f"{key_names} not set — required for {provider.name} models"

    # Legacy heuristics for unknown models
    if model.startswith("gemini"):
        if not os.environ.get("GEMINI_API_KEY") and not os.environ.get(
            "GOOGLE_API_KEY"
        ):
            return "GEMINI_API_KEY (or GOOGLE_API_KEY) not set — required for Gemini models"
        return None

    # Default: assume Anthropic
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return "ANTHROPIC_API_KEY not set — required for API orchestrator with Claude models"
    return None


def all_aliases() -> dict[str, str]:
    """Return a dict of alias → pydantic-ai model ID for all registered models."""
    return {m.alias: m.pydantic_id for m in _all_model_infos().values()}


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

    if provider_name == "anthropic":
        from pydantic_ai.providers.anthropic import AnthropicProvider
        from pydantic_ai.models.anthropic import AnthropicModel

        fresh_client = _httpx.AsyncClient(
            timeout=_httpx.Timeout(timeout=600, connect=5),
        )
        provider = AnthropicProvider(http_client=fresh_client)
        return AnthropicModel(model_name, provider=provider)

    if provider_name == "openai":
        from pydantic_ai.providers.openai import OpenAIProvider
        from pydantic_ai.models.openai import OpenAIChatModel

        fresh_client = _httpx.AsyncClient(
            timeout=_httpx.Timeout(timeout=600, connect=5),
        )
        provider = OpenAIProvider(http_client=fresh_client)
        return OpenAIChatModel(model_name, provider=provider)

    # Unknown provider: return the string, let pydantic-ai handle it
    return model_str
