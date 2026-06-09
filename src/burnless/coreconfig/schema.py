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
