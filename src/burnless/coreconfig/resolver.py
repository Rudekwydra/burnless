"""Cascade + lookups for the config spine. Everything routes through DEFAULT_TIERS.

Mirrors the legacy behavior of:
  - config.py  load()  (defaults -> ~/.config/burnless/config.yaml -> project/.burnless/config.yaml)
  - config.py  _deep_merge / resolve_model / _extract_model_token / normalize_model
  - routing.py route() precedence (gold kw -> path hint -> silver kw -> bronze kw -> default)

without importing or mutating those modules.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from .schema import DEFAULT_TIERS, DEFAULT_AGENTS, DEFAULT_CACHE_MODES, TierDefinition, Agent, CacheMode

# Mirrored from config.py MODEL_ALIASES.
MODEL_ALIASES = {
    "opus": "claude-opus-4-8",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5-20251001",
    "claude-haiku-4-5": "claude-haiku-4-5-20251001",
}

# Mirrored from routing.py PATH_HINT_RE.
PATH_HINT_RE = re.compile(r"(^|\s)(~?/|/Users/|\./|\.\./)[^\s]+")


def _deep_merge(base: dict, override: dict) -> dict:
    """Identical semantics to config.py _deep_merge."""
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _extract_model_token(cmd: str) -> str | None:
    """Mirrored from config.py."""
    m = re.search(r"(?:--model|-m)\s+(\S+)", cmd)
    return m.group(1) if m else None


def normalize_model(name: str | None) -> str | None:
    """Mirrored from config.py."""
    if not name:
        return name
    return MODEL_ALIASES.get(name.strip(), name.strip())


def default_config() -> dict:
    """Build the baseline config dict DERIVED from DEFAULT_TIERS.

    This is the spine's analogue of config.DEFAULT_CONFIG, but the agents and
    routing blocks are generated from the single source of truth instead of
    being hand-duplicated.
    """
    agents: dict[str, dict] = {}
    routing: dict[str, list[str]] = {}
    for tier, td in DEFAULT_TIERS.items():
        agents[tier] = {
            "name": td.name,
            "model": td.model,
            "role": td.role,
            "use_for": list(td.use_for),
        }
        if td.keywords:
            routing[tier] = list(td.keywords)
    return {"agents": agents, "routing": routing}


def load(project_root: Path | None = None) -> dict:
    """Deep-merge cascade: DEFAULT_TIERS-derived defaults -> global -> project.

    Precedence (low -> high), matching config.py load():
      1. defaults derived from DEFAULT_TIERS
      2. ~/.config/burnless/config.yaml
      3. <project_root>/.burnless/config.yaml
    """
    def _read(p: Path) -> dict:
        if not p.exists():
            return {}
        with p.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    global_path = Path.home() / ".config" / "burnless" / "config.yaml"
    global_data = _read(global_path)

    project_data: dict = {}
    if project_root is not None:
        project_path = Path(project_root) / ".burnless" / "config.yaml"
        project_data = _read(project_path)

    data = _deep_merge(default_config(), global_data)
    data = _deep_merge(data, project_data)
    return data


def _tier_def(tier: str) -> TierDefinition:
    return DEFAULT_TIERS.get(tier) or DEFAULT_TIERS["silver"]


def resolve_model(tier: str, cfg: dict | None = None) -> str:
    """cfg agents[tier] override wins, else DEFAULT_TIERS[tier].model.

    Override resolution mirrors config.resolve_model: command --model token,
    then explicit name/model key, then the spine default.
    """
    if cfg:
        agents = cfg.get("agents", {}) or {}
        agent = agents.get(tier, {}) or {}
        cmd = agent.get("command", "")
        if cmd:
            token = _extract_model_token(cmd)
            if token:
                return normalize_model(token)
        model = agent.get("model")
        if model:
            return normalize_model(model)
        name = agent.get("name")
        if name and name not in DEFAULT_TIERS:
            # a bare tier-name ("silver") is not a model id; ignore it and
            # fall through to the spine. A real alias ("opus") is honored.
            if name in MODEL_ALIASES:
                return normalize_model(name)
    return _tier_def(tier).model


def resolve_keywords(tier: str, cfg: dict | None = None) -> list[str]:
    """cfg routing[tier] override wins, else DEFAULT_TIERS[tier].keywords."""
    if cfg:
        routing = cfg.get("routing", {}) or {}
        kws = routing.get(tier)
        if isinstance(kws, list):
            return list(kws)
    return list(_tier_def(tier).keywords)


def resolve_priority(tier: str) -> int:
    """Priority rank from the spine (higher == routed first)."""
    return _tier_def(tier).priority


def route(text: str, cfg: dict | None = None) -> tuple[str, str]:
    """Return (tier, matched_keyword). Mirrors routing.py route() precedence.

    gold keyword -> path hint (silver) -> built-in silver hints (folded into
    silver keywords here) -> silver keyword -> bronze keyword -> default bronze.
    """
    default_tier = "bronze"
    if not text:
        return default_tier, ""
    haystack = text.lower()

    for kw in resolve_keywords("gold", cfg):
        if kw.lower() in haystack:
            return "gold", kw

    if PATH_HINT_RE.search(text):
        return "silver", "path"

    for tier in ("silver", "bronze"):
        for kw in resolve_keywords(tier, cfg):
            if kw.lower() in haystack:
                return tier, kw

    return default_tier, ""


def resolve_agent(name: str, cfg: dict | None = None) -> Agent:
    base = DEFAULT_AGENTS.get(name)
    if base is None:
        base = Agent(name=name, role="execute")
    import dataclasses
    fields = {f.name: getattr(base, f.name) for f in dataclasses.fields(base)}

    if cfg:
        if name == "maestro":
            overrides = cfg.get("maestro") or {}
        else:
            overrides = (cfg.get("agents") or {}).get(name) or {}

        for key in ("provider", "auth", "model", "role", "tools", "rules"):
            if key in overrides:
                fields[key] = overrides[key]

        if fields["model"] is None:
            cmd = overrides.get("command", "")
            if cmd:
                token = _extract_model_token(cmd)
                if token:
                    fields["model"] = normalize_model(token)

    return Agent(**fields)


def resolve_cache_mode(agent: Agent, cfg: dict | None = None) -> CacheMode:
    if agent.provider == "anthropic":
        key = f"anthropic_{agent.auth}"
    elif agent.provider == "codex":
        key = "codex"
    else:
        key = "none"

    base = DEFAULT_CACHE_MODES.get(key, DEFAULT_CACHE_MODES["none"])

    if cfg:
        overrides = (cfg.get("cache_modes") or {}).get(key) or {}
        if overrides:
            import dataclasses
            fields = {f.name: getattr(base, f.name) for f in dataclasses.fields(base)}
            for k, v in overrides.items():
                if k in fields:
                    fields[k] = v
            return CacheMode(**fields)

    return base
