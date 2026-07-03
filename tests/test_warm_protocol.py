import burnless.warm_session as _ws_claude
import burnless.warm_session_codex as _ws_codex
from burnless.agents import _inject_warm_fork_args


def test_claude_warm_returns_fork_flags_and_empty_prefix(tmp_path, monkeypatch):
    monkeypatch.setattr(_ws_claude, "warm_args", lambda br, m: ["--resume", "U", "--fork-session"])
    monkeypatch.setattr(_ws_claude, "warm_prefix", lambda br, m: "")
    monkeypatch.setattr(_ws_claude, "worker_cwd", lambda br, m: "/iso/x")
    parts, prefix, iso = _inject_warm_fork_args(["/x/claude", "-p"], cwd=tmp_path)
    assert "--resume" in parts
    assert "U" in parts
    assert "--fork-session" in parts
    assert prefix == ""
    assert iso == "/iso/x"


def test_codex_warm_returns_flags_and_brief_prefix(tmp_path, monkeypatch):
    monkeypatch.setattr(_ws_codex, "warm_flags", lambda br, m: ["-c", "x"])
    monkeypatch.setattr(_ws_codex, "warm_brief", lambda br, m: "BRIEF")
    parts, prefix, iso = _inject_warm_fork_args(
        ["codex", "exec"], cwd=tmp_path
    )
    assert "-c" in parts
    assert "x" in parts
    assert prefix == "BRIEF"


def test_ollama_returns_parts_unchanged_no_warn(tmp_path, capsys):
    original_parts = ["ollama", "run", "llama3"]
    parts, prefix, iso = _inject_warm_fork_args(original_parts, cwd=tmp_path)
    assert parts == original_parts
    assert prefix == ""
    assert iso is None
    captured = capsys.readouterr()
    assert "COLD" not in captured.err


def test_resolve_cache_mode_ollama_no_warm_module():
    from burnless.coreconfig.resolver import resolve_cache_mode
    from burnless.coreconfig.schema import Agent
    m = resolve_cache_mode(Agent(name="x", role="execute", provider="ollama", auth="none"))
    assert m.warm_module is None


def test_run_once_uses_iso_cwd(tmp_path, monkeypatch):
    import subprocess
    import burnless.agents as _agents

    captured_cwd = {}

    def fake_inject(parts, cwd):
        return (parts, "", "/tmp/iso_marker")

    def fake_subprocess_run(*args, **kwargs):
        captured_cwd["cwd"] = kwargs.get("cwd")
        raise subprocess.TimeoutExpired(args[0], 1)

    monkeypatch.setattr(_agents, "_inject_warm_fork_args", fake_inject)
    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)

    import shutil
    monkeypatch.setattr(shutil, "which", lambda x: x)

    agent_cfg = {"name": "test", "command": "echo", "model": "test-model"}
    monkeypatch.setattr(_agents, "resolve_command", lambda cfg: ["echo", "hi"])

    _agents._run_once(agent_cfg, "hello", cwd=tmp_path)
    assert captured_cwd.get("cwd") == "/tmp/iso_marker"
