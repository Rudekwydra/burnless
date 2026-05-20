"""Savings formulas.

`compute` is the legacy composed formula kept for compatibility. `compute_free`
is the user-facing Free breakdown: text compression, Maestro history/cache,
worker one-shot isolation, and tier routing.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class Savings:
    linear: float
    history: float
    quadratic_bonus: float
    total: float
    samples: int  # how many ratio observations contributed


@dataclass
class FreeSavings:
    input_compression: float
    maestro_history: float
    worker_oneshot: float
    tier_routing: float
    other: float
    total: float
    samples: int


def compute(metrics: dict) -> Savings:
    """Compute composed savings from a metrics dict.

    Conservative defaults: when no observations exist, returns all zeros.
    Never raises — clamps bad inputs to zero.
    """
    ratio_sum = float(metrics.get("compression_ratio_observed_sum", 0.0) or 0.0)
    ratio_count = int(metrics.get("compression_ratio_observed_count", 0) or 0)
    burnless_tokens = float(metrics.get("burnless_tokens", 0) or 0)
    delegations = int(metrics.get("delegation_counter", 0) or 0)

    if ratio_count <= 0:
        return Savings(0.0, 0.0, 0.0, 0.0, 0)

    avg_ratio = max(1.0, ratio_sum / ratio_count)

    # Linear: tokens saved per call × number of calls
    # Assumes burnless_tokens already reflects observed compression somehow;
    # treat it as the upper-bound "tokens not paid".
    linear = burnless_tokens

    # History: bonus for cumulative delegations (each later call benefits from
    # warmer cache + prior context). Conservative coefficient: 0.1 × delegations
    # × avg per-call savings.
    avg_per_call = (linear / ratio_count) if ratio_count > 0 else 0.0
    history = 0.1 * delegations * avg_per_call

    # Quadratic bonus: a linear session of N turns has O(N²) input growth
    # (every turn replays history). Burnless capsules keep it O(N). So the
    # gap grows as turn count². Use delegations as N proxy, scale by avg_ratio.
    quadratic_bonus = (delegations ** 2) * avg_per_call * 0.01 * (avg_ratio - 1.0)

    total = linear + history + max(0.0, quadratic_bonus)
    return Savings(
        linear=round(linear, 2),
        history=round(history, 2),
        quadratic_bonus=round(quadratic_bonus, 2),
        total=round(total, 2),
        samples=ratio_count,
    )


def compute_free(metrics: dict) -> FreeSavings:
    """Break down Free savings by product mechanism.

    The values are token-equivalent floors already accumulated by metrics.py.
    This function only groups them into terms a user can understand.
    """
    by_source = metrics.get("by_source", {}) if isinstance(metrics.get("by_source"), dict) else {}

    def _num(key: str) -> float:
        try:
            return max(float(by_source.get(key, 0) or 0), 0.0)
        except (TypeError, ValueError):
            return 0.0

    input_compression = _num("capsule_compression")
    maestro_history = _num("repeated_context_avoided") + _num("compact_state") + _num("keepalive_cache_renewed")
    worker_oneshot = _num("raw_logs_isolated")
    tier_routing = _num("expensive_model_avoided")
    grouped = input_compression + maestro_history + worker_oneshot + tier_routing
    try:
        total_recorded = max(float(metrics.get("burnless_tokens", 0) or 0), 0.0)
    except (TypeError, ValueError):
        total_recorded = grouped
    other = max(total_recorded - grouped, 0.0)
    samples = int(metrics.get("compression_ratio_observed_count", 0) or 0)
    total = input_compression + maestro_history + worker_oneshot + tier_routing + other
    return FreeSavings(
        input_compression=round(input_compression, 2),
        maestro_history=round(maestro_history, 2),
        worker_oneshot=round(worker_oneshot, 2),
        tier_routing=round(tier_routing, 2),
        other=round(other, 2),
        total=round(total, 2),
        samples=samples,
    )
