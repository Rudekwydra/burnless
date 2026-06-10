from pathlib import Path

from burnless import config


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_global_layer_applied(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _write(home / ".config" / "burnless" / "config.yaml",
           "agents:\n  silver:\n    name: globalworker\n    command: GLOBAL_CMD\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("BURNLESS_GLOBAL_CONFIG", str(home / ".config" / "burnless" / "config.yaml"))
    cfg_path = tmp_path / "proj" / ".burnless" / "config.yaml"
    _write(cfg_path, "project_name: ProjX\n")
    cfg = config.load(cfg_path)
    assert cfg["agents"]["silver"]["command"] == "GLOBAL_CMD"
    assert cfg["project_name"] == "ProjX"


def test_project_overrides_global(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _write(home / ".config" / "burnless" / "config.yaml",
           "agents:\n  silver:\n    command: GLOBAL_CMD\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("BURNLESS_GLOBAL_CONFIG", str(home / ".config" / "burnless" / "config.yaml"))
    cfg_path = tmp_path / "proj" / ".burnless" / "config.yaml"
    _write(cfg_path, "agents:\n  silver:\n    command: PROJECT_CMD\n")
    cfg = config.load(cfg_path)
    assert cfg["agents"]["silver"]["command"] == "PROJECT_CMD"


def test_no_files_returns_default(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    cfg = config.load(tmp_path / "nope" / "config.yaml")
    assert "agents" in cfg
