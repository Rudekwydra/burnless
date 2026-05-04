from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CompactionDecision:
    should_compact: bool
    break_even_turns: float
    expected_savings_tokens: float
    reason: str


def should_compact(
    *,
    old_tokens: int,
    compacted_tokens: int,
    expected_future_turns: int,
    cache_read_ratio: float = 0.10,
    cache_write_ratio: float = 2.0,
    compaction_cost_tokens: int = 0,
    min_hot_tail_tokens: int = 0,
) -> CompactionDecision:
    """Return whether creating a new frozen capsule block pays back.

    The policy compares the future cache-read savings from shrinking a hot
    capsule tail against the cache-write cost of introducing the new compacted
    block plus the one-time compaction cost:

        K * r * (B - S) > W * S + M

    B = old_tokens, S = compacted_tokens, K = expected future turns,
    r = cache_read/fresh input price, W = cache_write/fresh input price.
    """
    if old_tokens <= 0:
        return CompactionDecision(False, float("inf"), 0.0, "no hot-tail tokens")
    if old_tokens < min_hot_tail_tokens:
        return CompactionDecision(
            False,
            float("inf"),
            0.0,
            f"hot tail below threshold ({old_tokens} < {min_hot_tail_tokens})",
        )
    if compacted_tokens <= 0:
        compacted_tokens = 1
    saved_per_turn = cache_read_ratio * max(old_tokens - compacted_tokens, 0)
    upfront_cost = cache_write_ratio * compacted_tokens + compaction_cost_tokens
    if saved_per_turn <= 0:
        return CompactionDecision(False, float("inf"), -upfront_cost, "no token reduction")
    break_even = upfront_cost / saved_per_turn
    expected_savings = expected_future_turns * saved_per_turn - upfront_cost
    return CompactionDecision(
        should_compact=expected_savings > 0,
        break_even_turns=round(break_even, 2),
        expected_savings_tokens=round(expected_savings, 2),
        reason=(
            f"K={expected_future_turns}, break_even={break_even:.2f}, "
            f"expected_savings={expected_savings:.2f} input-token-equivalent"
        ),
    )


def estimate_compacted_tokens(old_tokens: int, ratio: float) -> int:
    ratio = min(max(ratio, 0.01), 0.99)
    return max(1, int(old_tokens * ratio))
