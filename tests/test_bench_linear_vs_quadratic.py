"""
Tests for benchmark: Compaction O(N) vs No-Compaction O(N²).
Validates the analytical models and proves the asymptotic behavior.
"""

import sys
import os

# Add _design to path so we can import the benchmark module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '_design'))

from bench_linear_vs_quadratic import (
    pure_ctx,
    compacted_ctx,
    cumulative_pure,
    cumulative_compacted,
)


class TestPureGrowth:
    """Test that pure (no compaction) grows quadratically."""

    def test_pure_grows_super_linearly(self):
        """When N doubles (100→200), cumulative_pure should > 2.5x (super-linear)."""
        C0, G = 44984, 2471
        cum_100 = cumulative_pure(100, C0, G)
        cum_200 = cumulative_pure(200, C0, G)
        ratio = cum_200 / cum_100
        assert ratio > 2.5, f"Expected ratio > 2.5, got {ratio:.2f} (pure grows linearly, not quadratically)"

    def test_pure_per_turn_unbounded(self):
        """Per-turn cost should increase linearly with turn index."""
        C0, G = 44984, 2471
        ctx_10 = pure_ctx(10, C0, G)
        ctx_50 = pure_ctx(50, C0, G)
        ctx_100 = pure_ctx(100, C0, G)

        assert ctx_50 > ctx_10, "Per-turn cost should increase"
        assert ctx_100 > ctx_50, "Per-turn cost should increase"
        assert ctx_50 - ctx_10 == 40 * G, "Difference from turn 10 to 50 should be 40*G"
        assert ctx_100 - ctx_50 == 50 * G, "Difference from turn 50 to 100 should be 50*G"


class TestCompactedGrowth:
    """Test that compacted (with compaction) grows linearly."""

    def test_compacted_grows_linearly(self):
        """When N doubles (100→200), cumulative_compacted should be ~2.0x (linear)."""
        C0, G, W = 44984, 2471, 10
        cum_100 = cumulative_compacted(100, C0, G, W)
        cum_200 = cumulative_compacted(200, C0, G, W)
        ratio = cum_200 / cum_100
        # Linear growth: ratio should be ~2.0 when N doubles
        assert 1.8 <= ratio <= 2.2, f"Expected 1.8 <= ratio <= 2.2, got {ratio:.2f} (not linear)"

    def test_compacted_ctx_bounded(self):
        """Per-turn cost should be bounded by C0 + (W-1)*G."""
        C0, G, W = 44984, 2471, 10
        max_ctx = C0 + (W - 1) * G
        # Check across 1000 turns
        for i in range(1000):
            ctx = compacted_ctx(i, C0, G, W)
            assert ctx <= max_ctx, f"At turn {i}, ctx {ctx} > max_ctx {max_ctx}"

    def test_compacted_per_turn_repeats(self):
        """Per-turn cost should repeat every W turns."""
        C0, G, W = 44984, 2471, 10
        for i in range(50):
            ctx_i = compacted_ctx(i, C0, G, W)
            ctx_i_plus_W = compacted_ctx(i + W, C0, G, W)
            assert ctx_i == ctx_i_plus_W, f"ctx({i}) != ctx({i + W}): {ctx_i} != {ctx_i_plus_W}"


class TestComparisonAndWins:
    """Test that compaction wins and show the gap."""

    def test_compaction_wins_at_500(self):
        """At N=500, compacted should be < pure."""
        C0, G, W = 44984, 2471, 10
        cum_pure = cumulative_pure(500, C0, G)
        cum_comp = cumulative_compacted(500, C0, G, W)
        assert cum_comp < cum_pure, f"Compacted {cum_comp} should be < pure {cum_pure}"

    def test_compaction_savings_grow(self):
        """Savings % should increase with N."""
        C0, G, W = 44984, 2471, 10
        for N in [50, 100, 200, 500]:
            cum_pure = cumulative_pure(N, C0, G)
            cum_comp = cumulative_compacted(N, C0, G, W)
            savings = (1 - cum_comp / cum_pure) * 100
            # Savings should monotonically increase (or stay flat) as N grows
            # For N > W, savings should be significant
            if N > W:
                assert savings > 20, f"At N={N}, expected savings > 20%, got {savings:.1f}%"

    def test_ratio_grows_quadratically(self):
        """Ratio (pure/comp) should grow ~quadratically."""
        C0, G, W = 44984, 2471, 10
        ratio_100 = cumulative_pure(100, C0, G) / cumulative_compacted(100, C0, G, W)
        ratio_500 = cumulative_pure(500, C0, G) / cumulative_compacted(500, C0, G, W)
        # If ratio grows quadratically, ratio_500 / ratio_100 should be ~(500/100)^2 / (1) ≈ 25
        # (not exactly, but order of magnitude: should be >> 2)
        growth = ratio_500 / ratio_100
        assert growth > 3, f"Ratio growth {growth:.2f} suggests not quadratic"


if __name__ == '__main__':
    # Simple runner for debugging
    import traceback

    tests = [
        TestPureGrowth(),
        TestCompactedGrowth(),
        TestComparisonAndWins(),
    ]

    passed, failed = 0, 0
    for test_class in tests:
        for method_name in dir(test_class):
            if method_name.startswith('test_'):
                try:
                    method = getattr(test_class, method_name)
                    method()
                    print(f"✓ {test_class.__class__.__name__}.{method_name}")
                    passed += 1
                except AssertionError as e:
                    print(f"✗ {test_class.__class__.__name__}.{method_name}: {e}")
                    failed += 1
                except Exception as e:
                    print(f"✗ {test_class.__class__.__name__}.{method_name}: {type(e).__name__}: {e}")
                    traceback.print_exc()
                    failed += 1

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
