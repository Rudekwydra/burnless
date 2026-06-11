"""Test that Fable 5 is present in anthropic_models."""
from burnless.menu import anthropic_models


def test_fable_in_anthropic_models(monkeypatch):
    """fable must appear in the anthropic model list (curated fallback guaranteed)."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = anthropic_models()
    assert "fable" in result


def test_opus_in_anthropic_models(monkeypatch):
    """opus must also appear in the anthropic model list."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = anthropic_models()
    assert "opus" in result
