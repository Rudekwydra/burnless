from burnless.cache_policy import should_compact


def test_compaction_waits_until_break_even():
    decision = should_compact(
        old_tokens=2000,
        compacted_tokens=600,
        expected_future_turns=8,
        cache_read_ratio=0.10,
        cache_write_ratio=2.0,
    )

    assert not decision.should_compact
    assert decision.break_even_turns > 8


def test_compaction_runs_when_future_cache_reads_pay_for_write():
    decision = should_compact(
        old_tokens=2000,
        compacted_tokens=600,
        expected_future_turns=10,
        cache_read_ratio=0.10,
        cache_write_ratio=2.0,
    )

    assert decision.should_compact
    assert decision.expected_savings_tokens > 0


def test_min_hot_tail_threshold_blocks_small_tails():
    decision = should_compact(
        old_tokens=1000,
        compacted_tokens=300,
        expected_future_turns=20,
        min_hot_tail_tokens=1500,
    )

    assert not decision.should_compact
    assert "below threshold" in decision.reason
