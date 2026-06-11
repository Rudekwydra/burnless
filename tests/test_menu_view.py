import pytest
from burnless.menu import build_menu_view


def test_build_menu_view_contains_header():
    """Test that build_menu_view returns a string with the header."""
    cfg = {"agents": {"silver": {"name": "gemma4", "provider": "ollama-local"}}}
    default_cfg = {"agents": {"silver": {"name": "sonnet"}}}
    providers = {"anthropic": True, "ollama": True}

    result = build_menu_view(cfg, default_cfg, providers)

    assert isinstance(result, str)
    assert "burnless" in result


def test_build_menu_view_contains_expected_content():
    """Test that build_menu_view contains tier, model, providers, and default hint."""
    cfg = {"agents": {"silver": {"name": "gemma4", "provider": "ollama-local"}}}
    default_cfg = {"agents": {"silver": {"name": "sonnet"}}}
    providers = {"anthropic": True, "ollama": True}

    result = build_menu_view(cfg, default_cfg, providers)

    assert "silver" in result
    assert "gemma4" in result
    assert "providers:" in result
    assert "--default" in result
