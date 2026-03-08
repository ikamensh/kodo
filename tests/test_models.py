"""Tests for kodo.models — pricing tables, model mappings, make_fresh_model."""

from __future__ import annotations

from kodo.models import (
    MODEL_PRICING,
    PYDANTIC_MODEL_MAP,
    make_fresh_model,
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
            assert out >= inp, (
                f"{model}: output price ({out}) < input price ({inp})"
            )


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


class TestMakeFreshModel:
    def test_non_google_returns_string(self):
        """Non-Google model strings are returned unchanged."""
        result = make_fresh_model("anthropic:claude-opus-4-6")
        assert result == "anthropic:claude-opus-4-6"

    def test_no_colon_returns_string(self):
        """Model string without provider prefix is returned as-is."""
        result = make_fresh_model("claude-opus-4-6")
        assert result == "claude-opus-4-6"

    def test_google_gla_returns_google_model(self):
        """google-gla: prefix creates a GoogleModel instance."""
        result = make_fresh_model("google-gla:gemini-3-flash-preview")
        # Should return a GoogleModel, not a string
        assert not isinstance(result, str)
        assert type(result).__name__ == "GoogleModel"

    def test_google_vertex_returns_google_model(self):
        """google-vertex: prefix creates a GoogleModel with vertexai=True."""
        result = make_fresh_model("google-vertex:gemini-3-flash-preview")
        assert not isinstance(result, str)
        assert type(result).__name__ == "GoogleModel"
