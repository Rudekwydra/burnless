from __future__ import annotations
from pathlib import Path
import yaml

DEFAULT_CONFIG: dict = {
    "project_name": "Project",
    "language": "pt-BR",
    "mode": "local_first",
    "agents": {
        # Tiers are quality/cost bands, not vendors. Map any provider/CLI here:
        # Claude gold, Codex silver, Ollama bronze; or GPT gold/silver/bronze;
        # or any other mix the user wants.
        "gold": {
            "name": "opus",
            "command": "claude --model opus -p",
            "role": "strategy_architecture",
            "use_for": ["architecture", "complex_reasoning", "high_level_planning"],
        },
        "silver": {
            "name": "sonnet",
            "command": "claude --model sonnet -p",
            "role": "documentation_structuring",
            "use_for": ["docs", "prd", "prompts", "specs"],
        },
        "bronze": {
            "name": "haiku",
            "command": "claude --model haiku -p",
            "role": "summaries_classification",
            "use_for": ["summarize", "classify", "clean_logs"],
        },
    },
    "routing": {
        "gold": [
            "arquitetura", "architecture", "estratégia", "estrategia", "strategy",
            "decisão", "decisao", "decision", "risco", "risk", "conceito",
            "produto", "roadmap", "trade-off", "tradeoff",
        ],
        "silver": [
            "documentação", "documentacao", "documentation", "briefing",
            "prd", "prompt", "especificação", "especificacao", "spec",
            "texto", "readme", "código", "codigo", "code", "erro", "bug",
            "debug", "terminal", "arquivo", "repo", "teste", "test",
            "build", "compilar", "compile", "stack trace", "exception",
            "compression", "simulator",
        ],
        "bronze": [
            "resumir", "resumo", "summarize", "summary", "limpar", "clean",
            "classificar", "classify", "extrair", "extract", "organizar log",
            "tag",
        ],
        # Opt-in hardcore filter: blocks --tier override upgrades when the
        # natural route resolved to a smaller tier.
        "hardcore_filter": False,
    },
    "metrics": {
        "token_estimation_ratio": 4,
        "show_burnless_tokens": True,
        "show_estimated_cost": True,
        # rough USD per 1M input tokens for the avoided expensive call
        "expensive_model_usd_per_million": 15.0,
    },
    "compression": {
        "mode": "balanced",   # canonical: light | balanced | extreme (aliases: safe→light, aggressive→extreme)
        "friendly": True,      # True = Haiku expands capsule into prose; False = print raw capsule (default for extreme)
        "voice_match": True,   # True (default) = decoder mirrors user's tone/slang/warmth in response. ~5% extra input tokens. False = robotic prose.
    },
    "cache_policy": {
        "cache_read_ratio": 0.10,
        "cache_write_ratio": 2.0,
        "expected_future_turns": 8,
        "min_hot_tail_tokens": 1500,
        "estimated_compaction_ratio": 0.30,
        "keep_recent_capsules": 8,
    },
}


def load(path: Path) -> dict:
    if not path.exists():
        return DEFAULT_CONFIG
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    user_comp = data.get("compression", {}) if isinstance(data.get("compression"), dict) else {}
    data = _deep_merge(DEFAULT_CONFIG, data)
    _normalize_legacy_tiers(data)
    from . import compression as _comp
    comp = data.setdefault("compression", {})
    comp["mode"] = _comp.normalize_mode(comp.get("mode", "balanced"))
    if "friendly" not in user_comp:
        comp["friendly"] = comp["mode"] != "extreme"
    return data


def write_default(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(DEFAULT_CONFIG, f, sort_keys=False, allow_unicode=True)


def save(path: Path, config: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, sort_keys=False, allow_unicode=True)


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _normalize_legacy_tiers(data: dict) -> None:
    """Map the old diamond/code tier into the current silver band in memory."""
    agents = data.get("agents")
    if isinstance(agents, dict) and isinstance(agents.get("diamond"), dict):
        diamond = dict(agents["diamond"])
        silver = agents.get("silver")
        if str(diamond.get("name", "")).lower() == "codex" or "codex" in str(
            diamond.get("command", "")
        ):
            agents["silver"] = diamond
        elif not isinstance(silver, dict):
            agents["silver"] = diamond
        agents.pop("diamond", None)

    routing = data.get("routing")
    if isinstance(routing, dict):
        legacy = routing.pop("diamond", None)
        if isinstance(legacy, list):
            silver_rules = routing.setdefault("silver", [])
            if isinstance(silver_rules, list):
                for kw in legacy:
                    if kw not in silver_rules:
                        silver_rules.append(kw)
