"""Tests for the native ollama tool-calling worker."""
import json
import urllib.request


class _MockResponse:
    def __init__(self, data: dict):
        self._data = json.dumps(data).encode()

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def test_is_ollama_tools_agent():
    from burnless.ollama_worker import is_ollama_tools_agent
    assert is_ollama_tools_agent({"provider": "ollama-local", "tools": True})
    assert is_ollama_tools_agent({"provider": "ollama-local", "tools": True, "model": "x"})
    assert not is_ollama_tools_agent({"provider": "anthropic"})
    assert not is_ollama_tools_agent({"provider": "ollama-local", "tools": False})
    assert not is_ollama_tools_agent({"provider": "ollama-local"})


def test_run_ollama_tools_writes_file(tmp_path, monkeypatch):
    from burnless.ollama_worker import run_ollama_tools

    target = tmp_path / "out.txt"
    call_count = [0]

    def fake_urlopen(req, timeout=None):
        c = call_count[0]
        call_count[0] += 1
        if c == 0:
            return _MockResponse({
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "escrever_arquivo",
                                "arguments": {
                                    "caminho": str(target),
                                    "conteudo": "hello from test",
                                },
                            }
                        }
                    ],
                }
            })
        else:
            return _MockResponse({
                "message": {
                    "role": "assistant",
                    "content": "done writing",
                }
            })

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    env = run_ollama_tools("test-model", "write the file", cwd=str(tmp_path))
    assert env["status"] == "OK"
    assert target.exists()
    assert target.read_text() == "hello from test"
    assert str(target) in env["files_touched"]


def test_run_ollama_tools_http_down_returns_err(monkeypatch):
    from burnless.ollama_worker import run_ollama_tools

    def fake_urlopen(req, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    env = run_ollama_tools("test-model", "do something")
    assert env["status"] == "ERR"


def test_agents_run_routes_ollama_tools(monkeypatch):
    import burnless.ollama_worker as ow
    from burnless import agents

    fake_env = {
        "status": "OK",
        "summary": "done",
        "files_touched": [],
        "validated": [],
        "evidence": [],
        "issues": [],
        "next": "",
    }

    monkeypatch.setattr(ow, "run_ollama_tools", lambda *a, **kw: fake_env)

    cfg = {
        "name": "gemma-local",
        "provider": "ollama-local",
        "tools": True,
        "model": "test-model",
    }
    result = agents._run_once(cfg, "do task", timeout=30)
    assert result["returncode"] == 0
    env_out = json.loads(result["stdout"])
    assert env_out["status"] == "OK"


def test_run_ollama_tools_generic_done_with_file_synthesizes_summary(tmp_path, monkeypatch):
    """Generic final text ('done') + a real file write => summary is synthesized
    from concrete signals, not left as the low-information 'done'."""
    from burnless.ollama_worker import run_ollama_tools

    target = tmp_path / "synth.txt"
    call_count = [0]

    def fake_urlopen(req, timeout=None):
        c = call_count[0]
        call_count[0] += 1
        if c == 0:
            return _MockResponse({
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "escrever_arquivo",
                                "arguments": {
                                    "caminho": str(target),
                                    "conteudo": "x",
                                },
                            }
                        }
                    ],
                }
            })
        return _MockResponse({"message": {"role": "assistant", "content": "done"}})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    env = run_ollama_tools("test-model", "write the file", cwd=str(tmp_path))
    assert env["status"] == "OK"
    assert str(target) in env["files_touched"]
    # low-information 'done' must be replaced by a synthesized summary
    assert env["summary"] != "done"
    assert env["summary"].startswith("wrote 1 file(s):")
    assert str(target) in env["summary"]


def test_run_ollama_tools_generic_done_no_tools_keeps_generic_summary(monkeypatch):
    """Generic final text with no tool calls and no files: nothing to synthesize,
    so the generic summary is preserved and no files are reported."""
    from burnless.ollama_worker import run_ollama_tools

    def fake_urlopen(req, timeout=None):
        return _MockResponse({"message": {"role": "assistant", "content": "done"}})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    env = run_ollama_tools("test-model", "just think")
    assert env["status"] == "OK"
    assert env["files_touched"] == []
    assert env["summary"] == "done"
