#!/usr/bin/env python3
"""
Benchmark: Compaction O(N) vs No-Compaction O(N²).
Grounded in REAL measured params from session be229c3d (123 turns).
"""

def pure_ctx(i, C0=44984, G=2471):
    """Context without compaction: unbounded growth."""
    return C0 + i * G


def compacted_ctx(i, C0=44984, G=2471, W=10):
    """Context with compaction: resets every W turns."""
    return C0 + (i % W) * G


def cumulative_pure(N, C0=44984, G=2471):
    """Sum of per-turn context costs, no compaction."""
    total = 0
    for i in range(N):
        total += pure_ctx(i, C0, G)
    return total


def cumulative_compacted(N, C0=44984, G=2471, W=10):
    """Sum of per-turn context costs, with compaction."""
    total = 0
    for i in range(N):
        total += compacted_ctx(i, C0, G, W)
    return total


def format_table(C0=44984, G=2471, W=10):
    """Generate benchmark table."""
    results = []
    for N in [10, 25, 50, 100, 200, 500, 1000]:
        pure_last = pure_ctx(N - 1, C0, G)
        comp_last = compacted_ctx(N - 1, C0, G, W)
        cum_pure = cumulative_pure(N, C0, G)
        cum_comp = cumulative_compacted(N, C0, G, W)
        ratio = cum_pure / cum_comp
        savings = (1 - cum_comp / cum_pure) * 100

        results.append({
            'N': N,
            'pure_last': pure_last,
            'comp_last': comp_last,
            'cum_pure': cum_pure,
            'cum_comp': cum_comp,
            'ratio': ratio,
            'savings': savings
        })

    return results


def ascii_chart(results):
    """Generate ASCII chart of cumulative cost."""
    max_cum = max(r['cum_pure'] for r in results)
    cols = 60

    lines = []
    lines.append("Cumulative token cost: pure (P) vs compacted (C)")
    lines.append("")

    for r in results:
        N = r['N']
        pure_bar = int(r['cum_pure'] / max_cum * cols)
        comp_bar = int(r['cum_comp'] / max_cum * cols)

        pure_line = "█" * pure_bar
        comp_line = "█" * comp_bar

        lines.append(f"N={N:4d} P: {pure_line}")
        lines.append(f"       C: {comp_line}")
        lines.append("")

    return "\n".join(lines)


def main(C0=44984, G=2471, W=10, K=492):
    """Run benchmark and print results."""
    print("=" * 90)
    print("BENCHMARK: Compaction O(N) vs No-Compaction O(N²)")
    print("=" * 90)
    print()
    print("REAL measured params from session be229c3d (123 turns):")
    print(f"  C0 (cold-start context): {C0} tokens")
    print(f"  G  (per-turn growth):    {G} tokens/turn")
    print(f"  W  (compaction window):  {W} turns")
    print(f"  K  (rolling capsule):    {K} tokens (negligible)")
    print()
    print("Models:")
    print(f"  pure_ctx(i)      = C0 + i*G                    [unbounded growth]")
    print(f"  compacted_ctx(i) = C0 + (i % W)*G              [bounded, resets every W turns]")
    print()

    results = format_table(C0, G, W)

    print("BENCHMARK TABLE")
    print("-" * 130)
    print(f"{'N':>5} | {'Pure/Turn':>13} | {'Comp/Turn':>13} | {'Cumul Pure':>15} | {'Cumul Comp':>15} | {'Ratio':>9} | {'Savings':>9}")
    print("-" * 130)

    for r in results:
        print(
            f"{r['N']:5d} | {r['pure_last']:13d} | {r['comp_last']:13d} | "
            f"{r['cum_pure']:15d} | {r['cum_comp']:15d} | {r['ratio']:9.2f}x | {r['savings']:8.1f}%"
        )

    print("-" * 130)
    print()

    # ASCII chart
    print(ascii_chart(results))

    print("BIG-O PROOF SKETCH")
    print("-" * 90)
    print("Pure (unbounded):")
    print("  cumulative_pure(N) = Σ(C0 + i*G) for i=0..N-1 = C0*N + G*N(N-1)/2 = Θ(N²)")
    print()
    print("Compacted (bounded):")
    print("  cumulative_compacted(N) = Σ(C0 + (i%W)*G) for i=0..N-1 ≤ N*(C0+(W-1)*G) = Θ(N)")
    print()

    print("CROSSOVER & BREAK-EVEN")
    print("-" * 90)
    print(f"For N > W={W}: compaction always wins; gap grows as ~N²")
    cum_pure_1k = cumulative_pure(1000, C0, G)
    cum_comp_1k = cumulative_compacted(1000, C0, G, W)
    print(f"At N=1000:")
    print(f"  Pure cumulative:      {cum_pure_1k:,} tokens")
    print(f"  Compacted cumulative: {cum_comp_1k:,} tokens")
    print(f"  Savings:              {(1 - cum_comp_1k / cum_pure_1k) * 100:.1f}%")
    print()

    print("METHODOLOGY / HONESTY")
    print("-" * 90)
    print("This benchmark is an ANALYTICAL PROJECTION grounded in REAL per-turn token")
    print("measurements from session be229c3d (123 turns). NOT a live LLM A/B test.")
    print()
    print("Per-turn cost ≈ context size (prompt-cache). Absolute $ depends on model pricing.")
    print("The O(N) vs O(N²) SHAPE is structural: compaction converts quadratic to linear.")
    print()
    print("Next step: throttled end-to-end LLM validation.")
    print("=" * 90)


if __name__ == '__main__':
    main()
