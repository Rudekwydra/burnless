import pytest
from burnless.config import parse_worker_spec, build_worker_agent, apply_worker_overrides


def test_parse_worker_spec_with_colon():
    """Test parsing 'provider:model' format."""
    provider, model = parse_worker_spec("ollama:gemma4-e4b")
    assert provider == "ollama"
    assert model == "gemma4-e4b"


def test_parse_worker_spec_no_colon():
    """Test parsing bare model name defaults to anthropic."""
    provider, model = parse_worker_spec("sonnet")
    assert provider == "anthropic"
    assert model == "sonnet"


def test_parse_worker_spec_whitespace():
    """Test that whitespace is stripped."""
    provider, model = parse_worker_spec("  ollama : gemma4-e4b  ")
    assert provider == "ollama"
    assert model == "gemma4-e4b"


def test_build_worker_agent_ollama():
    """Test ollama agent dict: provider=='ollama-local', tools truthy, model set."""
    agent = build_worker_agent("ollama", "gemma4-e4b")
    assert agent["provider"] == "ollama-local"
    assert agent["tools"] is True
    assert agent["model"] == "gemma4-e4b"
    assert agent["name"] == "gemma4-e4b"
    assert agent["command"] == ""


def test_build_worker_agent_anthropic():
    """Test anthropic agent: provider=='anthropic', 'sonnet' in command."""
    agent = build_worker_agent("anthropic", "sonnet")
    assert agent["provider"] == "anthropic"
    assert "sonnet" in agent["command"]
    assert agent["name"] == "sonnet"
    assert "claude -p --model" in agent["command"]


def test_build_worker_agent_codex():
    """Test codex agent: provider=='codex'."""
    agent = build_worker_agent("codex", "gpt-5.2")
    assert agent["provider"] == "codex"
    assert agent["name"] == "gpt-5.2"
    assert "codex exec" in agent["command"]


def test_build_worker_agent_gemini():
    """Test gemini agent: provider=='gemini', model in command."""
    agent = build_worker_agent("gemini", "gemini-pro")
    assert agent["provider"] == "gemini"
    assert "gemini-pro" in agent["command"]
    assert "gemini -p --model" in agent["command"]


def test_build_worker_agent_unknown_provider():
    """Test that unknown provider raises ValueError."""
    with pytest.raises(ValueError) as exc_info:
        build_worker_agent("nope", "x")
    assert "unknown provider: nope" in str(exc_info.value)


def test_apply_worker_overrides_mutation_safety():
    """Test that input cfg is NOT mutated (deep copy semantics)."""
    original_cfg = {
        "agents": {
            "silver": {
                "name": "old",
                "command": "old-command",
            }
        }
    }
    overrides = {"silver": "ollama:gemma4-e4b"}

    result = apply_worker_overrides(original_cfg, overrides)

    # Result should have ollama agent
    assert result["agents"]["silver"]["provider"] == "ollama-local"
    assert result["agents"]["silver"]["model"] == "gemma4-e4b"

    # Original cfg must be unchanged
    assert original_cfg["agents"]["silver"]["name"] == "old"
    assert original_cfg["agents"]["silver"]["command"] == "old-command"


def test_apply_worker_overrides_multiple_tiers():
    """Test overriding multiple tiers at once."""
    cfg = {
        "agents": {
            "bronze": {"name": "haiku"},
            "silver": {"name": "sonnet"},
            "gold": {"name": "opus"},
        }
    }
    overrides = {
        "bronze": "ollama:qwen",
        "silver": "codex:gpt-5.2",
    }

    result = apply_worker_overrides(cfg, overrides)

    assert result["agents"]["bronze"]["provider"] == "ollama-local"
    assert result["agents"]["bronze"]["model"] == "qwen"

    assert result["agents"]["silver"]["provider"] == "codex"
    assert "codex exec" in result["agents"]["silver"]["command"]

    # Gold unchanged
    assert result["agents"]["gold"]["name"] == "opus"


def test_apply_worker_overrides_empty_overrides():
    """Test that empty overrides returns a copy with no changes."""
    cfg = {
        "agents": {
            "silver": {"name": "sonnet", "command": "cmd"}
        }
    }

    result = apply_worker_overrides(cfg, {})

    assert result["agents"]["silver"]["name"] == "sonnet"
    assert result["agents"]["silver"]["command"] == "cmd"
    # Verify it's a copy
    assert result is not cfg
    assert result["agents"] is not cfg["agents"]


def test_apply_worker_overrides_none_overrides():
    """Test that None overrides is treated as empty."""
    cfg = {"agents": {"silver": {"name": "sonnet"}}}
    result = apply_worker_overrides(cfg, None)
    assert result["agents"]["silver"]["name"] == "sonnet"
    assert result is not cfg
