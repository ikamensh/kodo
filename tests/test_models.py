"""Tests for kodo.models — pricing tables, model mappings, make_fresh_model."""

from __future__ import annotations

import pytest

from kodo.models import (
    MODEL_PRICING,
    OLLAMA_LOCAL,
    PROVIDER_REGISTRY,
    PYDANTIC_MODEL_MAP,
    _probe_request,
    api_orchestrator_model_options,
    available_model_choices,
    check_api_key_for_model,
    ensure_ollama_base_url,
    implied_orchestrator_from_model,
    is_ollama_model,
    list_ollama_models,
    make_fresh_model,
    normalize_ollama_model,
    probe_keys_async,
    resolve_model,
    verify_api_key,
)


def _provider(name: str):
    return next(p for p in PROVIDER_REGISTRY if p.name == name)


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
        result = make_fresh_model("anthropic:claude-opus-4-7")
        from pydantic_ai.models.anthropic import AnthropicModel

        assert isinstance(result, AnthropicModel)

    def test_no_colon_returns_string(self):
        """Model string without provider prefix is returned as-is."""
        result = make_fresh_model("claude-opus-4-7")
        assert result == "claude-opus-4-7"

    def test_unknown_provider_returns_string(self):
        """Unknown provider prefix is returned as-is."""
        result = make_fresh_model("mystery:some-model")
        assert result == "mystery:some-model"

    @pytest.mark.live
    def test_google_gla_returns_google_model(self):
        """google-gla: prefix creates a GoogleModel instance (requires GOOGLE_API_KEY)."""
        result = make_fresh_model("google-gla:gemini-3.5-flash")
        # Should return a GoogleModel, not a string
        assert not isinstance(result, str)
        assert type(result).__name__ == "GoogleModel"

    @pytest.mark.live
    def test_google_vertex_returns_google_model(self):
        """google-vertex: prefix creates a GoogleModel with vertexai=True (requires GCP credentials)."""
        result = make_fresh_model("google-vertex:gemini-3.5-flash")
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

    def test_verify_skipped_for_ollama_and_unknown(self):
        """Ollama and legacy-heuristic models never trigger a network probe."""
        from unittest.mock import patch

        with (
            pytest.MonkeyPatch.context() as mp,
            patch("httpx.get", autospec=True) as mock_get,
        ):
            mp.setenv("ANTHROPIC_API_KEY", "k")
            assert check_api_key_for_model("ollama:llama3.2") is None
            assert check_api_key_for_model("some-unknown-model") is None
        mock_get.assert_not_called()

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


# ── Live API key verification ────────────────────────────────────────────


class TestVerifyApiKey:
    """verify_api_key makes one free request to catch dead keys pre-launch.

    Rejection must be definitive (auth-style HTTP error); anything
    inconclusive (network down, 5xx) must NOT block a launch.
    """

    def test_every_provider_has_probe_endpoint(self):
        """New providers must get a probe URL, or expired keys for them
        would silently skip early validation again (the original bug)."""
        for provider in PROVIDER_REGISTRY:
            assert _probe_request(provider, "key") is not None, provider.name

    def test_rejected_key_returns_message(self):
        import httpx
        from unittest.mock import patch

        resp = httpx.Response(401, json={"error": {"message": "invalid api key"}})
        with (
            pytest.MonkeyPatch.context() as mp,
            patch("httpx.get", autospec=True, return_value=resp),
        ):
            mp.setenv("OPENAI_API_KEY", "sk-dead")
            err = verify_api_key(_provider("OpenAI"))
        assert err is not None
        assert "OPENAI_API_KEY" in err
        assert "invalid api key" in err

    def test_google_expired_key_400_detected(self):
        """Google reports expired keys as HTTP 400, not 401 (issue trigger)."""
        import httpx
        from unittest.mock import patch

        resp = httpx.Response(
            400,
            json={"error": {"code": 400, "message": "API key expired."}},
        )
        with (
            pytest.MonkeyPatch.context() as mp,
            patch("httpx.get", autospec=True, return_value=resp),
        ):
            mp.setenv("GOOGLE_API_KEY", "expired")
            mp.delenv("GEMINI_API_KEY", raising=False)
            err = verify_api_key(_provider("Google"))
        assert err is not None
        assert "API key expired" in err

    def test_google_probes_google_api_key_when_both_set(self):
        """The google-genai SDK prefers GOOGLE_API_KEY; probe the same key."""
        import httpx
        from unittest.mock import patch

        resp = httpx.Response(200, json={"models": []})
        with (
            pytest.MonkeyPatch.context() as mp,
            patch("httpx.get", autospec=True, return_value=resp) as mock_get,
        ):
            mp.setenv("GOOGLE_API_KEY", "the-google-key")
            mp.setenv("GEMINI_API_KEY", "the-gemini-key")
            assert verify_api_key(_provider("Google")) is None
        headers = mock_get.call_args.kwargs["headers"]
        assert headers["x-goog-api-key"] == "the-google-key"

    def test_valid_key_returns_none(self):
        import httpx
        from unittest.mock import patch

        resp = httpx.Response(200, json={"data": []})
        with (
            pytest.MonkeyPatch.context() as mp,
            patch("httpx.get", autospec=True, return_value=resp),
        ):
            mp.setenv("OPENAI_API_KEY", "sk-live")
            assert verify_api_key(_provider("OpenAI")) is None

    def test_network_error_is_inconclusive(self):
        """Offline / proxy trouble must not block a launch."""
        import httpx
        from unittest.mock import patch

        with (
            pytest.MonkeyPatch.context() as mp,
            patch("httpx.get", autospec=True, side_effect=httpx.ConnectError("down")),
        ):
            mp.setenv("OPENAI_API_KEY", "sk-x")
            assert verify_api_key(_provider("OpenAI")) is None

    def test_server_error_is_inconclusive(self):
        import httpx
        from unittest.mock import patch

        resp = httpx.Response(503, text="overloaded")
        with (
            pytest.MonkeyPatch.context() as mp,
            patch("httpx.get", autospec=True, return_value=resp),
        ):
            mp.setenv("OPENAI_API_KEY", "sk-x")
            assert verify_api_key(_provider("OpenAI")) is None

    def test_check_api_key_for_model_surfaces_rejection(self):
        """The wizard/launch funnel (check_api_key_for_model) propagates a
        live rejection for a registry alias — the user-facing behavior."""
        import httpx
        from unittest.mock import patch

        resp = httpx.Response(
            400, json={"error": {"message": "API key expired. Please renew."}}
        )
        with (
            pytest.MonkeyPatch.context() as mp,
            patch("httpx.get", autospec=True, return_value=resp),
        ):
            mp.setenv("GOOGLE_API_KEY", "expired")
            mp.delenv("GEMINI_API_KEY", raising=False)
            err = check_api_key_for_model("gemini-flash")
        assert err is not None and "API key expired" in err

    def test_check_api_key_for_model_missing_key_message_unchanged(self):
        with pytest.MonkeyPatch.context() as mp:
            mp.delenv("GOOGLE_API_KEY", raising=False)
            mp.delenv("GEMINI_API_KEY", raising=False)
            err = check_api_key_for_model("gemini-flash")
        assert err is not None and "not set" in err


class TestOrchestratorGrade:
    """Weak models are excluded from orchestrator selection but remain
    resolvable as aliases (for worker configs and the Custom... entry)."""

    def test_weak_models_hidden_from_orchestrator_wizard(self):
        with pytest.MonkeyPatch.context() as mp:
            for var in ("OPENROUTER_API_KEY", "MISTRAL_API_KEY", "GROQ_API_KEY"):
                mp.setenv(var, "k")
            aliases = [a for a, _d, _p in available_model_choices()]
        assert "mistral-large" in aliases
        weak = (
            "openrouter-auto",
            "nemotron",
            "nemotron-free",
            "codestral",
            "llama-4-scout",
            "llama-70b",
        )
        for alias in weak:
            assert alias not in aliases, alias

    def test_weak_aliases_still_resolve(self):
        assert resolve_model("codestral") == "mistral:codestral-latest"
        assert resolve_model("nemotron-free").startswith("openrouter:")
        assert resolve_model("llama-70b").startswith("groq:")


class TestProbeKeysAsync:
    """probe_keys_async probes configured providers in parallel and reports
    only definitive rejections, keyed by provider name."""

    def test_collects_only_rejections(self):
        from unittest.mock import patch

        google, openai = _provider("Google"), _provider("OpenAI")

        def fake_verify(provider):
            return "GOOGLE_API_KEY was rejected" if provider.name == "Google" else None

        with (
            patch(
                "kodo.models.available_providers",
                autospec=True,
                return_value=[google, openai],
            ),
            patch(
                "kodo.models.verify_api_key", autospec=True, side_effect=fake_verify
            ),
        ):
            results = probe_keys_async()()
        assert results == {"Google": "GOOGLE_API_KEY was rejected"}

    def test_no_configured_providers_yields_empty(self):
        from unittest.mock import patch

        with patch("kodo.models.available_providers", autospec=True, return_value=[]):
            assert probe_keys_async()() == {}
