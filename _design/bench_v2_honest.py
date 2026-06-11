#!/usr/bin/env python3
"""
HONEST benchmark v2: burnless rollover vs Claude Code native uncapped growth.
Empirical fact (2026-06-11, session be229c3d): 162 turns, context 44,984→470,513 tokens,
ZERO compaction drops. CC does NOT auto-compact at 200k. The baseline is real uncapped growth.
Includes prompt-cache economics + realistic verbose-input compression.
"""

def benchmark():
    # Measured params (exact as per spec)
    C0 = 44984          # cold-start context tokens
    G = 2471            # per-turn growth tokens
    W = 10              # burnless rollover window
    CACHE_READ = 0.1    # cache-read weight (10x cheaper than fresh)
    VERBOSE_CHARS = 900 # realistic user turn (long paragraph)
    TELEGRAMMER = 3.0   # input compression ratio (realistic, not exaggerated)

    # Per-turn context models
    def cc_native(i):
        """CC grows uncapped: C0 + i*G. EMPIRICALLY VALIDATED: session be229c3d hit
        470,513 tokens (turn 162) with 0 compaction drops; 86 turns > 200k all climbing
        monotonically. The auto-compact assumption is REFUTED."""
        return C0 + i * G

    def burnless_roll(i):
        """Burnless resets every W=10 turns."""
        return C0 + (i % W) * G

    def no_compact_naive(i):
        """Identical to cc_native now: the 'fantasy' turns out to be REAL."""
        return C0 + i * G

    # Cost model with prompt-cache
    def cost_turn(context_i, is_reset, cache_weight=CACHE_READ):
        """
        Per-turn cost:
        - Normal turn: stable prefix at cache_weight, new delta (G tokens) at 1.0
        - Reset turn: whole context at 1.0 (cache miss penalty)
        """
        if is_reset:
            return context_i  # full fresh
        else:
            stable = context_i - G
            return stable * cache_weight + G * 1.0

    # Verbose input saving (Burnless only)
    user_input_tokens = VERBOSE_CHARS / 4  # ~225 tokens per turn
    compression_saving = user_input_tokens / TELEGRAMMER  # ~75 tokens saved

    # Run scenarios for N in [50, 100, 200, 500, 1000]
    turns_list = [50, 100, 200, 500, 1000]
    results = []

    for N in turns_list:
        # Cumulative costs
        cc_cost = 0.0
        burnless_cost = 0.0

        for i in range(N):
            # CC native (uncapped, no resets)
            ctx_cc = cc_native(i)
            cc_cost += cost_turn(ctx_cc, is_reset=False)

            # Burnless rollover (resets every W turns)
            ctx_bl = burnless_roll(i)
            is_reset_bl = (i % W == 0)
            burnless_cost += cost_turn(ctx_bl, is_reset_bl)
            burnless_cost -= compression_saving  # apply verbose compression saving

        # Unbounded naive (identical to cc_native: REAL baseline now, not fantasy)
        naive_cost = 0.0
        for i in range(N):
            ctx_naive = no_compact_naive(i)
            naive_cost += cost_turn(ctx_naive, is_reset=False)

        honest_ratio = cc_cost / burnless_cost if burnless_cost > 0 else 0
        honest_savings = (cc_cost - burnless_cost) / cc_cost * 100 if cc_cost > 0 else 0
        naive_ratio = naive_cost / burnless_cost if burnless_cost > 0 else 0

        results.append({
            'N': N,
            'cc_native_cost': cc_cost,
            'burnless_cost': burnless_cost,
            'naive_cost': naive_cost,
            'honest_ratio': honest_ratio,
            'honest_savings_pct': honest_savings,
            'naive_ratio': naive_ratio,
        })

    return results, C0, G, W, CACHE_READ, VERBOSE_CHARS, TELEGRAMMER


def main():
    results, C0, G, W, CACHE_READ, VERBOSE_CHARS, TELEGRAMMER = benchmark()

    print("=" * 100)
    print("HONEST BENCHMARK v2: burnless rollover vs Claude Code native uncapped growth")
    print("=" * 100)
    print()
    print(f"Parameters:")
    print(f"  C0={C0}, G={G}, W={W}")
    print(f"  CC native: UNCAPPED (C0 + i*G) — empirically validated at 470,513 tokens, 0 drops")
    print(f"  Burnless window={W} (resets every {W} turns)")
    print(f"  Prompt-cache: stable prefix at {CACHE_READ}x, new delta at 1.0x")
    print(f"  Verbose input: {VERBOSE_CHARS} chars/turn, compressed {TELEGRAMMER}x (Burnless only)")
    print()

    print(f"{'N':<6} {'CC Native $':<14} {'Burnless $':<14} {'Honest Ratio':<14} {'Savings %':<10}")
    print("-" * 58)

    for r in results:
        print(
            f"{r['N']:<6.0f} "
            f"{r['cc_native_cost']:<14.0f} "
            f"{r['burnless_cost']:<14.0f} "
            f"{r['honest_ratio']:<14.2f}x "
            f"{r['honest_savings_pct']:<10.1f}%"
        )

    print()
    print("HONEST HEADLINE:")
    final_honest_ratio = results[-1]['honest_ratio']  # N=1000
    print(f"  Burnless wins by {final_honest_ratio:.2f}x over Claude Code uncapped baseline.")
    print(f"  REAL baseline (not 22x fantasy v1): CC does NOT auto-compact at 200k.")
    print(f"  Empirical evidence: session be229c3d hit 470,513 tokens with ZERO drops.")
    print()


if __name__ == '__main__':
    main()
