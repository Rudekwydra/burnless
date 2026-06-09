"""Single source of truth for burnless tier definitions.

DEFAULT_TIERS below is THE one place tiers (bronze/silver/gold/diamond) and
their model / keyword / priority mappings are defined. Everything else in the
coreconfig package resolves through this dict.

Values are mirrored from the legacy duplicated locations (do not invent):
  - model      <- src/burnless/config.py  DEFAULT_TIER_MODELS / DEFAULT_CONFIG["agents"]
  - role       <- src/burnless/config.py  DEFAULT_CONFIG["agents"][tier]["role"]
  - use_for    <- src/burnless/config.py  DEFAULT_CONFIG["agents"][tier]["use_for"]
  - keywords   <- src/burnless/config.py  DEFAULT_CONFIG["routing"][tier]
                  (the silver list already folds in routing.BUILTIN_SILVER_HINTS)
  - priority   <- derived from routing.TIER_PRIORITY = ["gold","silver","bronze"]
                  expressed as a rank (higher == routed first).

diamond is NOT present in the legacy DEFAULT_TIER_MODELS / routing tables: it is
an explicit-only escalation tier (never auto-routed). Per config.py
_normalize_legacy_tiers the real diamond is "Opus as an explicit second-opinion
tier", so its model mirrors the opus model id (same value gold/opus uses). Its
keywords are intentionally empty (reachable only via --tier diamond) and its
priority sits above gold.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TierDefinition:
    name: str
    model: str
    role: str
    use_for: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    priority: int = 0


# THE one place tiers are defined.
DEFAULT_TIERS: dict[str, TierDefinition] = {
    "bronze": TierDefinition(
        name="bronze",
        model="claude-haiku-4-5-20251001",
        role="summaries_classification",
        use_for=["summarize", "classify", "clean_logs"],
        keywords=[
            "resumir", "resumo", "summarize", "summary", "limpar", "clean",
            "classificar", "classify", "extrair", "extract", "organizar log",
            "tag",
        ],
        priority=1,
    ),
    "silver": TierDefinition(
        name="silver",
        model="claude-sonnet-4-6",
        role="documentation_structuring",
        use_for=["docs", "prd", "prompts", "specs"],
        keywords=[
            "documentação", "documentacao", "documentation", "briefing",
            "prd", "prompt", "especificação", "especificacao", "spec",
            "texto", "readme", "código", "codigo", "code", "erro", "bug",
            "debug", "terminal", "arquivo", "repo", "teste", "test",
            "build", "compilar", "compile", "stack trace", "exception",
            "compression", "simulator", "projeto", "repositorio",
            "repositório", "pasta", "diretorio", "diretório", "memoria",
            "memória", "anotacoes", "anotações",
        ],
        priority=2,
    ),
    "gold": TierDefinition(
        name="gold",
        model="claude-opus-4-8",
        role="strategy_architecture",
        use_for=["architecture", "complex_reasoning", "high_level_planning"],
        keywords=[
            "arquitetura", "architecture", "estratégia", "estrategia", "strategy",
            "decisão", "decisao", "decision", "risco", "risk", "conceito",
            "produto", "roadmap", "trade-off", "tradeoff",
        ],
        priority=3,
    ),
    "diamond": TierDefinition(
        name="diamond",
        model="claude-opus-4-8",
        role="second_opinion_escalation",
        use_for=[],
        keywords=[],
        priority=4,
    ),
}


@dataclass
class Agent:
    name: str
    role: str
    provider: str = "anthropic"
    auth: str = "subscription"
    model: str | None = None
    tools: list[str] = field(default_factory=list)
    rules: str = ""


@dataclass
class CacheMode:
    name: str
    module: str
    mechanism: str
    warm_module: str | None = None
    keepalive: bool = False
    ttl: str | None = None
    flags: list[str] = field(default_factory=list)
    headers: list[str] = field(default_factory=list)


DEFAULT_AGENTS: dict[str, Agent] = {
    "gold": Agent(
        name="gold",
        role="execute",
        provider="anthropic",
        auth="subscription",
        model="claude-opus-4-8",
    ),
    "silver": Agent(
        name="silver",
        role="execute",
        provider="anthropic",
        auth="subscription",
        model="claude-sonnet-4-6",
    ),
    "bronze": Agent(
        name="bronze",
        role="execute",
        provider="anthropic",
        auth="subscription",
        model="claude-haiku-4-5-20251001",
    ),
    "maestro": Agent(
        name="maestro",
        role="orchestrate",
        provider="anthropic",
        auth="subscription",
        tools=["delegate"],
        rules="never_execute",
    ),
}

DEFAULT_CACHE_MODES: dict[str, CacheMode] = {
    "anthropic_subscription": CacheMode(
        name="anthropic_subscription",
        module="burnless.cache_modes.anthropic_subscription",
        mechanism="cli_setting_sources",
        warm_module="burnless.warm_session",
        keepalive=True,
        ttl="1h",
        flags=["--setting-sources", "project,local", "--exclude-dynamic"],
    ),
    "anthropic_api": CacheMode(
        name="anthropic_api",
        module="burnless.cache_modes.anthropic_api",
        mechanism="sdk_cache_control",
        warm_module="burnless.warm_session",
        keepalive=True,
        ttl="1h",
        headers=["extended-cache-ttl-2025-04-11"],
    ),
    "codex_subscription": CacheMode(
        name="codex_subscription",
        module="burnless.cache_modes.codex_subscription",
        mechanism="codex_native_session",
        warm_module="burnless.warm_session_codex",
        keepalive=False,
    ),
    "codex_api": CacheMode(
        name="codex_api",
        module="burnless.cache_modes.codex_api",
        mechanism="openai_api_cache",
        warm_module=None,
        keepalive=False,
    ),
    "gemini_subscription": CacheMode(
        name="gemini_subscription",
        module="burnless.cache_modes.gemini_subscription",
        mechanism="gemini_native",
        warm_module=None,
        keepalive=False,
    ),
    "gemini_api": CacheMode(
        name="gemini_api",
        module="burnless.cache_modes.gemini_api",
        mechanism="gemini_context_cache",
        warm_module=None,
        keepalive=False,
    ),
    "none": CacheMode(
        name="none",
        module="burnless.cache_modes.none",
        mechanism="none",
    ),
}
