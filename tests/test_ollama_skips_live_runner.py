from burnless.ollama_worker import is_ollama_tools_agent


def test_ollama_agent_recognized():
    assert is_ollama_tools_agent({"provider": "ollama-local", "tools": True, "command": "", "name": "gemma", "model": "gemma"}) is True


def test_non_ollama_agent_not_recognized():
    cfg = {"provider": "anthropic", "command": "claude -p", "name": "opus"}
    assert not is_ollama_tools_agent(cfg)
