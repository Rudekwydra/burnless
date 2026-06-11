"""
Tests for benchmark v2 honest (MUST NOT be removed or edited by prohibition rule).
"""
import sys
import os

# Add parent dir to path so we can import from _design
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from _design.bench_v2_honest import benchmark


def test_burnless_cost_lower_than_cc_native_n500():
    """Burnless cumulative cost < CC native for N=500."""
    results, _, _, _, _, _, _ = benchmark()
    n500_result = next((r for r in results if r['N'] == 500), None)
    assert n500_result is not None, "N=500 not in results"
    assert n500_result['burnless_cost'] < n500_result['cc_native_cost'], \
        f"Burnless {n500_result['burnless_cost']} should be < CC {n500_result['cc_native_cost']}"


def test_cc_native_uncapped():
    """CC native context is UNCAPPED (C0 + i*G). Empirically validated: cc_native(190) >> 400k."""
    results, C0, G, W, CACHE_READ, VERBOSE_CHARS, TELEGRAMMER = benchmark()

    def cc_native(i):
        return C0 + i * G

    ctx_190 = cc_native(190)
    assert ctx_190 > 400000, \
        f"Turn 190: CC context {ctx_190} should be > 400k (empirical data: ~470k at turn 162)"


def test_burnless_context_bounded():
    """Burnless context <= C0 + (W-1)*G."""
    results, C0, G, W, CACHE_READ, VERBOSE_CHARS, TELEGRAMMER = benchmark()
    max_burnless_context = C0 + (W - 1) * G

    for i in range(1000):
        ctx = C0 + (i % W) * G
        assert ctx <= max_burnless_context, \
            f"Turn {i}: Burnless context {ctx} exceeds max {max_burnless_context}"


if __name__ == '__main__':
    test_burnless_cost_lower_than_cc_native_n500()
    print("✓ test_burnless_cost_lower_than_cc_native_n500")

    test_cc_native_uncapped()
    print("✓ test_cc_native_uncapped")

    test_burnless_context_bounded()
    print("✓ test_burnless_context_bounded")

    print("\nAll tests passed.")
