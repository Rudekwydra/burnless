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
            "command": "claude --model opus -p --setting-sources project,local --output-format stream-json --verbose --include-partial-messages",
            "role": "strategy_architecture",
            "use_for": ["architecture", "complex_reasoning", "high_level_planning"],
        },
        "silver": {
            "name": "sonnet",
            "command": "claude --model sonnet -p --setting-sources project,local --output-format stream-json --verbose --include-partial-messages",
            # Optional multi-provider autobalance:
            # providers:
            #   - provider: anthropic
            #     name: sonnet
            #     command: claude --model sonnet -p --output-format stream-json --verbose --include-partial-messages
            #   - provider: openai
            #     name: gpt-5.5
            #     command: codex exec --model gpt-5.5 --sandbox workspace-write
            #   - provider: openrouter
            #     name: sonnet-openrouter
            #     command: openrouter ...
            #   - provider: gemini
            #     name: gemini-pro
            #     command: gemini -p --model gemini-2.5-pro
            #   - provider: ollama-local
            #     name: qwen-local
            #     command: ollama run qwen2.5-coder
            "role": "documentation_structuring",
            "use_for": ["docs", "prd", "prompts", "specs"],
        },
        "bronze": {
            "name": "haiku",
            "command": "claude --model haiku -p --setting-sources project,local --output-format stream-json --verbose --include-partial-messages",
            "role": "summaries_classification",
            "use_for": ["summarize", "classify", "clean_logs"],
        },
    },
    # ---- per-layer tier (L1 encoder / L2 maestro). L3 = "agents" above. ----
    # preset is a shortcut that resolves the two knobs below. Explicit
    # encoder/maestro entries OVERRIDE the preset. Omitting all three keys
    # keeps legacy behavior (default = "protocol").
    "preset": "protocol",          # "protocol" | "direct"
    "encoder": {"model": None},    # L1: None→from preset; "passthrough"→no-op
    "maestro": {"model": None},    # L2: None→from preset; "off"→short-circuit
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
            "compression", "simulator", "projeto", "repositorio",
            "repositório", "pasta", "diretorio", "diretório", "memoria",
            "memória", "anotacoes", "anotações",
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
    "audit": {
        "auditors": ["bronze", "silver", "gold"],
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
        "local_codec": "auto",  # auto | ollama | hint — auto = use ollama if detected, else hint-only
        "local_codec_model": "qwen2.5-coder:7b",
    },
    "cache_policy": {
        "cache_read_ratio": 0.10,
        "cache_write_ratio": 2.0,
        "expected_future_turns": 8,
        "min_hot_tail_tokens": 1500,
        "estimated_compaction_ratio": 0.30,
        "keep_recent_capsules": 8,
    },
    "privacy": {
        "mode": "cost",       # cost | redact | audit | opaque
        "raw_retention": "plain",  # current default: plain | none | encrypted (planned)
        "key_store": "memory",     # memory | local (planned for audit mode)
    },
    "display": {
        "progress_detail": "brief",  # minimal | brief | full
        # stale_timeout_seconds intentionally absent from DEFAULT_CONFIG so that
        # _TIER_STALE_DEFAULTS (bronze=120, silver=600, gold=900) are used as the
        # fallback instead of a generic 300s value that overrides per-tier defaults.
        # Users can still override explicitly via display.stale_timeout_seconds or
        # display.tier_stale_timeout_seconds.<tier> in their config.yaml.
    },
    "retry": {
        "max_attempts": 1,        # automatic retries before escalating to maestro
        "stale_worker_retry": True,  # retry workers killed by timeout
        "audit_retry": True,      # retry when auditor returns PART
    },
    "parallel_jitter": {
        # QTP-C: when multiple `burnless do` invocations fire concurrently,
        # space out the worker subprocess launches with random jitter to
        # avoid API 529 (overload) cascades.
        "enabled": True,
        "min_s": 0.5,
        "max_s": 2.5,
    },
    "cache_prefix": {
        # QTP-F: when enabled, prompt is structured as
        # [fixed runtime context] → [variable task] → [chain manifest] →
        # [fixed output contract]. Maximizes prompt-cache hit rate across
        # sibling delegations in the same project. Off by default for
        # backwards compatibility (v0.7.0 layout puts runtime context
        # AFTER the task). Turn on for projects with N≥3 sibling
        # delegations to see cache_read_input_tokens climb.
        "enabled": False,
    },
    "cache_worker": {
        # Opt-in SDK worker path with explicit prompt caching controls.
        "enabled": False,
    },
    "visual_review": {
        # QTP-E: when worker emits files_touched containing visual
        # deliverables (.png/.jpg/.pdf/.pptx/.html/.svg), attach a 256×256
        # base64 JPEG thumbnail to the audit JSON. Operator can scan for
        # "obviously wrong" output without opening files. Uses Pillow if
        # available, sips on macOS as fallback. Default ON; thumbnails on
        # since both tools are commonly available.
        "enabled": True,
        "thumbnails": True,
        "max_size": 256,
        "max_artifacts": 5,
    },
}


_TIER_STALE_DEFAULTS: dict[str, int] = {
    "bronze": 120,
    "silver": 600,
    "gold": 900,
    "platinum": 1800,
}


def resolve_stale_timeout(cfg: dict, tier: str, cli_override: int | None = None) -> int:
    """Resolve stale_timeout in seconds for the given tier.

    Precedence (high → low):
      1. cli_override (--stale-timeout-s flag)
      2. display.tier_stale_timeout_seconds.<tier>
      3. display.stale_timeout_seconds (explicit user override only — not present in DEFAULT_CONFIG)
      4. _TIER_STALE_DEFAULTS[tier]  (bronze=120, silver=600, gold=900, platinum=1800)
      5. 300 (last-resort fallback)
    """
    if cli_override is not None and cli_override > 0:
        return int(cli_override)
    display = cfg.get("display", {}) or {}
    tier_map = display.get("tier_stale_timeout_seconds") or {}
    if isinstance(tier_map, dict) and tier in tier_map:
        try:
            v = int(tier_map[tier])
            if v > 0:
                return v
        except (TypeError, ValueError):
            pass
    legacy = display.get("stale_timeout_seconds")
    if legacy is not None:
        try:
            v = int(legacy)
            if v > 0:
                return v
        except (TypeError, ValueError):
            pass
    return _TIER_STALE_DEFAULTS.get(tier, 300)


def load(path: Path) -> dict:
    if not path.exists():
        return DEFAULT_CONFIG
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    user_agents = data.get("agents") if isinstance(data.get("agents"), dict) else {}
    legacy_diamond_only = "diamond" in user_agents and "silver" not in user_agents
    user_comp = data.get("compression", {}) if isinstance(data.get("compression"), dict) else {}
    data = _deep_merge(DEFAULT_CONFIG, data)
    _normalize_legacy_tiers(data, prefer_diamond=legacy_diamond_only)
    from . import compression as _comp
    comp = data.setdefault("compression", {})
    comp["mode"] = _comp.normalize_mode(comp.get("mode", "balanced"))
    if "friendly" not in user_comp:
        comp["friendly"] = comp["mode"] != "extreme"
    return data


def write_default(path: Path, agents_override: dict | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cfg = dict(DEFAULT_CONFIG)
    if agents_override is not None:
        cfg = dict(cfg)
        cfg["agents"] = agents_override
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True)


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


def _normalize_legacy_tiers(data: dict, *, prefer_diamond: bool = False) -> None:
    """Handle the diamond tier key.

    Legacy behaviour (pre-v0.7): diamond was an alias for the Codex/code
    worker and got collapsed into silver. That migration is preserved when
    diamond and silver share the same command (i.e. they are the same agent).

    New behaviour: if diamond has a *different* command than silver it is a
    real opt-in escalation tier (e.g. Opus as a explicit second-opinion tier
    while gold is Sonnet). In that case diamond is kept as-is — it is never
    auto-routed, only reachable via --tier diamond.
    """
    agents = data.get("agents")
    if not isinstance(agents, dict):
        return
    diamond = agents.get("diamond")
    if not isinstance(diamond, dict):
        return

    # prefer_diamond=True means user had ONLY diamond and no silver — always legacy
    if prefer_diamond:
        agents["silver"] = dict(diamond)
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
        return

    silver = agents.get("silver")
    diamond_cmd = diamond.get("command", "")
    silver_cmd = silver.get("command", "") if isinstance(silver, dict) else ""

    # Different commands → real opt-in tier, preserve diamond untouched
    if diamond_cmd and silver_cmd and diamond_cmd.strip() != silver_cmd.strip():
        return

    # Same command → legacy alias, collapse into silver
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


HAIKU_MODEL = "claude-haiku-4-5-20251001"

_PRESET_RESOLUTIONS = {
    "protocol": {"encoder": HAIKU_MODEL, "maestro": HAIKU_MODEL},
    "direct":   {"encoder": "passthrough", "maestro": "off"},
}


def resolve_layer_models(cfg: dict) -> dict:
    """Resolve per-layer model settings.
    Precedence: explicit cfg[encoder|maestro][model] (non-None) > preset > "protocol".
    Returns {"encoder": <model|"passthrough">, "maestro": <model|"off">}. Never raises.
    """
    preset = (cfg.get("preset") or "protocol")
    base = _PRESET_RESOLUTIONS.get(preset, _PRESET_RESOLUTIONS["protocol"])
    enc_cfg = cfg.get("encoder") or {}
    mae_cfg = cfg.get("maestro") or {}
    enc_explicit = enc_cfg.get("model")
    mae_explicit = mae_cfg.get("model")
    return {
        "encoder": enc_explicit if enc_explicit is not None else base["encoder"],
        "maestro": mae_explicit if mae_explicit is not None else base["maestro"],
    }
