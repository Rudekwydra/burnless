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
}

BASELINE_MODEL = "opus"
CHEAP_TIER_MODEL = "haiku"


def rate(model: str, kind: str) -> float:
    """Return rate in $ per token (price per-MTok ÷ 1M).

    Unknown model -> opus fallback. Clamps result >= 0. Never raises.
    """
    m = model if model in MODEL_PRICES else BASELINE_MODEL
    try:
        return max(float(MODEL_PRICES[m].get(kind, 0) or 0), 0.0) / 1_000_000
    except (TypeError, ValueError):
        return 0.0
