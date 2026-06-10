"""Tests for economy model_family routing and non-Anthropic worker pricing."""

from burnless.economy import model_family, _usage_cost_usd
from burnless.pricing import rate


def test_model_family_gemma():
    """gemma family pricing."""
    assert model_family("gemma-4-12b-it-local") == "gemma"


def test_model_family_gpt():
    """gpt family pricing (codex → gpt alias)."""
    assert model_family("codex-gpt-5.2") == "gpt"
    assert model_family("gpt-5.2") == "gpt"


def test_model_family_gemini():
    """gemini family pricing."""
    assert model_family("gemini-3-flash") == "gemini"


def test_model_family_regression():
    """Existing Claude models unchanged."""
    assert model_family("claude-haiku-4-5") == "haiku"


def test_gemma_rates_zero():
    """gemma has zero marginal cost."""
    assert rate("gemma", "output") == 0.0
    assert rate("gemma", "input") == 0.0
    assert rate("gemma", "cache_read") == 0.0
    assert rate("gemma", "cache_write") == 0.0


def test_gemma_usage_cost_zero():
    """gemma worker usage prices to ~$0."""
    cost = _usage_cost_usd(
        {
            "model": "gemma-4-12b-it-local",
            "output_tokens": 5000,
            "input_tokens": 1000,
        },
        "sonnet",
    )
    assert cost == 0.0
