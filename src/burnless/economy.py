"""Burnless economy report: 4-bucket real $ savings breakdown,
plus the per-turn counterfactual snapshot for `burnless chat` (v1 footer).

Pure module: no LLM, no subprocess, no I/O — offline-testable."""

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
    accounted_total: int = 0
    monetizable_subtotal: int = 0
    excluded_categories: list = field(default_factory=list)


def _buckets_from_by_source(by: dict) -> list[Bucket]:
    """Same 4-bucket formulas compute_economy has always used, factored out so
    compute_economy_snapshot can reuse them against a LedgerSnapshot's by_source."""
    def n(k: str) -> float:
        try:
            v = by.get(k, 0) or 0
            return max(float(v), 0.0)
        except (TypeError, ValueError):
            return 0.0

    b1_tok = n("capsule_compression")
    b1_usd = b1_tok * P("opus", "input")

    b2_compact = n("compact_state")
    b2_decomp = n("output_decompression_avoided")
    b2_tok = b2_compact + b2_decomp
    b2_usd = b2_compact * P("opus", "input") + b2_decomp * P("opus", "output")

    b3_tok = n("expensive_model_avoided")
    b3_usd = b3_tok * (P("opus", "input") - P("haiku", "input"))
    b3_note = "worker output not yet instrumented (v2)"

    b4_repeated = n("repeated_context_avoided")
    b4_keepalive = n("keepalive_cache_renewed")
    b4_tok = b4_repeated + b4_keepalive
    b4_usd = b4_tok * (P("opus", "input") - P("opus", "cache_read"))

    return [
        Bucket(name="Input compression (encoder)", tokens=b1_tok, usd=b1_usd),
        Bucket(name="Maestro history/cache linearization", tokens=b2_tok, usd=b2_usd),
        Bucket(name="Worker tier downgrade", tokens=b3_tok, usd=b3_usd, note=b3_note),
        Bucket(name="Cache hits", tokens=b4_tok, usd=b4_usd),
    ]


def compute_economy(metrics: dict, cfg: dict | None = None) -> EconomyReport:
    """Compute 4-bucket economy report from metrics dict.

    Pure function: never raises, clamps all inputs >= 0.
    Formulas follow spec verbatim.
    """
    by = metrics.get("by_source", {}) or {}
    buckets = _buckets_from_by_source(by)
    total_tok = sum(b.tokens for b in buckets)
    total_usd = sum(b.usd for b in buckets)
    return EconomyReport(buckets=buckets, total_tokens=total_tok, total_usd=total_usd)


def compute_economy_snapshot(snapshot, cfg: dict | None = None) -> EconomyReport:
    """Compute the same 4-bucket economy report from a ledger_projector.LedgerSnapshot,
    plus the reconciliation numbers the design doc's 'raw_logs_isolated na UI' section requires:
    accounted_total (all saving categories), monetizable_subtotal (accounted minus excluded),
    and excluded_categories (nominal list of what was excluded and why)."""
    by = dict(snapshot.by_source or {})
    buckets = _buckets_from_by_source(by)
    total_tok = sum(b.tokens for b in buckets)
    total_usd = sum(b.usd for b in buckets)
    return EconomyReport(
        buckets=buckets,
        total_tokens=total_tok,
        total_usd=total_usd,
        accounted_total=int(snapshot.accounted_total_tokens),
        monetizable_subtotal=int(snapshot.monetizable_tokens),
        excluded_categories=list(snapshot.excluded_categories or []),
    )


# ---------------------------------------------------------------------------
# Counterfactual economy snapshot (`burnless chat` footer)
# ---------------------------------------------------------------------------

# Cache multipliers vs input rate: ephemeral_1h prompt cache reads at 0.10×,
# writes at 2.0× (FABLE_COSTMODEL_2026-06-09.md).
CACHE_READ_MULT = 0.10
CACHE_WRITE_MULT = 2.0

# Counterfactual solo agent: k=6 agentic calls per turn, each re-reading the
# FULL accumulated conversation at cache_read rate. Measured agentic turns run
# k≈3–15; k=6 is the conservative working value adopted by the corrected cost
# model (FABLE_COSTMODEL_2026-06-09.md §2 Error 2 / §3).
SOLO_K_CALLS_PER_TURN = 6
SOLO_MODEL = "sonnet"

_FAMILIES = ("haiku", "sonnet", "opus", "fable", "gemma", "gpt", "gemini")


def model_family(model_id: str | None) -> str:
    """Map a full model id (e.g. claude-haiku-4-5-20251001) to a pricing
    family. Unknown/missing -> sonnet (the chat-footer default)."""
    low = (model_id or "").lower()
    if "codex" in low:
        return "gpt"
    for fam in _FAMILIES:
        if fam in low:
            return fam
    return SOLO_MODEL


@dataclass
class EconomySnapshot:
    actual_usd: float = 0.0
    solo_usd: float = 0.0
    ratio: float = 0.0
    saved_usd: float = 0.0


def _tok(usage: dict, key: str) -> float:
    try:
        return max(float(usage.get(key, 0) or 0), 0.0)
    except (TypeError, ValueError):
        return 0.0


def _usage_cost_usd(usage: dict, default_model: str) -> float:
    fam = model_family(usage.get("model") or default_model)
    rate_in = P(fam, "input")
    rate_out = P(fam, "output")
    return (
        _tok(usage, "input_tokens") * rate_in
        + _tok(usage, "cache_read_input_tokens") * CACHE_READ_MULT * rate_in
        + _tok(usage, "cache_creation_input_tokens") * CACHE_WRITE_MULT * rate_in
        + _tok(usage, "output_tokens") * rate_out
    )


def economy_snapshot(
    turn_usages: list[dict],
    conv_tokens_cumulative: int,
    maestro_model: str,
    worker_usages: list[dict],
) -> EconomySnapshot:
    """Counterfactual economy for the chat session so far.

    actual_usd: every burnless call (maestro turns + workers) priced at its
    own model's rates — fresh input at 1×, cache_read at 0.10×, cache_creation
    at 2.0×, output at out-rate.

    solo_usd (ESTIMATED counterfactual): what a single sonnet context would
    have paid to do the same work — k=6 agentic calls/turn each cache_read the
    full accumulated conversation (conv_tokens_cumulative), the turn's deposit
    (the same work output) is cache_written at 2.0×, and the same output is
    produced at sonnet out-rate. k=6 per FABLE_COSTMODEL_2026-06-09.md.
    """
    turn_usages = [u for u in (turn_usages or []) if isinstance(u, dict)]
    worker_usages = [u for u in (worker_usages or []) if isinstance(u, dict)]
    actual = sum(_usage_cost_usd(u, maestro_model) for u in turn_usages)
    actual += sum(_usage_cost_usd(u, SOLO_MODEL) for u in worker_usages)

    try:
        conv = max(float(conv_tokens_cumulative or 0), 0.0)
    except (TypeError, ValueError):
        conv = 0.0
    out_total = sum(_tok(u, "output_tokens") for u in turn_usages + worker_usages)
    s_in = P(SOLO_MODEL, "input")
    s_out = P(SOLO_MODEL, "output")
    solo = (
        SOLO_K_CALLS_PER_TURN * CACHE_READ_MULT * conv * s_in
        + CACHE_WRITE_MULT * out_total * s_in
        + out_total * s_out
    )
    ratio = (solo / actual) if actual > 0 else 0.0
    return EconomySnapshot(
        actual_usd=actual,
        solo_usd=solo,
        ratio=ratio,
        saved_usd=solo - actual,
    )


def render_footer(cum: EconomySnapshot) -> str:
    """One-line economy footer for the chat REPL."""
    return (
        f"💰 solo ~${cum.solo_usd:.2f} · burnless ${cum.actual_usd:.2f} · "
        f"⇣{cum.ratio:.1f}× (−${cum.saved_usd:.2f})  [solo=estimado]"
    )
