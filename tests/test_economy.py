"""Tests for economy module (4-bucket savings breakdown)."""

import pytest
from burnless.economy import compute_economy


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
