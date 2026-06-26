from __future__ import annotations

from dataclasses import dataclass

from burnless import cache_policy


@dataclass(frozen=True)
class CompactionDecision:
    should_compact: bool
    reason: str
    scope: dict
    old_tokens: int
    compacted_tokens_est: int
    expected_future_turns: int
    break_even_turns: float
    expected_savings_tokens: float
    cache_read_ratio: float
    cache_write_ratio: float
    compaction_cost_tokens: int
    min_hot_tail_tokens: int
    cache_bust_risk: str        # none|low|medium|high
    loss_risk: str              # none|low|medium|high
    retention_policy: str       # plain|none|encrypted
    source: str                 # epoch|capsule|cmd|maestro|hook


def decide_compaction(
    *,
    old_tokens: int,
    compacted_tokens_est: int,
    expected_future_turns: int,
    scope: dict | None = None,
    cache_read_ratio: float = 0.10,
    cache_write_ratio: float = 2.0,
    compaction_cost_tokens: int = 0,
    min_hot_tail_tokens: int = 0,
    retention_policy: str = "plain",
    source: str = "cmd",
) -> CompactionDecision:
    """Rich compaction verdict built on top of cache_policy.should_compact().

    The core economic math (break-even, expected savings, should_compact) is
    delegated to burnless.cache_policy; this layer adds risk/provenance fields.
    """
    core = cache_policy.should_compact(
        old_tokens=old_tokens,
        compacted_tokens=compacted_tokens_est,
        expected_future_turns=expected_future_turns,
        cache_read_ratio=cache_read_ratio,
        cache_write_ratio=cache_write_ratio,
        compaction_cost_tokens=compaction_cost_tokens,
        min_hot_tail_tokens=min_hot_tail_tokens,
    )

    # cache_bust_risk: no compaction => no cache bust. A deep shrink (<= half the
    # old tail) reuses most of the warm prefix => low; a shallow shrink rewrites
    # a larger fresh block => medium.
    if not core.should_compact:
        cache_bust_risk = "none"
    elif compacted_tokens_est <= old_tokens * 0.5:
        cache_bust_risk = "low"
    else:
        cache_bust_risk = "medium"

    # loss_risk: plain retention keeps the original recoverable => none;
    # any non-plain policy (none|encrypted) drops/obscures it => low.
    loss_risk = "none" if retention_policy == "plain" else "low"

    return CompactionDecision(
        should_compact=core.should_compact,
        reason=core.reason,
        scope={} if scope is None else scope,
        old_tokens=old_tokens,
        compacted_tokens_est=compacted_tokens_est,
        expected_future_turns=expected_future_turns,
        break_even_turns=core.break_even_turns,
        expected_savings_tokens=core.expected_savings_tokens,
        cache_read_ratio=cache_read_ratio,
        cache_write_ratio=cache_write_ratio,
        compaction_cost_tokens=compaction_cost_tokens,
        min_hot_tail_tokens=min_hot_tail_tokens,
        cache_bust_risk=cache_bust_risk,
        loss_risk=loss_risk,
        retention_policy=retention_policy,
        source=source,
    )
