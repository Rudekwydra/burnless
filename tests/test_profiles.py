from __future__ import annotations
import os
from pathlib import Path

import pytest
import yaml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        yaml.safe_dump(data, f)


def _make_burnless_root(tmp_path: Path) -> tuple[Path, Path]:
    """Returns (config_path, profiles_dir)."""
    config = tmp_path / "config.yaml"
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    return config, profiles


# ---------------------------------------------------------------------------
# Fixtures / monkeypatching
# ---------------------------------------------------------------------------

@pytest.fixture()
def fake_home(tmp_path, monkeypatch):
    """Redirect profiles._PROFILES_DIR and _CONFIG_BASE to tmp_path."""
    import burnless.profiles as prof
    config = tmp_path / "config.yaml"
    profiles_dir = tmp_path / "profiles"
    state_dir = tmp_path / "state"
    monkeypatch.setattr(prof, "_PROFILES_DIR", profiles_dir)
    monkeypatch.setattr(prof, "_CONFIG_BASE", config)
    monkeypatch.setattr(prof, "_STATE_BASE", state_dir)
    return tmp_path, config, profiles_dir, state_dir


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_resolve_profile_default(fake_home):
    _tmp, config, _profiles, _state = fake_home
    _write_yaml(config, {"project_name": "MyProject", "language": "pt-BR"})

    from burnless.profiles import resolve_profile
    cfg = resolve_profile(None)
    assert cfg["project_name"] == "MyProject"
    assert cfg["language"] == "pt-BR"


def test_resolve_profile_default_missing_config(fake_home):
    # config.yaml doesn't exist → empty dict
    from burnless.profiles import resolve_profile
    cfg = resolve_profile(None)
    assert isinstance(cfg, dict)


def test_resolve_profile_named(fake_home):
    _tmp, config, profiles_dir, _state = fake_home
    _write_yaml(config, {"project_name": "Base"})
    profiles_dir.mkdir(exist_ok=True)
    _write_yaml(profiles_dir / "myprof.yaml", {"brain": {"provider": "ollama"}})

    from burnless.profiles import resolve_profile
    cfg = resolve_profile("myprof")
    assert cfg["brain"]["provider"] == "ollama"


def test_resolve_profile_extends(fake_home):
    _tmp, config, profiles_dir, _state = fake_home
    _write_yaml(config, {
        "project_name": "Base",
        "brain": {"provider": "anthropic", "model": "base-model"},
        "keepalive": {"enabled": False},
    })
    profiles_dir.mkdir(exist_ok=True)
    _write_yaml(profiles_dir / "claude.yaml", {
        "extends": "../config.yaml",
        "brain": {"provider": "anthropic", "model": "claude-opus-4-7"},
        "keepalive": {"enabled": True},
    })

    from burnless.profiles import resolve_profile
    cfg = resolve_profile("claude")

    # overridden values
    assert cfg["brain"]["model"] == "claude-opus-4-7"
    assert cfg["keepalive"]["enabled"] is True
    # inherited from base
    assert cfg["project_name"] == "Base"


def test_resolve_profile_extends_deep_merge(fake_home):
    _tmp, config, profiles_dir, _state = fake_home
    _write_yaml(config, {
        "agents": {
            "gold": {"name": "opus", "command": "claude --model opus"},
            "silver": {"name": "sonnet", "command": "claude --model sonnet"},
        }
    })
    profiles_dir.mkdir(exist_ok=True)
    _write_yaml(profiles_dir / "codex.yaml", {
        "extends": "../config.yaml",
        "agents": {
            "silver": {"name": "codex-silver", "command": "codex exec"},
        }
    })

    from burnless.profiles import resolve_profile
    cfg = resolve_profile("codex")
    # deep merge: gold preserved, silver overridden
    assert cfg["agents"]["gold"]["name"] == "opus"
    assert cfg["agents"]["silver"]["name"] == "codex-silver"


def test_list_profiles(fake_home):
    _tmp, _config, profiles_dir, _state = fake_home
    profiles_dir.mkdir(exist_ok=True)
    for name in ("alpha", "beta", "gamma"):
        _write_yaml(profiles_dir / f"{name}.yaml", {})
    # underscore-prefixed files should be excluded
    _write_yaml(profiles_dir / "_autodetect.yaml", {})

    from burnless.profiles import list_profiles
    names = list_profiles()
    assert names == ["alpha", "beta", "gamma"]


def test_list_profiles_empty(fake_home):
    from burnless.profiles import list_profiles
    # profiles_dir doesn't exist yet
    assert list_profiles() == []


def test_init_profile_bare(fake_home):
    _tmp, _config, profiles_dir, _state = fake_home

    from burnless.profiles import init_profile
    path = init_profile("myprof")
    assert path.exists()
    data = yaml.safe_load(path.read_text())
    assert "extends" in data


def test_init_profile_claude(fake_home):
    _tmp, _config, profiles_dir, _state = fake_home

    from burnless.profiles import init_profile
    path = init_profile("work", template="claude")
    assert path.exists()
    data = yaml.safe_load(path.read_text())
    assert data["brain"]["provider"] == "anthropic"
    assert data["keepalive"]["enabled"] is True


def test_init_profile_ollama(fake_home):
    _tmp, _config, profiles_dir, _state = fake_home

    from burnless.profiles import init_profile
    path = init_profile("local", template="ollama")
    data = yaml.safe_load(path.read_text())
    assert data["brain"]["provider"] == "ollama"
    assert data["keepalive"]["enabled"] is False


def test_get_active_profile_env(monkeypatch):
    monkeypatch.setenv("BURNLESS_PROFILE", "testprof")
    from burnless.profiles import get_active_profile
    assert get_active_profile() == "testprof"


def test_get_active_profile_unset(monkeypatch):
    monkeypatch.delenv("BURNLESS_PROFILE", raising=False)
    from burnless.profiles import get_active_profile
    assert get_active_profile() is None


def test_state_isolation(fake_home):
    _tmp, _config, _profiles, state_dir = fake_home

    from burnless.profiles import get_state_path
    default_path = get_state_path(None)
    claude_path = get_state_path("claude")
    codex_path = get_state_path("codex")

    assert default_path != claude_path
    assert claude_path != codex_path
    assert "claude" in str(claude_path)
    assert "codex" in str(codex_path)
    assert claude_path.name == "state.json"
