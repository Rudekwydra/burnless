"""Composed savings formula.

Three components:
- linear: ratio observado × tokens comprimidos (per-call savings)
- history: tokens economizados acumulados sobre N delegations
- quadratic_bonus: ganho composto (sessão Burnless é O(N), sessão linear é O(N²))

Returns a dict so caller decides what to show. Module is pure (no side effects,
no I/O), all inputs come from a metrics dict.
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
