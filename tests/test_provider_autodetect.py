from unittest.mock import patch
from burnless.provider_autodetect import (
    detect_providers,
    build_agents,
    describe,
    _both,
    _claude_only,
    _codex_only,
    _neither,
)


def test_detect_returns_paths_when_found():
    with patch("burnless.provider_autodetect.shutil.which") as m:
        m.side_effect = lambda name: f"/usr/bin/{name}" if name in ("claude", "codex") else None
        result = detect_providers()
        assert result["claude"] == "/usr/bin/claude"
        assert result["codex"] == "/usr/bin/codex"


def test_detect_returns_none_when_missing():
    with patch("burnless.provider_autodetect.shutil.which", return_value=None):
        result = detect_providers()
        assert result["claude"] is None
        assert result["codex"] is None


def test_build_agents_both():
    agents = build_agents({"claude": "/c", "codex": "/x"})
    assert agents["gold"]["name"] == "claude-opus"
    assert agents["silver"]["name"] == "codex-gpt-5.2"
    assert agents["bronze"]["name"] == "claude-haiku-4-5"
    assert "providers" in agents["silver"]
    assert len(agents["silver"]["providers"]) == 2
    assert agents["silver"]["providers"][0]["provider"] == "codex"
    assert agents["silver"]["providers"][1]["provider"] == "anthropic"


def test_build_agents_claude_only():
    agents = build_agents({"claude": "/c", "codex": None})
    assert agents["gold"]["name"] == "claude-opus"
    assert agents["silver"]["name"] == "claude-sonnet-4-6"
    assert agents["bronze"]["name"] == "claude-haiku-4-5"
    assert "providers" not in agents["silver"]
    assert "codex" not in agents["silver"]["command"]


def test_build_agents_codex_only():
    agents = build_agents({"claude": None, "codex": "/x"})
    assert agents["gold"]["name"] == "codex-gpt-5.4"
    assert agents["silver"]["name"] == "codex-gpt-5.2"
    assert agents["bronze"]["name"] == "codex-gpt-5.4-mini-low"
    assert "claude" not in agents["gold"]["command"]


def test_build_agents_neither():
    agents = build_agents({"claude": None, "codex": None})
    assert agents["gold"]["name"] == "opus"
    assert agents["silver"]["name"] == "sonnet"
    assert agents["bronze"]["name"] == "haiku"


def test_describe_both():
    out = describe({"claude": "/c", "codex": "/x"})
    assert "/c" in out and "/x" in out
    assert "codex-gpt-5.2" in out


def test_describe_neither_warns():
    out = describe({"claude": None, "codex": None})
    assert "WARNING" in out


def test_detect_includes_ollama_path():
    from burnless.provider_autodetect import detect_providers
    detected = detect_providers()
    assert "ollama" in detected
