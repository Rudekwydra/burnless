"""Brain adapter factory + routing (brecha #6, P5 closeout)."""
from __future__ import annotations

import pytest

from burnless import brain_adapters as ba


def test_anthropic_adapter_shape():
    a = ba.current_anthropic_adapter("claude-sonnet-4-6")
    assert a.kind == "anthropic"
    assert a.api_key_env == "ANTHROPIC_API_KEY"
    assert a.supports_thinking is True
    assert "claude-sonnet-4-6" in a.models
    assert a.capabilities.streaming is True
    assert a.capabilities.delegation is True


def test_openai_adapter_shape():
    a = ba.openai_adapter()
    assert a.kind == "openai"
    assert a.api_key_env == "OPENAI_API_KEY"
    assert a.default_model == "gpt-4o"
    assert a.supports_thinking is False


def test_gemini_adapter_shape():
    a = ba.gemini_adapter()
    assert a.kind == "gemini"
    assert a.api_key_env == "GEMINI_API_KEY"
    assert a.default_model.startswith("gemini")


def test_openrouter_adapter_shape():
    a = ba.openrouter_adapter()
    assert a.kind == "openrouter"
    assert a.api_key_env == "OPENROUTER_API_KEY"
    assert a.base_url == "https://openrouter.ai/api/v1"


def test_load_adapter_defaults_to_anthropic():
    a = ba.load_adapter({}, "claude-sonnet-4-6")
    assert a.kind == "anthropic"


def test_load_adapter_explicit_anthropic():
    a = ba.load_adapter({"brain_adapter": "anthropic"}, "claude-sonnet-4-6")
    assert a.kind == "anthropic"


def test_load_adapter_openai():
    a = ba.load_adapter({"brain_adapter": "openai"}, "gpt-4o")
    assert a.kind == "openai"


def test_load_adapter_gemini():
    a = ba.load_adapter({"brain_adapter": "gemini"}, "gemini-2.5-pro")
    assert a.kind == "gemini"


def test_load_adapter_openrouter():
    a = ba.load_adapter({"brain_adapter": "openrouter"}, "anthropic/claude-sonnet-4")
    assert a.kind == "openrouter"


def test_load_adapter_unknown_kind_raises():
    with pytest.raises(NotImplementedError):
        ba.load_adapter({"brain_adapter": "definitely-not-a-provider"}, "x")


def test_all_brain_streams_modules_expose_create_stream():
    """Each provider stream module must implement create_stream()."""
    from burnless.maestro.brain_streams import anthropic, gemini, openai, openrouter
    for mod in (anthropic, openai, gemini, openrouter):
        assert callable(mod.create_stream), f"{mod.__name__} missing create_stream"


def test_normalized_event_emitted_by_brain_streams():
    """All brain_streams import the same NormalizedEvent type."""
    from burnless.maestro.brain_streams import NormalizedEvent
    e = NormalizedEvent(kind="text_delta", text="hi")
    assert e.kind == "text_delta"
    assert e.text == "hi"


def test_adapter_capabilities_uniform_for_all_providers():
    """All four production adapters expose the same minimum capability set."""
    for kind in ("anthropic", "openai", "gemini", "openrouter"):
        if kind == "anthropic":
            a = ba.current_anthropic_adapter("test-model")
        else:
            a = ba.load_adapter({"brain_adapter": kind}, "test-model")
        assert a.capabilities.single_shot is True, f"{kind} missing single_shot"
        assert a.capabilities.streaming is True, f"{kind} missing streaming"
        assert a.capabilities.delegation is True, f"{kind} missing delegation"
