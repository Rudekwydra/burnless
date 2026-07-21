"""Burnless pricing rates (per-MTok, Jan-2026 public rates)."""

MODEL_PRICES = {
    "opus": {
        "input": 15,
        "output": 75,
        "cache_read": 1.50,
        "cache_write": 18.75,
    },
    "sonnet": {
        "input": 3,
        "output": 15,
        "cache_read": 0.30,
        "cache_write": 3.75,
    },
    "haiku": {
        "input": 1,
        "output": 5,
        "cache_read": 0.10,
        "cache_write": 1.25,
    },
    "fable": {
        "input": 10,
        "output": 50,
        "cache_read": 1.00,
        "cache_write": 12.50,
    },
    # Local inference (ollama / gemma): $0 marginal cost.
    "gemma": {
        "input": 0,
        "output": 0,
        "cache_read": 0,
        "cache_write": 0,
    },
    # codex / gpt on subscription: marginal $ unverified → assumed sonnet-equivalent.
    "gpt": {
        "input": 3,
        "output": 15,
        "cache_read": 0.30,
        "cache_write": 3.75,
    },
    # gemini: marginal $ unverified → assumed sonnet-equivalent.
    "gemini": {
        "input": 3,
        "output": 15,
        "cache_read": 0.30,
        "cache_write": 3.75,
    },
}

BASELINE_MODEL = "opus"
CHEAP_TIER_MODEL = "haiku"

_PRICING_FAMILIES = ("haiku", "sonnet", "opus", "fable", "gemma", "gpt", "gemini")


def blended_cost(model: str) -> float:
    "Rough $/Mtok cost for ranking: input + output rate of the model's family. 0.0 if unknown/local."
    try:
        low = (model or "").lower()
        if "codex" in low:
            fam = "gpt"
        else:
            fam = next((f for f in _PRICING_FAMILIES if f in low), "")
        p = MODEL_PRICES.get(fam) or {}
        return float(p.get("input", 0) or 0) + float(p.get("output", 0) or 0)
    except Exception:
        return 0.0


def rate(model: str, kind: str) -> float:
    """Return rate in $ per token (price per-MTok ÷ 1M).

    Unknown model -> opus fallback. Clamps result >= 0. Never raises.
    """
    m = model if model in MODEL_PRICES else BASELINE_MODEL
    try:
        return max(float(MODEL_PRICES[m].get(kind, 0) or 0), 0.0) / 1_000_000
    except (TypeError, ValueError):
        return 0.0


PRICING_VERSION = "2026-01"


def rate_versioned(family: str, field: str, version: str = PRICING_VERSION) -> float:
    """Version-aware wrapper around rate(). Only the "2026-01" table exists today,
    so any version string (known or not) falls back to it. Never raises."""
    return rate(family, field)
