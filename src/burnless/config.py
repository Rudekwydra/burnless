from __future__ import annotations
from pathlib import Path
import os
import re
import yaml

DEFAULT_CONFIG: dict = {
    "project_name": "Project",
    "language": "pt-BR",
    "mode": "local_first",
    "agents": {
        # Tiers are quality/cost bands, not vendors. Map any provider/CLI here:
        # Claude gold, Codex silver, Ollama bronze; or GPT gold/silver/bronze;
        # or any other mix the user wants.
        "diamond": {
            "name": "fable",
            "command": "claude --model fable -p --setting-sources project,local --output-format stream-json --verbose --include-partial-messages",
            "role": "hardest_irreversible",
            "use_for": ["hardest_engineering", "irreversible_decisions", "second_opinion"],
        },
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
        "friendly": True,      # True = Haiku expands capsule into prose; False = print raw capsule
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
        "capsule_budget_tokens": 1500,      # CONSTANT ultra-compact capsule size (NOT proportional to window)
        "compaction_cost_tokens": 4000,     # M: input-token-equiv cost of one compaction call (capsule is OUTPUT tokens ~5x)
        "keep_tail_turns": 4,   # turns kept VERBATIM in the window across a rewind (anti-whiplash)
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
    "validation": {
        "honest_exit_code": True,
        "verify_timeout_s": 120,
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

_OLLAMA_LOCAL_STALE_FLOOR = 1800   # local models are slow + free; never false-positive stale on them


def resolve_stale_timeout(cfg: dict, tier: str, cli_override: int | None = None, provider: str | None = None) -> int:
    """Resolve stale_timeout in seconds for the given tier.

    Precedence (high → low):
      1. cli_override (--stale-timeout-s flag) — respected verbatim, no floor applied
      2. display.tier_stale_timeout_seconds.<tier>
      3. display.stale_timeout_seconds (explicit user override only — not present in DEFAULT_CONFIG)
      4. _TIER_STALE_DEFAULTS[tier]  (bronze=120, silver=600, gold=900, platinum=1800)
      5. 300 (last-resort fallback)

    After resolving via the above chain (steps 2-5 only), if provider=="ollama-local" the
    result is floored to _OLLAMA_LOCAL_STALE_FLOOR so slow local models never trip stale detection.
    """
    if cli_override is not None and cli_override > 0:
        return int(cli_override)
    display = cfg.get("display", {}) or {}
    tier_map = display.get("tier_stale_timeout_seconds") or {}
    if isinstance(tier_map, dict) and tier in tier_map:
        try:
            v = int(tier_map[tier])
            if v > 0:
                if provider == "ollama-local":
                    return max(v, _OLLAMA_LOCAL_STALE_FLOOR)
                return v
        except (TypeError, ValueError):
            pass
    legacy = display.get("stale_timeout_seconds")
    if legacy is not None:
        try:
            v = int(legacy)
            if v > 0:
                if provider == "ollama-local":
                    return max(v, _OLLAMA_LOCAL_STALE_FLOOR)
                return v
        except (TypeError, ValueError):
            pass
    v = _TIER_STALE_DEFAULTS.get(tier, 300)
    if provider == "ollama-local":
        return max(v, _OLLAMA_LOCAL_STALE_FLOOR)
    return v


def load(path: Path) -> dict:
    def _read(p: Path) -> dict:
        if not p.exists():
            return {}
        with p.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    env_global = os.environ.get("BURNLESS_GLOBAL_CONFIG")
    if env_global is not None:
        global_data = _read(Path(env_global)) if env_global else {}
    else:
        global_data = _read(Path.home() / ".config" / "burnless" / "config.yaml")
    project_data = _read(path)
    user_data = _deep_merge(global_data, project_data)
    if not user_data:
        return DEFAULT_CONFIG
    user_agents = user_data.get("agents") if isinstance(user_data.get("agents"), dict) else {}
    legacy_diamond_only = "diamond" in user_agents and "silver" not in user_agents
    user_comp = user_data.get("compression", {}) if isinstance(user_data.get("compression"), dict) else {}
    data = _deep_merge(DEFAULT_CONFIG, user_data)
    _normalize_legacy_tiers(data, prefer_diamond=legacy_diamond_only)
    comp = data.setdefault("compression", {})
    if "friendly" not in user_comp:
        comp["friendly"] = True
    return data


def global_config_path() -> Path:
    """Path to the global config (BURNLESS_GLOBAL_CONFIG env wins, else ~/.config/burnless/config.yaml)."""
    env = os.environ.get("BURNLESS_GLOBAL_CONFIG")
    if env:
        return Path(env)
    return Path.home() / ".config" / "burnless" / "config.yaml"


def parse_worker_spec(spec: str) -> tuple[str, str]:
    """'ollama:gemma4-e4b' -> ('ollama','gemma4-e4b'). No colon -> provider 'anthropic'.
    e.g. 'sonnet' -> ('anthropic','sonnet'). Strips whitespace."""
    s = (spec or "").strip()
    if ":" in s:
        provider, _, model = s.partition(":")
        return provider.strip().lower(), model.strip()
    return "anthropic", s


def build_worker_agent(provider: str, model: str) -> dict:
    """Build agent dict for given provider and model.

    Supported providers: anthropic, codex, ollama, gemini.
    Returns dict with keys: name, command, provider (plus model/tools for ollama).
    """
    provider = (provider or "").strip().lower()
    model = (model or "").strip()

    if provider == "anthropic":
        return {
            "name": model,
            "command": f"claude -p --model {model} --permission-mode bypassPermissions --allowedTools Read,Edit,Write,Bash,Glob,Grep,LS --output-format stream-json --verbose --include-partial-messages",
            "provider": "anthropic",
        }
    elif provider == "codex":
        return {
            "name": model,
            "command": "codex exec --skip-git-repo-check --sandbox danger-full-access",
            "provider": "codex",
        }
    elif provider == "ollama":
        return {
            "name": model,
            "provider": "ollama-local",
            "tools": True,
            "model": model,
            "command": "",
        }
    elif provider == "gemini":
        return {
            "name": model,
            "command": f"gemini -p --model {model}",
            "provider": "gemini",
        }
    else:
        raise ValueError(f"unknown provider: {provider}")


def apply_worker_overrides(cfg: dict, overrides: dict) -> dict:
    """Return a deep copy of cfg with cfg['agents'][tier] replaced for each
    tier->spec in overrides. Input cfg is NOT mutated. spec is a 'provider:model'
    string parsed via parse_worker_spec."""
    import copy
    out = copy.deepcopy(cfg)
    agents = out.setdefault("agents", {})
    for tier, spec in (overrides or {}).items():
        provider, model = parse_worker_spec(spec)
        agents[tier] = build_worker_agent(provider, model)
    return out


def write_default(path: Path, agents_override: dict | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cfg = {k: v for k, v in DEFAULT_CONFIG.items() if k != "agents"}
    if agents_override is not None:
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

    # Keep diamond as a real opt-in tier UNLESS it is literally the same agent as
    # silver (same non-empty command). An empty silver command (e.g. an ollama
    # worker) means they are different agents, so diamond is kept.
    if not (diamond_cmd and silver_cmd and diamond_cmd.strip() == silver_cmd.strip()):
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


DEFAULT_TIER_MODELS = {
    "gold": "claude-opus-4-8",
    "silver": "claude-sonnet-4-6",
    "bronze": "claude-haiku-4-5-20251001",
}

DEFAULT_PROVIDER_MODELS = {
    "claude": "claude-sonnet-4-6",
    "codex": "gpt-5.2",
}

HAIKU_MODEL = DEFAULT_TIER_MODELS["bronze"]

MODEL_ALIASES = {
    "opus": "claude-opus-4-8",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5-20251001",
    "claude-haiku-4-5": "claude-haiku-4-5-20251001",
}


def _extract_model_token(cmd: str) -> str | None:
    m = re.search(r"(?:--model|-m)\s+(\S+)", cmd)
    return m.group(1) if m else None


def normalize_model(name: str | None) -> str | None:
    if not name:
        return name
    return MODEL_ALIASES.get(name.strip(), name.strip())


def resolve_model(tier: str, cfg: dict | None = None) -> str:
    if cfg:
        agents = cfg.get("agents", {})
        agent = agents.get(tier, {})
        cmd = agent.get("command", "")
        if cmd:
            token = _extract_model_token(cmd)
            if token:
                return normalize_model(token)
        name = agent.get("name")
        if name:
            return normalize_model(name)
    return DEFAULT_TIER_MODELS.get(tier, DEFAULT_TIER_MODELS["silver"])


def tier_model_label(tier: str, cfg: dict | None = None) -> str:
    """Derive human-readable model label from tier (via resolve_model SSO)."""
    try:
        mid = (resolve_model(tier, cfg) or "").lower()
        if "opus" in mid:
            return "Opus"
        elif "sonnet" in mid:
            return "Sonnet"
        elif "haiku" in mid:
            return "Haiku"
        elif "codex" in mid or "gpt" in mid:
            return "Codex"
        elif any(k in mid for k in ("ollama", "mistral", "llama")):
            return "Ollama"
        else:
            return (mid.split("-")[0] or tier).capitalize()
    except Exception:
        return tier.capitalize()


def resolve_fallback_model(tier: str, cfg: dict | None = None) -> str | None:
    if cfg:
        agents = cfg.get("agents", {})
        agent = agents.get(tier, {})
        providers = agent.get("providers", [])
        if isinstance(providers, list) and len(providers) > 1:
            p = providers[1]
            if isinstance(p, dict):
                cmd = p.get("command", "")
                token = _extract_model_token(cmd) if cmd else None
                if token:
                    return normalize_model(token)
                pname = p.get("name")
                if pname:
                    return normalize_model(pname)
        fallback = agent.get("fallback")
        if fallback:
            return normalize_model(fallback)
    return None


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
