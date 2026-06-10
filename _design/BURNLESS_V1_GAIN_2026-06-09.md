# Burnless v1 — measured gain (2026-06-09)

The consolidated v1 maestro = **never-compact + conversation-native cached** (rolling-recompact built,
validated, default OFF — see cost model below). This documents the GAIN, measured from a live full-loop
run (haiku maestro, real CLI, via partner_turn_session + MaestroSession), then the next step.

## Method
Effective input-token cost model (Anthropic monthly-plan, w=2.0 measured = ephemeral_1h):
`eff = input*1.0 + cache_creation*2.0 + cache_read*0.10`.
- **v1 (cached, conversation-native):** per turn `in + cr*2.0 + rd*0.10` — from measured cr/rd.
- **cold baseline (no cache, re-send full context each turn):** `(cr+rd)*1.0`.

Measured cr/rd per turn (12-turn verbose session): rd climbs 11k→64k, cr stays ~3k (delta-only writes).

## Result
| turn | v1 cached (eff) | cold (eff) | gain |
|---|---|---|---|
| 1 | 42,745 | 31,891 | 0.7× (write-tax, no read yet) |
| 2 | 9,764 | 35,174 | 3.6× |
| 6 | 10,937 | 48,249 | 4.4× |
| 12 | 12,439 | 67,146 | **5.4×** (and rising) |

**Cumulative over 12 turns: 3.59× cheaper (166.3k vs 597.0k eff-tokens).**

## Reading
- **Break-even at turn 2.** Turn 1 pays the cache_creation write tax (cr×2.0) with no read yet → 0.7×.
  From turn 2 the cache read (10%) dominates and the gain compounds.
- **Gain GROWS with session length:** cold re-sends the whole growing context every turn (quadratic);
  v1 reads it at 10% + writes only the delta (linear). At turn 12 it's 5.4×/turn and still climbing.
- This is the burnless thesis quantified: the value is in the conversation cache across a session, and it
  is real and growing — for the COMMON case (never-compact), no exotic machinery needed.

## Why never-compact is the v1 default (cost model, measured constants)
base~28k, delta~4500/turn, rewind re-warm~25k, w=2.0. Sweeping cycle-length L × session-length T:
- Eager rolling (default K=8 ≈ L~5) LOSES at every T (re-warm dominates).
- never-compact WINS up to ~T30–50 (carrying the cached history at 10% beats paying 25k re-warm).
- Rolling beats never-compact only past ~T50 AND only with a FAT window (L≈15); at T120, L15 ≈ 2× cheaper
  than never-compact.
→ v1 default = never-compact (optimal for normal sessions); rolling-recompact = built+validated, toggle OFF,
  opt-in for very long sessions.

## Next (Roberto's sequence: measure+document gain → implement → measure again)
1. ✅ Gain measured + documented (this file).
2. Implement: `cache_policy.rolling_compaction_enabled: false` toggle (consolidates option 1).
3. Implement IN-FORK compaction (Fable §2.3): compact as the dying fork's last turn, reading the window at
   10% instead of re-creating ~25k base on re-fork. Open mechanism Q first: does `--resume BASE
   --fork-session` re-READ base (cheap) or re-CREATE it (live showed cr~25k = re-create)? If re-read is
   achievable, the re-warm collapses and rolling wins far earlier.
4. **Measure again** after in-fork: re-run the cost model with the new re-warm constant → new crossover.
   Document the improvement (the "implement and measure again" loop).
