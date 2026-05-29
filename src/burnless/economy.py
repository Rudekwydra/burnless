"""Burnless economy report: 4-bucket real $ savings breakdown."""

from dataclasses import dataclass, field
from .pricing import rate as P


@dataclass
class Bucket:
    """Single economy bucket: tokens saved and USD equivalent."""

    name: str
    tokens: float
    usd: float
    note: str = ""


@dataclass
class EconomyReport:
    """Full economy breakdown with assumptions."""

    buckets: list[Bucket]
    total_tokens: float
    total_usd: float
    assumptions: list[str] = field(
        default_factory=lambda: [
            "baseline=opus",
            "cheap tier=haiku",
            "Jan-2026 public rates",
            "floors not ceilings",
            "worker-output not yet metered",
        ]
    )


def compute_economy(metrics: dict, cfg: dict | None = None) -> EconomyReport:
    """Compute 4-bucket economy report from metrics dict.

    Pure function: never raises, clamps all inputs >= 0.
    Formulas follow spec verbatim.
    """
    by = metrics.get("by_source", {}) or {}

    def n(k: str) -> float:
        """Clamp token source to [0, ∞)."""
        try:
            v = by.get(k, 0) or 0
            return max(float(v), 0.0)
        except (TypeError, ValueError):
            return 0.0

    # Bucket 1: Input compression (encoder)
    b1_tok = n("capsule_compression")
    b1_usd = b1_tok * P("opus", "input")

    # Bucket 2: Maestro history/cache linearization
    b2_compact = n("compact_state")
    b2_decomp = n("output_decompression_avoided")
    b2_tok = b2_compact + b2_decomp
    b2_usd = b2_compact * P("opus", "input") + b2_decomp * P("opus", "output")

    # Bucket 3: Worker tier downgrade (expensive_model_avoided = opus->haiku substitution)
    b3_tok = n("expensive_model_avoided")
    b3_usd = b3_tok * (P("opus", "input") - P("haiku", "input"))
    b3_note = "worker output not yet instrumented (v2)"

    # Bucket 4: Cache hits (repeated_context_avoided + keepalive_cache_renewed)
    b4_repeated = n("repeated_context_avoided")
    b4_keepalive = n("keepalive_cache_renewed")
    b4_tok = b4_repeated + b4_keepalive
    b4_usd = b4_tok * (P("opus", "input") - P("opus", "cache_read"))

    buckets = [
        Bucket(name="Input compression (encoder)", tokens=b1_tok, usd=b1_usd),
        Bucket(
            name="Maestro history/cache linearization",
            tokens=b2_tok,
            usd=b2_usd,
        ),
        Bucket(
            name="Worker tier downgrade",
            tokens=b3_tok,
            usd=b3_usd,
            note=b3_note,
        ),
        Bucket(
            name="Cache hits",
            tokens=b4_tok,
            usd=b4_usd,
        ),
    ]

    total_tok = sum(b.tokens for b in buckets)
    total_usd = sum(b.usd for b in buckets)

    return EconomyReport(
        buckets=buckets,
        total_tokens=total_tok,
        total_usd=total_usd,
    )
