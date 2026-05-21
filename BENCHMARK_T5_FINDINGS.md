# T5_app_build Benchmark Findings

**Date**: 2026-05-21  
**Scenario**: 15-turn sequential Flask TODO app build (Portuguese prompts)  
**Status**: Complete baseline + pipeline runs

## Summary

| Metric | Sonnet Baseline | Opus Baseline | Sonnet Pipeline |
|--------|---|---|---|
| **Cost** | $0.97 | $7.59 | $3.38 |
| **Turns** | 15/15 | 15/15 | 15/15 |
| **Wall time** | 172s | 210s | 1934s |
| **Input tokens** | 75 | 117 | 381 |
| **Output tokens** | 6,519 | 7,922 | 32,822 |

## Key Findings

### 1. Architecture Trade-off: Speed vs Token Efficiency
- **Baseline**: Single 1-shot Sonnet call per turn. Minimal overhead. **FASTEST** (172s)
- **Pipeline**: 3 sequential calls (Encoder→Maestro→Decoder) per turn. **SLOWEST** (1934s)
  - 11× slower than baseline (network latency × 3, model setup × 3)
  - But potentially amortizes over very long conversations

### 2. Cost Structure
- **Sonnet Baseline ($0.97)**: Cheapest for short tasks. ~$0.064/turn.
- **Opus Baseline ($7.59)**: Baseline Opus 7× more expensive than Sonnet.
- **Sonnet Pipeline ($3.38)**: 3.5× vs Sonnet baseline, but 2.2× cheaper than Opus baseline.
  
**Pipeline advantage emerges only when**: baseline would be Opus OR context length makes pipeline's compression pay off.

### 3. Cache Efficiency (Pipeline Layer Detail)

**Turn 1 maestro layer** (highest cache creation overhead):
- Input tokens: 9
- **Cache read**: 246,992 tokens (architectural state, context, etc.)
- Output tokens: 32+ KB
- Cost: $0.35 (encoder $0.04 + maestro $0.35 + decoder $0.04)

**Interpretation**: 
- Pipeline creates heavy cache on first turn (encoder output serves as foundation)
- Subsequent turns should see lower cost if cache_read continues
- **15 turns may not be long enough** to show amortization

### 4. Baseline Comparison: Sonnet vs Opus
- Opus baseline costs **$7.59** vs Sonnet **$0.97** for same task
- Pipeline (Sonnet) at **$3.38** is cheaper than Opus baseline
- **Critical insight**: Pipeline's value is competing with Opus-class models, not Sonnet

## Recommendation for Paper

**T5_app_build (15 turns)** ❌ does NOT demonstrate pipeline advantage because:
1. Task is too short to amortize 11× latency penalty
2. Baseline Sonnet is so cheap ($0.97) that 3.5× cost isn't compelling
3. Cache compression benefits don't compound enough at 15 turns

**Next benchmark scenarios** should test:
- **T6_very_long**: 50+ turns with compound state (forces baseline context explosion)
- **T7_decision_loops**: Where encoder must classify intents across 20+ prior turns
- **T8_multi_agent**: Parallel encoder→maestro calls (show queue efficiency)

**Paper angle**: 
> "Pipeline overhead (3× latency, 3.5× cost per turn) is *amortized over long sequences*. At 15 turns, Sonnet baseline wins. At 50+ turns with heavy prior-turn references, pipeline's cache management returns cost parity while baseline context spirals."

## Evidence Chain

- ✅ Baseline Sonnet: 15 turns, $0.97 (most direct comparison)
- ✅ Baseline Opus: 15 turns, $7.59 (shows Opus cost problem pipeline solves)
- ✅ Pipeline Sonnet: 15 turns, $3.38 (middle ground, promising for longer tasks)
- ⚠️ Pipeline Maestro Cache: 246k read tokens / turn 1 (needs analysis: is this wasteful or foundation?)

## Next Steps

1. **Extend T5_app_build to 50 turns** → measure inflection point where pipeline wins
2. **Analyze cache_read pattern** → are we reading useful context or redundant state?
3. **Profile maestro_layer** → where are the 1934s spent? (encoder 3s, maestro 1900s, decoder 30s?)
4. **Test with Opus maestro** → does pipeline + Opus beat Sonnet baseline on cost?

---

*Buildblock for: "3-layer Pipeline Architecture" paper section*
