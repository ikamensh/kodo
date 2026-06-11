"""Centralized model name constants and mappings.

Single source of truth for every model string used across kodo.
Import from here instead of scattering raw literals.
"""

from __future__ import annotations

from collections.abc import Callable
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
CLAUDE_OPUS_FULL = "claude-opus-4-7"
CLAUDE_SONNET_FULL = "claude-sonnet-4-6"

# ---------------------------------------------------------------------------
# Cursor
# ---------------------------------------------------------------------------
CURSOR_COMPOSER = "composer-2.5"

# ---------------------------------------------------------------------------
# Codex
# ---------------------------------------------------------------------------
CODEX_DEFAULT = "gpt-5.5"
CODEX_WORKER = "gpt-5.5"

# ---------------------------------------------------------------------------
# Gemini CLI (agent backend)
# ---------------------------------------------------------------------------
GEMINI_CLI_FLASH = "gemini-3.5-flash"
GEMINI_CLI_FLASH_V3 = "gemini-3.5-flash"
GEMINI_CLI_PRO = "gemini-3-pro"

# ---------------------------------------------------------------------------
# Gemini API (orchestrator)
# ---------------------------------------------------------------------------
# Short aliases (user-facing CLI names that map to full model IDs)
GEMINI_ALIAS_PRO = "gemini-pro"
GEMINI_ALIAS_FLASH = "gemini-flash"

GEMINI_API_PRO = "gemini-3.1-pro-preview"
GEMINI_API_PRO_V3 = "gemini-3.1-pro-preview"
GEMINI_API_FLASH = "gemini-3.5-flash"

# ---------------------------------------------------------------------------
# Gemini API (summarizer — lightweight, direct REST)
# ---------------------------------------------------------------------------
GEMINI_SUMMARIZER = "gemini-3.1-flash-lite-preview"

# ---------------------------------------------------------------------------
# Kimi (Moonshot AI)
# ---------------------------------------------------------------------------
KIMI_K2_5 = "kimi-k2.5"

# ---------------------------------------------------------------------------
# Kiro (Amazon)
# ---------------------------------------------------------------------------
KIRO_DEFAULT = "default"

# ---------------------------------------------------------------------------
# OpenCode
# ---------------------------------------------------------------------------
OPENCODE_DEFAULT = "default"


# ---------------------------------------------------------------------------
# Backend / orchestrator emoji (for terminal output)
# ---------------------------------------------------------------------------
BACKEND_EMOJI: dict[str, str] = {
    "claude": "🤖",
    "claude_code": "🤖",
    "cursor": "⚡",
    "codex": "🌀",
    "gemini-cli": "💎",
    "kimi": "🌙",
    "kimi-code": "🌙",
    "kiro": "👻",
    "opencode": "🔑",
    "api": "🔮",  # default for API orchestrator
}

# Provider-specific emoji for API orchestrator model prefixes
_PROVIDER_EMOJI: dict[str, str] = {
    "anthropic": "🤖",
    "google-gla": "💎",
    "google-vertex": "💎",
    "openai": "🌀",
    "deepseek": "🐋",
    "groq": "⚡",
    "openrouter": "🔀",
    "mistral": "🌊",
    "xai": "𝕏",
    "ollama": "🦙",
}


def orchestrator_emoji(orchestrator_name: str, model: str | None = None) -> str:
    """Return the emoji for an orchestrator backend.

    For 'api' orchestrators, infers from the model provider prefix.
    """
    if orchestrator_name == "api" and model:
        # Try resolving alias to pydantic ID first
        resolved = resolve_model(model)
        if ":" in resolved:
            prefix = resolved.split(":", 1)[0]
            if prefix in _PROVIDER_EMOJI:
                return _PROVIDER_EMOJI[prefix]
    return BACKEND_EMOJI.get(orchestrator_name, "")


# ---------------------------------------------------------------------------
# Per-1M-token pricing: (input, output)
# ---------------------------------------------------------------------------
MODEL_PRICING: dict[str, tuple[float, float]] = {
    CLAUDE_OPUS_FULL: (5, 25),
    CLAUDE_SONNET_FULL: (3, 15),
    GEMINI_API_PRO: (2.0, 12.0),
    GEMINI_API_PRO_V3: (2.0, 12.0),
    GEMINI_API_FLASH: (1.50, 9.0),
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
    pydantic_id: str  # full pydantic-ai model string, e.g. "anthropic:claude-opus-4-7"
    full_model_id: str  # bare model ID, e.g. "claude-opus-4-7"
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
                    "anthropic:claude-opus-4-7",
                    "claude-opus-4-7",
                    "Claude Opus 4.7",
                    (5.0, 25.0),
                ),
                ModelInfo(
                    "sonnet",
                    "anthropic:claude-sonnet-4-6",
                    "claude-sonnet-4-6",
                    "Claude Sonnet 4.6",
                    (3.0, 15.0),
                ),
                ModelInfo(
                    "haiku",
                    "anthropic:claude-haiku-4-5",
                    "claude-haiku-4-5",
                    "Claude Haiku 4.5",
                    (1.0, 5.0),
                ),
            ),
        ),
        Provider(
            # GOOGLE_API_KEY first: the google-genai SDK prefers it when both
            # are set, and verify_api_key must probe the key actually used.
            name="Google",
            env_vars=("GOOGLE_API_KEY", "GEMINI_API_KEY"),
            pydantic_prefix="google-gla",
            models=(
                ModelInfo(
                    "gemini-pro",
                    "google-gla:gemini-3.1-pro-preview",
                    "gemini-3.1-pro-preview",
                    "Gemini 3.1 Pro",
                    (2.0, 12.0),
                ),
                ModelInfo(
                    "gemini-flash",
                    "google-gla:gemini-3.5-flash",
                    "gemini-3.5-flash",
                    "Gemini 3.5 Flash",
                    (1.50, 9.0),
                ),
                ModelInfo(
                    "gemini-flash-lite",
                    "google-gla:gemini-3.1-flash-lite-preview",
                    "gemini-3.1-flash-lite-preview",
                    "Gemini 3.1 Flash Lite",
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
                    "gpt-5.5", "openai:gpt-5.5", "gpt-5.5", "GPT-5.5", (5.0, 30.0)
                ),
                ModelInfo(
                    "gpt-5.4", "openai:gpt-5.4", "gpt-5.4", "GPT-5.4", (2.5, 15.0)
                ),
                ModelInfo(
                    "gpt-5.4-mini",
                    "openai:gpt-5.4-mini",
                    "gpt-5.4-mini",
                    "GPT-5.4 Mini",
                    (0.75, 4.50),
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
                ModelInfo(
                    "nemotron",
                    "openrouter:nvidia/nemotron-3-super-120b-a12b",
                    "nvidia/nemotron-3-super-120b-a12b",
                    "Nemotron 3 Super",
                    (0.12, 0.30),
                ),
                ModelInfo(
                    "nemotron-free",
                    "openrouter:nvidia/nemotron-3-super-120b-a12b:free",
                    "nvidia/nemotron-3-super-120b-a12b:free",
                    "Nemotron 3 Super (free)",
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
                    "mistral:codestral-latest",
                    "codestral-latest",
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
                ModelInfo("grok-4.1", "xai:grok-4.1", "grok-4.1", "Grok 4.1", (3.0, 15.0)),
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
    - provider:model passthrough (e.g. "anthropic:claude-opus-4-7")
    - Legacy bare IDs (e.g. "claude-opus-4-7") → lookup in PYDANTIC_MODEL_MAP
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

    # Legacy bare model IDs (e.g. "claude-opus-4-7", "gemini-3.5-flash")
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


def _provider_for_model(model: str) -> Provider | None:
    """Resolve the registry Provider for an alias, full model ID, or provider:model."""
    info = get_model_info(model)
    if info is not None:
        for provider in PROVIDER_REGISTRY:
            if info in provider.models:
                return provider
    if ":" in model:
        prefix = model.split(":", 1)[0]
        for provider in PROVIDER_REGISTRY:
            if provider.pydantic_prefix == prefix:
                return provider
    return None


# Free auth-probe endpoints (list-models or key-info; no tokens consumed).
_BEARER_PROBE_URLS: dict[str, str] = {
    "OpenAI": "https://api.openai.com/v1/models",
    "DeepSeek": "https://api.deepseek.com/models",
    "Groq": "https://api.groq.com/openai/v1/models",
    "OpenRouter": "https://openrouter.ai/api/v1/key",
    "Mistral": "https://api.mistral.ai/v1/models",
    "xAI": "https://api.x.ai/v1/models",
}


def _probe_request(provider: Provider, key: str) -> tuple[str, dict[str, str]] | None:
    """Return (url, headers) for a free request that authenticates *key*."""
    if provider.name == "Anthropic":
        return (
            "https://api.anthropic.com/v1/models?limit=1",
            {"x-api-key": key, "anthropic-version": "2023-06-01"},
        )
    if provider.name == "Google":
        return (
            "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1",
            {"x-goog-api-key": key},
        )
    url = _BEARER_PROBE_URLS.get(provider.name)
    if url is None:
        return None
    return (url, {"Authorization": f"Bearer {key}"})


def _error_detail(resp) -> str:
    """Extract a human-readable error message from a provider response."""
    try:
        err = resp.json().get("error")
    except ValueError:
        err = None
    if isinstance(err, dict) and err.get("message"):
        return str(err["message"])
    if isinstance(err, str):
        return err
    return resp.text[:200]


def verify_api_key(provider: Provider) -> str | None:
    """Live-check the provider's API key with a free models-list request.

    Returns an error message on a definitive rejection (expired/invalid key),
    None when the key works or the result is inconclusive (offline, provider
    without a probe endpoint). Goal is cheap early detection before a run
    starts, not gatekeeping — network trouble never blocks a launch here.
    """
    import os

    import httpx

    key = next((os.environ[v] for v in provider.env_vars if os.environ.get(v)), None)
    if not key:
        return None
    probe = _probe_request(provider, key)
    if probe is None:
        return None
    url, headers = probe
    try:
        resp = httpx.get(url, headers=headers, timeout=4.0)
    except httpx.HTTPError:
        return None
    rejected = resp.status_code in (401, 403) or (
        resp.status_code == 400 and "api key" in resp.text.lower()
    )
    if rejected:
        env_var = next(v for v in provider.env_vars if os.environ.get(v))
        return (
            f"{env_var} was rejected by {provider.name} "
            f"(HTTP {resp.status_code}): {_error_detail(resp)}"
        )
    return None


def probe_keys_async() -> Callable[[], dict[str, str]]:
    """Start background key probes for every configured provider.

    Returns a join() that waits for the probes (bounded) and maps
    provider name → error for rejected keys only. Started at wizard entry,
    the probes overlap with the first questions, so results are ready by
    the time the model list is shown.
    """
    import threading

    results: dict[str, str] = {}

    def _run() -> None:
        from concurrent.futures import ThreadPoolExecutor

        providers = available_providers()
        if not providers:
            return
        with ThreadPoolExecutor(max_workers=len(providers)) as pool:
            for provider, err in zip(providers, pool.map(verify_api_key, providers)):
                if err:
                    results[provider.name] = err

    thread = threading.Thread(target=_run, daemon=True, name="kodo-key-probes")
    thread.start()

    def join(timeout: float = 6.0) -> dict[str, str]:
        thread.join(timeout)
        return dict(results)

    return join


def check_api_key_for_model(model: str | None) -> str | None:
    """Return an error message if the required API key is missing or rejected.

    For registry models with a key set, performs a free live probe against the
    provider (see verify_api_key) so expired/invalid keys surface before a run
    starts. Also handles Ollama models.
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

    provider = _provider_for_model(model)
    if provider is not None:
        if not _provider_has_key(provider):
            key_names = " or ".join(provider.env_vars)
            return f"{key_names} not set — required for {provider.name} models"
        return verify_api_key(provider)

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

    if provider_name == "openrouter":
        import os as _os

        from pydantic_ai.providers.openai import OpenAIProvider
        from pydantic_ai.models.openai import OpenAIChatModel

        fresh_client = _httpx.AsyncClient(
            timeout=_httpx.Timeout(timeout=600, connect=5),
        )
        provider = OpenAIProvider(
            base_url="https://openrouter.ai/api/v1",
            api_key=_os.environ.get("OPENROUTER_API_KEY", ""),
            http_client=fresh_client,
        )
        return OpenAIChatModel(model_name, provider=provider)

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
