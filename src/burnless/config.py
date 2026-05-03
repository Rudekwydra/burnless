from __future__ import annotations
from pathlib import Path
import yaml

DEFAULT_CONFIG: dict = {
    "project_name": "Project",
    "language": "pt-BR",
    "mode": "local_first",
    "agents": {
        # Tiers are abstract roles, not models. Map any provider/CLI here.
        "diamond": {
            "name": "codex",
            "command": "codex exec --skip-git-repo-check --sandbox workspace-write",
            "role": "code_debug_execution",
            "use_for": ["code", "debug", "tests", "repo_changes"],
            # Declarative overrides (also via env: BURNLESS_SANDBOX,
            # BURNLESS_WORKSPACE_ROOT, BURNLESS_ALLOW_NET=1):
            #   sandbox: read-only | workspace-write | danger-full-access
            #   allow_net: true   -> uses codex --full-auto (network ON)
            #   workspace_root: /abs/path  -> codex --cd <path> (cross-project)
        },
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
        "diamond": [
            "código", "codigo", "code", "erro", "bug", "debug", "terminal",
            "arquivo", "repo", "teste", "test", "build", "compilar", "compile",
            "stack trace", "exception", "compression", "simulator",
        ],
        "gold": [
            "arquitetura", "architecture", "estratégia", "estrategia", "strategy",
            "decisão", "decisao", "decision", "risco", "risk", "conceito",
            "produto", "roadmap", "trade-off", "tradeoff",
        ],
        "silver": [
            "documentação", "documentacao", "documentation", "briefing",
            "prd", "prompt", "especificação", "especificacao", "spec",
            "texto", "readme",
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
        # one of: safe | balanced | aggressive
        # also acts as a tier dial during delegate:
        #   safe       -> may promote bronze→silver on default match
        #   balanced   -> use natural routing as-is
        #   aggressive -> demote one tier (gold→silver, silver→bronze)
        "mode": "balanced",
    },
}


def load(path: Path) -> dict:
    if not path.exists():
        return DEFAULT_CONFIG
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return _deep_merge(DEFAULT_CONFIG, data)


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
