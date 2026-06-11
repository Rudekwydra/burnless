import pytest
from burnless.agents import is_available, AgentError


def test_ollama_tools_agent_is_available():
    """ollama-local tools agent with empty command should be available."""
    agent_cfg = {
        "name": "gemma4:e2b",
        "command": "",
        "provider": "ollama-local",
        "tools": True,
    }
    assert is_available(agent_cfg) is True


def test_normal_agent_not_found():
    """Normal agent with non-existent binary should return False."""
    agent_cfg = {
        "name": "x",
        "command": "definitely-not-a-real-binary-xyz123 --flag",
        "provider": "anthropic",
    }
    assert is_available(agent_cfg) is False
