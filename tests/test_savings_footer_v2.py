"""Tests for render_footer_v2 and pricing_family_for_model."""

import pytest
from burnless.savings_footer import (
    pricing_family_for_model,
    render_footer_v2,
    TurnMetrics,
)


class TestFamilyResolution:
    """Test pricing_family_for_model model→family mapping."""

    def test_haiku(self):
        assert pricing_family_for_model("claude-haiku-4-5-20251001") == "haiku"

    def test_sonnet(self):
        assert pricing_family_for_model("claude-sonnet-5-20250514") == "sonnet"

    def test_opus(self):
        assert pricing_family_for_model("claude-opus-4-20250514") == "opus"

    def test_fable(self):
        assert pricing_family_for_model("claude-fable-5-20250514") == "fable"

    def test_gpt_unknown_fallback(self):
        # Unknown gpt variant falls back to "gpt" family (sonnet-equivalent pricing)
        result = pricing_family_for_model("gpt-unknown-model-xyz")
        assert result == "gpt"

    def test_local_hf_prefix(self):
        assert pricing_family_for_model("hf.co/unsloth/gemma-4-E4B-it-qat-GGUF:UD-Q4_K_XL") == "gemma"

    def test_local_ollama(self):
        assert pricing_family_for_model("ollama:gemma-4-E4B") == "gemma"

    def test_local_gemma_keyword(self):
        assert pricing_family_for_model("gemma-4-E4B-it-qat") == "gemma"

    def test_local_qwen(self):
        assert pricing_family_for_model("qwen:7b") == "gemma"

    def test_local_llama(self):
        assert pricing_family_for_model("llama-2:70b") == "gemma"

    def test_unknown_default(self):
        assert pricing_family_for_model("some-random-model-xyz") == "sonnet"

    def test_empty_string(self):
        assert pricing_family_for_model("") == "sonnet"


class TestRenderFooterV2Labels:
    """Test render_footer_v2 labels are honest (no % signs, no 'Saved')."""

    def test_labels_honest_no_percentage(self):
        """Output should not contain % sign."""
        metrics = TurnMetrics(
            turn_num=1,
            original_tokens=252000,
            compressed_tokens=254,
            saved_tokens=251746,
            saved_pct=99.9,
            real_usd=0.758,
            burnless_usd=0.002,
            saved_usd=0.756,
            model="opus",
        )
        output = render_footer_v2(
            metrics, did="d123", tier="silver", worker_model="claude-haiku-4-5-20251001"
        )
        assert "%" not in output, "Output should not contain % sign"

    def test_labels_honest_no_saved_word(self):
        """Output should not contain the word 'Saved'."""
        metrics = TurnMetrics(
            turn_num=1,
            original_tokens=252000,
            compressed_tokens=254,
            saved_tokens=251746,
            saved_pct=99.9,
            real_usd=0.758,
            burnless_usd=0.002,
            saved_usd=0.756,
            model="opus",
        )
        output = render_footer_v2(
            metrics, did="d123", tier="silver", worker_model="claude-haiku-4-5-20251001"
        )
        assert "Saved" not in output, "Output should not contain word 'Saved'"

    def test_contains_required_tokens_and_ratio(self):
        """Output should contain original/compressed tokens and ratio."""
        metrics = TurnMetrics(
            turn_num=1,
            original_tokens=252000,
            compressed_tokens=254,
            saved_tokens=251746,
            saved_pct=99.9,
            real_usd=0.758,
            burnless_usd=0.002,
            saved_usd=0.756,
            model="opus",
        )
        output = render_footer_v2(
            metrics, did="d123", tier="silver", worker_model="claude-haiku-4-5-20251001"
        )
        # Should contain "252k" (original)
        assert "252k" in output, "Output should contain original token count (252k)"
        # Should contain "254" (compressed)
        assert "254" in output, "Output should contain compressed token count (254)"
        # Should contain ratio (252000 / 254 ≈ 992×)
        assert "992×" in output, "Output should contain compression ratio (992×)"
        # Should contain "est." (estimated cost marker)
        assert "est." in output, "Output should contain 'est.' marker"


class TestRenderFooterV2LocalWorker:
    """Test render_footer_v2 with local worker (zero cost)."""

    def test_local_worker_zero_cost(self):
        """Local worker should show 'worker local $0', no 'est. $'."""
        metrics = TurnMetrics(
            turn_num=1,
            original_tokens=50000,
            compressed_tokens=100,
            saved_tokens=49900,
            saved_pct=99.8,
            real_usd=0.0,
            burnless_usd=0.0,
            saved_usd=0.0,
            model="gemma",
        )
        output = render_footer_v2(
            metrics,
            did="d456",
            tier="bronze",
            worker_model="hf.co/unsloth/gemma-4-E4B-it-qat-GGUF:UD-Q4_K_XL",
        )
        assert "worker local $0" in output, "Output should contain 'worker local $0'"
        assert "est. $" not in output, "Output should not contain 'est. $' for local worker"


class TestRenderFooterV2ZeroCompressed:
    """Test render_footer_v2 with zero compressed tokens (no divide-by-zero)."""

    def test_zero_compressed_no_ratio(self):
        """When compressed_tokens=0, ratio should not appear."""
        metrics = TurnMetrics(
            turn_num=1,
            original_tokens=1000,
            compressed_tokens=0,
            saved_tokens=1000,
            saved_pct=100.0,
            real_usd=0.001,
            burnless_usd=0.0,
            saved_usd=0.001,
            model="opus",
        )
        output = render_footer_v2(
            metrics, did="d789", tier="gold", worker_model="claude-opus-4-20250514"
        )
        # Should not crash, should show "1k" → "0 no contexto" without ratio
        assert "1k" in output
        assert "0 no contexto" in output
        assert "×" not in output, "Should not have ratio when compressed=0"
