import pytest
from burnless.menu import provider_of, detect_providers, source_marker, render_models_table


class TestProviderOf:
    def test_explicit_provider_field(self):
        assert provider_of({"provider": "ollama-local"}) == "ollama-local"

    def test_infer_codex_from_command(self):
        assert provider_of({"command": "/Users/x/.local/bin/codex exec --sandbox x"}) == "codex"

    def test_infer_anthropic_from_claude_command(self):
        assert provider_of({"command": "/opt/homebrew/bin/claude -p --model opus"}) == "anthropic"

    def test_infer_gemini_from_command(self):
        assert provider_of({"command": "gemini -p --model gemini-2.5-pro"}) == "gemini"

    def test_default_anthropic(self):
        assert provider_of({}) == "anthropic"


class TestDetectProviders:
    def test_detect_providers_structure(self):
        result = detect_providers()
        assert isinstance(result, dict)
        assert set(result.keys()) == {"anthropic", "codex", "ollama"}
        for key, value in result.items():
            assert isinstance(value, bool)


class TestSourceMarker:
    def test_session_override_wins(self):
        result = source_marker(
            "silver",
            cfg={"agents": {"silver": {"name": "gemma"}}},
            default_cfg={"agents": {"silver": {"name": "sonnet"}}},
            session_overrides={"silver": "ollama:gemma"}
        )
        assert result == "(session)"

    def test_default_when_names_match(self):
        result = source_marker(
            "gold",
            cfg={"agents": {"gold": {"name": "opus"}}},
            default_cfg={"agents": {"gold": {"name": "opus"}}}
        )
        assert result == "(default)"

    def test_global_when_names_differ(self):
        result = source_marker(
            "silver",
            cfg={"agents": {"silver": {"name": "gemma"}}},
            default_cfg={"agents": {"silver": {"name": "sonnet"}}}
        )
        assert result == "(global)"


class TestRenderModelsTable:
    def test_render_contains_expected_fields(self):
        result = render_models_table(
            cfg={"agents": {"silver": {"name": "gemma4", "provider": "ollama-local"}}},
            default_cfg={"agents": {"silver": {"name": "sonnet"}}}
        )
        assert "silver" in result
        assert "gemma4" in result
        assert "ollama-local" in result

    def test_render_includes_header(self):
        result = render_models_table(
            cfg={"agents": {"silver": {"name": "gemma4", "provider": "ollama-local"}}},
            default_cfg={"agents": {"silver": {"name": "sonnet"}}}
        )
        assert "tier" in result
        assert "provider" in result
        assert "model" in result
        assert "source" in result
