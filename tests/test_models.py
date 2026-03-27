"""Tests for kodo.models — pricing tables, model mappings, make_fresh_model."""

from __future__ import annotations

import pytest

from kodo.models import (
    MODEL_PRICING,
    OLLAMA_LOCAL,
    PYDANTIC_MODEL_MAP,
    api_orchestrator_model_options,
    ensure_ollama_base_url,
    implied_orchestrator_from_model,
    is_ollama_model,
    list_ollama_models,
    make_fresh_model,
    normalize_ollama_model,
)


# ── Pricing table sanity ─────────────────────────────────────────────────


class TestModelPricing:
    def test_pricing_has_entries(self):
        """MODEL_PRICING is not empty."""
        assert len(MODEL_PRICING) > 0

    def test_pricing_values_are_positive_tuples(self):
        """Each pricing entry is (input, output) with positive numbers."""
        for model, (inp, out) in MODEL_PRICING.items():
            assert isinstance(inp, (int, float)), f"{model}: input price not numeric"
            assert isinstance(out, (int, float)), f"{model}: output price not numeric"
            assert inp > 0, f"{model}: input price must be positive"
            assert out > 0, f"{model}: output price must be positive"

    def test_output_price_geq_input_price(self):
        """Output tokens are always at least as expensive as input tokens."""
        for model, (inp, out) in MODEL_PRICING.items():
            assert out >= inp, f"{model}: output price ({out}) < input price ({inp})"


# ── Pydantic-AI model map ────────────────────────────────────────────────


class TestPydanticModelMap:
    def test_map_has_entries(self):
        assert len(PYDANTIC_MODEL_MAP) > 0

    def test_map_values_have_provider_prefix(self):
        """Every mapped model string has a provider:model format."""
        for key, value in PYDANTIC_MODEL_MAP.items():
            assert ":" in value, f"{key} → {value!r} missing provider prefix"

    def test_anthropic_models_have_anthropic_prefix(self):
        for key, value in PYDANTIC_MODEL_MAP.items():
            if "claude" in key:
                assert value.startswith("anthropic:"), (
                    f"{key} should have anthropic: prefix, got {value!r}"
                )

    def test_gemini_models_have_google_prefix(self):
        for key, value in PYDANTIC_MODEL_MAP.items():
            if "gemini" in key:
                assert value.startswith("google-"), (
                    f"{key} should have google- prefix, got {value!r}"
                )

    def test_all_priced_models_are_mapped(self):
        """Every model in MODEL_PRICING should be in PYDANTIC_MODEL_MAP."""
        for model in MODEL_PRICING:
            assert model in PYDANTIC_MODEL_MAP, (
                f"{model} has pricing but no pydantic-ai mapping"
            )


# ── make_fresh_model ─────────────────────────────────────────────────────


@pytest.mark.real_models
class TestMakeFreshModel:
    def test_anthropic_returns_model_instance(self):
        """anthropic: prefix creates an AnthropicModel instance."""
        result = make_fresh_model("anthropic:claude-opus-4-6")
        from pydantic_ai.models.anthropic import AnthropicModel

        assert isinstance(result, AnthropicModel)

    def test_no_colon_returns_string(self):
        """Model string without provider prefix is returned as-is."""
        result = make_fresh_model("claude-opus-4-6")
        assert result == "claude-opus-4-6"

    def test_unknown_provider_returns_string(self):
        """Unknown provider prefix is returned as-is."""
        result = make_fresh_model("mystery:some-model")
        assert result == "mystery:some-model"

    @pytest.mark.live
    def test_google_gla_returns_google_model(self):
        """google-gla: prefix creates a GoogleModel instance (requires GOOGLE_API_KEY)."""
        result = make_fresh_model("google-gla:gemini-3-flash-preview")
        # Should return a GoogleModel, not a string
        assert not isinstance(result, str)
        assert type(result).__name__ == "GoogleModel"

    @pytest.mark.live
    def test_google_vertex_returns_google_model(self):
        """google-vertex: prefix creates a GoogleModel with vertexai=True (requires GCP credentials)."""
        result = make_fresh_model("google-vertex:gemini-3-flash-preview")
        assert not isinstance(result, str)
        assert type(result).__name__ == "GoogleModel"

    def test_ollama_returns_openai_chat_model(self):
        """ollama: prefix creates an OpenAIChatModel backed by Ollama."""
        from unittest.mock import create_autospec, patch

        from pydantic_ai.providers.ollama import OllamaProvider

        mock_provider = create_autospec(OllamaProvider, instance=True)
        with patch(  # noqa: autospec
            "pydantic_ai.providers.ollama.OllamaProvider",
            return_value=mock_provider,
        ):
            result = make_fresh_model("ollama:llama3.2")

        assert not isinstance(result, str)
        assert type(result).__name__ == "OpenAIChatModel"


class TestOllamaHelpers:
    def test_list_ollama_models_returns_unique_names(self):
        payload = {
            "models": [
                {"name": "qwen2.5-coder:14b"},
                {"model": "llama3.2"},
                {"name": "qwen2.5-coder:14b"},
            ],
        }

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                import json

                return json.dumps(payload).encode()

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("urllib.request.urlopen", lambda *args, **kwargs: _Resp())
            assert list_ollama_models() == ["qwen2.5-coder:14b", "llama3.2"]

    def test_is_ollama_model_recognizes_alias_and_prefixes(self):
        assert is_ollama_model(OLLAMA_LOCAL) is True
        assert is_ollama_model("ollama:llama3.2") is True
        assert is_ollama_model("ollama/llama3.2") is True
        assert is_ollama_model("gemini-flash") is False

    def test_implied_orchestrator_from_ollama_model_is_api(self):
        assert implied_orchestrator_from_model("ollama:qwen2.5-coder:14b") == "api"
        assert implied_orchestrator_from_model(OLLAMA_LOCAL) == "api"
        assert implied_orchestrator_from_model("gemini-flash") is None

    def test_normalize_ollama_model_resolves_slash_form(self):
        assert normalize_ollama_model("ollama/llama3.2") == "ollama:llama3.2"

    def test_normalize_ollama_model_resolves_local_alias(self):
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("kodo.models.list_ollama_models", lambda: ["qwen2.5-coder"])
            assert normalize_ollama_model(OLLAMA_LOCAL) == "ollama:qwen2.5-coder"

    def test_ensure_ollama_base_url_sets_default(self):
        with pytest.MonkeyPatch.context() as mp:
            mp.delenv("OLLAMA_BASE_URL", raising=False)
            assert ensure_ollama_base_url() == "http://localhost:11434/v1"

    def test_api_orchestrator_model_options_include_detected_ollama_models(self):
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "kodo.models.list_ollama_models",
                lambda: ["qwen2.5-coder:14b", "llama3.2"],
            )
            mp.setenv("ANTHROPIC_API_KEY", "test-key")
            mp.setenv("GEMINI_API_KEY", "test-key")
            options = api_orchestrator_model_options()
            # Should include Anthropic and Google aliases plus Ollama models
            assert "opus" in options
            assert "sonnet" in options
            assert "gemini-pro" in options
            assert "gemini-flash" in options
            assert "ollama:qwen2.5-coder:14b" in options
            assert "ollama:llama3.2" in options
