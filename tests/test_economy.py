"""Tests for economy module (4-bucket savings breakdown + chat counterfactual snapshot)."""

import pytest
from burnless.economy import (
    EconomySnapshot,
    compute_economy,
    economy_snapshot,
    render_footer,
)


def test_capsule_compression_bucket():
    """1M tokens capsule_compression priced at opus input = $15."""
    r = compute_economy({"by_source": {"capsule_compression": 1_000_000}})
    b = [x for x in r.buckets if "compression" in x.name.lower()][0]
    assert abs(b.usd - 15.0) < 1e-6, f"expected 15.0, got {b.usd}"


def test_output_decompression_bucket():
    """1M tokens output_decompression_avoided priced at opus output = $75."""
    r = compute_economy({"by_source": {"output_decompression_avoided": 1_000_000}})
    assert abs(r.total_usd - 75.0) < 1e-6, f"expected 75.0, got {r.total_usd}"


def test_cache_hits_bucket():
    """1M tokens repeated_context_avoided priced at (opus input - opus cache_read) = $13.50."""
    r = compute_economy({"by_source": {"repeated_context_avoided": 1_000_000}})
    assert abs(r.total_usd - (15 - 1.50)) < 1e-6, f"expected 13.5, got {r.total_usd}"


def test_empty_metrics():
    """Empty metrics → all buckets 0, total 0."""
    r = compute_economy({})
    assert r.total_tokens == 0
    assert r.total_usd == 0
    for b in r.buckets:
        assert b.tokens == 0
        assert b.usd == 0


def test_garbage_input_never_raises():
    """compute_economy never raises on garbage (None, str values)."""
    # Should not raise
    r = compute_economy({"by_source": {"capsule_compression": None, "compact_state": "x"}})
    assert r.total_usd == 0
    assert r.total_tokens == 0


# ---------------------------------------------------------------------------
# economy_snapshot — chat counterfactual footer (offline, pure)
# ---------------------------------------------------------------------------


def test_snapshot_actual_usd_exact_haiku():
    """actual_usd = in·rate + cache_read·0.10·rate + cache_creation·2.0·rate + out·out_rate."""
    mu = [{
        "model": "claude-haiku-4-5-20251001",
        "input_tokens": 100,
        "cache_read_input_tokens": 10_000,
        "cache_creation_input_tokens": 2_000,
        "output_tokens": 1_000,
    }]
    s = economy_snapshot(mu, 0, "claude-haiku-4-5-20251001", [])
    # haiku 1/5 $/Mtok: (100·1 + 10000·0.10·1 + 2000·2.0·1 + 1000·5) / 1e6
    expected = (100 + 1_000 + 4_000 + 5_000) / 1e6
    assert abs(s.actual_usd - expected) < 1e-12, s.actual_usd


def test_snapshot_solo_usd_exact_formula():
    """solo = k·0.10·conv·sonnet_in + 2.0·out_total·sonnet_in + out_total·sonnet_out (k=6)."""
    mu = [{"model": "claude-haiku-4-5", "output_tokens": 200}]
    wu = [{"model": "claude-haiku-4-5", "output_tokens": 1_800}]
    conv = 50_000
    s = economy_snapshot(mu, conv, "claude-haiku-4-5", wu)
    out_total = 2_000
    expected = (
        6 * 0.10 * conv * (3 / 1e6)
        + 2.0 * out_total * (3 / 1e6)
        + out_total * (15 / 1e6)
    )
    assert abs(s.solo_usd - expected) < 1e-12, s.solo_usd
    assert abs(s.saved_usd - (s.solo_usd - s.actual_usd)) < 1e-12


def test_snapshot_multiturn_solo_beats_actual():
    """Large cumulative conversation -> solo_usd > actual_usd, ratio > 1."""
    mu = [
        {"model": "claude-sonnet-4-6", "input_tokens": 50,
         "cache_read_input_tokens": 8_000, "cache_creation_input_tokens": 500,
         "output_tokens": 300}
        for _ in range(5)
    ]
    wu = [
        {"model": "claude-haiku-4-5", "input_tokens": 400,
         "cache_read_input_tokens": 22_000, "cache_creation_input_tokens": 0,
         "output_tokens": 1_500}
        for _ in range(5)
    ]
    s = economy_snapshot(mu, 200_000, "claude-sonnet-4-6", wu)
    assert s.solo_usd > s.actual_usd
    assert s.ratio > 1
    assert s.saved_usd > 0


def test_snapshot_haiku_worker_vs_sonnet_solo_ratio_gt_1():
    """The flagship counterfactual: haiku maestro+worker vs sonnet solo."""
    mu = [{"model": "claude-haiku-4-5", "input_tokens": 10,
           "cache_read_input_tokens": 20_000, "cache_creation_input_tokens": 1_000,
           "output_tokens": 200}]
    wu = [{"model": "claude-haiku-4-5", "input_tokens": 600,
           "cache_read_input_tokens": 22_000, "cache_creation_input_tokens": 0,
           "output_tokens": 2_000}]
    s = economy_snapshot(mu, 40_000, "claude-haiku-4-5", wu)
    assert s.solo_usd > s.actual_usd, (s.actual_usd, s.solo_usd)
    assert s.ratio > 1


def test_snapshot_div0_guard_and_garbage():
    """Empty usages -> actual 0, ratio 0 (no div-by-zero); garbage never raises."""
    s = economy_snapshot([], 0, "claude-sonnet-4-6", [])
    assert s.actual_usd == 0 and s.ratio == 0
    s2 = economy_snapshot(
        [{"model": None, "input_tokens": "x", "output_tokens": None}],
        None, "whatever", [{}],
    )
    assert s2.actual_usd >= 0


def test_render_footer_non_empty_with_both_dollars():
    snap = EconomySnapshot(actual_usd=0.0178, solo_usd=0.1182,
                           ratio=6.64, saved_usd=0.1004)
    footer = render_footer(snap)
    assert footer
    assert footer.count("$") >= 2
    assert "0.12" in footer and "0.02" in footer
    assert "estimado" in footer
