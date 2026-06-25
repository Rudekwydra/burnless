from __future__ import annotations
import os
from pathlib import Path

from . import config

import yaml

_PROFILES_DIR = Path.home() / ".burnless" / "profiles"
_CONFIG_BASE = Path.home() / ".config/burnless" / "config.yaml"
_STATE_BASE = Path.home() / ".burnless" / "state"

_TEMPLATES: dict[str, dict] = {
    "claude": {
        "extends": "../config.yaml",
        "brain": {
            "provider": "anthropic",
            "model": "claude-sonnet-4-6",
        },
        "keepalive": {"enabled": True, "interval_sec": 270},
    },
    "codex": {
        "extends": "../config.yaml",
        "agents": {
            "silver": {
                "name": "codex-silver",
                "command": "codex exec --model gpt-5.5 --sandbox workspace-write",
            }
        },
        "keepalive": {"enabled": False},
    },
    "ollama": {
        "extends": "../config.yaml",
        "brain": {
            "provider": "ollama",
            "model": "gpt-oss:120b",
            "endpoint": "http://localhost:11434",
        },
        "agents": {
            "bronze": {
                "name": config.DEFAULT_LOCAL_MODEL,
                "provider": "ollama-local",
                "tools": True,
                "model": config.DEFAULT_LOCAL_MODEL,
                "command": "",
            }
        },
        "keepalive": {"enabled": False},
        "cache_policy": {"strategy": "passthrough"},
    },
    "antigrav": {
        "extends": "../config.yaml",
        "brain": {
            "provider": "anthropic",
            "model": "claude-haiku-4-5-20251001",
        },
        "agents": {
            "silver": {
                "name": "haiku-maestro",
                "command": "claude --model haiku -p --output-format stream-json --verbose --include-partial-messages",
            }
        },
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def resolve_profile(name: str | None) -> dict:
    if name is None:
        return _load_yaml(_CONFIG_BASE)

    profile_path = _PROFILES_DIR / f"{name}.yaml"
    profile_data = _load_yaml(profile_path)

    extends = profile_data.pop("extends", None)
    if extends:
        base_path = (profile_path.parent / extends).resolve()
        base_data = _load_yaml(base_path)
        return _deep_merge(base_data, profile_data)

    return profile_data


def list_profiles() -> list[str]:
    if not _PROFILES_DIR.exists():
        return []
    return sorted(
        p.stem for p in _PROFILES_DIR.glob("*.yaml")
        if not p.name.startswith("_")
    )


def get_active_profile() -> str | None:
    return os.environ.get("BURNLESS_PROFILE") or None


def init_profile(name: str, template: str | None = None) -> Path:
    _PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    profile_path = _PROFILES_DIR / f"{name}.yaml"

    if template and template in _TEMPLATES:
        data = _TEMPLATES[template]
    else:
        data = {"extends": "../config.yaml"}

    with profile_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)

    return profile_path


def get_state_path(profile: str | None) -> Path:
    if profile:
        return _STATE_BASE / profile / "state.json"
    return _STATE_BASE / "state.json"
