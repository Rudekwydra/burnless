"""Tests for i18n message resolution and language configuration."""

import pytest
from burnless.i18n import msg, MESSAGES
from burnless.config import DEFAULT_CONFIG
from burnless.savings_footer import render_footer_v2, render_praise, TurnMetrics


class TestMsgResolution:
    """Test msg() key resolution and language fallback."""

    def test_msg_pt_br_exact(self):
        """msg() with lang='pt-BR' returns Portuguese message."""
        result = msg("footer_input_avoided_local", "pt-BR")
        assert "brutos" not in result  # local worker messages don't have this
        assert "input avoided" not in result  # not EN
        assert "worker local" in result or "evitado" in result

    def test_msg_en_exact(self):
        """msg() with lang='en' returns English message."""
        result = msg("footer_input_avoided_local", "en")
        assert "avoided" in result
        assert "local" in result
        assert "brutos" not in result

    def test_msg_unknown_lang_falls_to_en(self):
        """msg() with unknown lang falls back to 'en'."""
        result = msg("footer_input_avoided_local", "fr")
        assert "avoided" in result  # EN fallback
        assert "brutos" not in result

    def test_msg_with_placeholders_pt_br(self):
        """msg() formats placeholders correctly in Portuguese."""
        result = msg("praise_tokens_compressed", "pt-BR", ratio=500, orig_fmt="100k", comp_fmt="200")
        assert "500×" in result
        assert "100k" in result
        assert "200" in result
        assert "brutos" in result  # PT-BR specific

    def test_msg_with_placeholders_en(self):
        """msg() formats placeholders correctly in English."""
        result = msg("praise_tokens_compressed", "en", ratio=500, orig_fmt="100k", comp_fmt="200")
        assert "500×" in result
        assert "100k" in result
        assert "200" in result
        assert "raw tokens" in result  # EN specific

    def test_msg_unknown_key_raises(self):
        """msg() raises KeyError for unknown key."""
        with pytest.raises(KeyError):
            msg("nonexistent_key", "en")

    def test_msg_nested_delegation_guard_pt_br(self):
        """msg() for nested delegation guard in Portuguese."""
        result = msg("guard_nested_delegation", "pt-BR")
        assert "re-delegacao" in result or "delegação" in result or "bloqueada" in result

    def test_msg_nested_delegation_guard_en(self):
        """msg() for nested delegation guard in English."""
        result = msg("guard_nested_delegation", "en")
        assert "blocked" in result
        assert "brutos" not in result


class TestRenderFooterV2WithLang:
    """Test render_footer_v2 respects lang parameter."""

    def _metrics(self, original, compressed):
        return TurnMetrics(
            turn_num=1,
            original_tokens=original,
            compressed_tokens=compressed,
            saved_tokens=max(0, original - compressed),
            saved_pct=0.0,
            real_usd=0.001,
            burnless_usd=0.0,
            saved_usd=0.001,
        )

    def test_render_footer_v2_pt_br_has_pt_strings(self):
        """render_footer_v2 with lang='pt-BR' contains Portuguese strings."""
        metrics = self._metrics(100000, 200)
        output = render_footer_v2(
            metrics, did="d001", tier="silver", worker_model="claude-opus-4", lang="pt-BR"
        )
        assert "tok brutos" in output or "brutos" in output
        assert "no contexto" in output
        assert "est." in output  # shared format part

    def test_render_footer_v2_en_has_en_strings(self):
        """render_footer_v2 with lang='en' contains English strings."""
        metrics = self._metrics(100000, 200)
        output = render_footer_v2(
            metrics, did="d001", tier="silver", worker_model="claude-opus-4", lang="en"
        )
        assert "raw tokens" in output
        assert "in context" in output
        assert "brutos" not in output
        assert "evitado" not in output  # Portuguese-specific

    def test_render_footer_v2_en_local_worker(self):
        """render_footer_v2 with lang='en' and local worker."""
        metrics = self._metrics(50000, 100)
        output = render_footer_v2(
            metrics, did="d002", tier="bronze", worker_model="gemma-4-E4B", lang="en"
        )
        assert "avoided" in output
        assert "local" in output
        assert "brutos" not in output

    def test_render_footer_v2_default_lang_pt_br(self):
        """render_footer_v2 defaults to 'pt-BR' if lang not provided."""
        metrics = self._metrics(100000, 200)
        output = render_footer_v2(
            metrics, did="d001", tier="silver", worker_model="claude-opus-4"
        )
        # Default is pt-BR, should contain Portuguese strings
        assert "tok brutos" in output or "brutos" in output


class TestRenderPraiseWithLang:
    """Test render_praise respects lang parameter."""

    def _metrics(self, original, compressed):
        return TurnMetrics(
            turn_num=1,
            original_tokens=original,
            compressed_tokens=compressed,
            saved_tokens=max(0, original - compressed),
            saved_pct=0.0,
            real_usd=0.0,
            burnless_usd=0.0,
            saved_usd=0.0,
        )

    def test_render_praise_pt_br_has_pt_strings(self):
        """render_praise with lang='pt-BR' contains Portuguese strings."""
        metrics = self._metrics(130000, 100)
        output = render_praise(metrics, 1000, lang="pt-BR")
        assert output != ""  # Should fire (1300× > 1000)
        assert "brutos" in output
        assert "viraram" in output

    def test_render_praise_en_has_en_strings(self):
        """render_praise with lang='en' contains English strings."""
        metrics = self._metrics(130000, 100)
        output = render_praise(metrics, 1000, lang="en")
        assert output != ""  # Should fire (1300× > 1000)
        assert "became" in output
        assert "brutos" not in output

    def test_render_praise_default_lang_pt_br(self):
        """render_praise defaults to 'pt-BR' if lang not provided."""
        metrics = self._metrics(130000, 100)
        output = render_praise(metrics, 1000)
        assert output != ""
        # Default is pt-BR, should contain Portuguese strings
        assert "brutos" in output


class TestDefaultConfig:
    """Test DEFAULT_CONFIG language setting."""

    def test_default_config_language_is_en(self):
        """DEFAULT_CONFIG language should be 'en'."""
        assert DEFAULT_CONFIG["language"] == "en"
