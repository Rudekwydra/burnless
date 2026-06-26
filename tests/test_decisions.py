import dataclasses

from burnless.decisions import CompactionDecision, decide_compaction


def test_pays_off_case_compacts_with_positive_savings():
    decision = decide_compaction(
        old_tokens=2000,
        compacted_tokens_est=600,
        expected_future_turns=10,
        cache_read_ratio=0.10,
        cache_write_ratio=2.0,
    )

    assert decision.should_compact
    assert decision.expected_savings_tokens > 0


def test_below_break_even_does_not_compact():
    decision = decide_compaction(
        old_tokens=2000,
        compacted_tokens_est=600,
        expected_future_turns=8,
        cache_read_ratio=0.10,
        cache_write_ratio=2.0,
    )

    assert not decision.should_compact


def test_dataclass_has_exactly_16_fields():
    assert len(dataclasses.fields(CompactionDecision)) == 16


def test_cache_ratios_echoed_back_unchanged():
    decision = decide_compaction(
        old_tokens=2000,
        compacted_tokens_est=600,
        expected_future_turns=10,
        cache_read_ratio=0.10,
        cache_write_ratio=2.0,
    )

    assert decision.cache_read_ratio == 0.10
    assert decision.cache_write_ratio == 2.0


def test_default_plain_retention_yields_no_loss_risk():
    decision = decide_compaction(
        old_tokens=2000,
        compacted_tokens_est=600,
        expected_future_turns=10,
    )

    assert decision.retention_policy == "plain"
    assert decision.loss_risk == "none"
