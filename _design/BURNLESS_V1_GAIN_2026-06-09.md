# Burnless v1 — measured gain (2026-06-09, CORRECTED)

> CORRECTION (Roberto caught it): the first version compared v1 against a COLD (no-cache) baseline. That
> is a STRAWMAN — Sonnet-solo ALSO uses Anthropic's auto-cache (linear, read at 10%), so nobody runs cold.
> Cache is a COMMODITY; both arms have it. The real gain is NOT "having a cache". This doc is the fair version.
> Fair-baseline rule: [[feedback-fair-baseline-sonnet-with-cache]].

## How Anthropic auto-cache works (monthly plan, both models)
Caches the prefix (system + tool defs + conversation), reads the cached prefix at ~10% of input price,
writes new tokens at the TTL rate (measured w=2.0 = ephemeral_1h). SAME mechanism for haiku/sonnet/opus.
So Sonnet-solo's conversation cache is ALSO linear. The cache is not where burnless wins.

## Fair comparison: burnless vs Sonnet-solo, BOTH cached (cost model, $/Mtok haiku 1/5, sonnet 3/15)
Conversation grows ~4500 tok/turn (cached, read 10% in both arms). Work ~2000 out tok/turn.
- **Sonnet-solo:** sonnet reads the whole cached conversation (sonnet rate ×0.10) AND does the work in-context.
- **Burnless:** haiku-maestro reads the cached conversation (HAIKU rate ×0.10) to decide+delegate; the worker
  does the work on its OWN warm base, seeing only the compact spec (it does NOT carry the conversation).

| turn | sonnet-solo | burnless | gain |
|---|---|---|---|
| 1 | 32.9k | 12.5k | 2.6× |
| 10 | 45.0k | 16.5k | 2.7× |
| 40 | 85.5k | 30.0k | 2.9× |

**The gain GROWS with session length** (an earlier draft wrongly said "~flat 2.7×" — Roberto corrected it):
the ~2.7× above under-modeled the WORK. The true picture has two parts:
1. **Conversation carry**: maestro O(t) @ HAIKU rate vs solo O(t) @ SONNET rate → constant **3×** on this term.
2. **Work**: the burnless WORKER is **O(1) flat** (forks a FIXED warm base + reads only the compact spec —
   it NEVER carries the conversation) vs Sonnet-solo doing the work INSIDE the growing context = **O(t) per
   turn → O(T²) per session**. Measured work-cost ratio: 1.0× (t1) → 5.4× (t10) → **19.8× (t40)**, unbounded.

So the total gain is NOT flat — it grows with the session, driven by the work-isolation term. Both arms
still pay an O(T²) conversation-carry, but burnless's is at haiku rate (3×) AND its work-doer is O(1) not O(t).

## Where the gain ACTUALLY comes from (NOT the cache)
1. **Maestro carries the conversation at HAIKU rate (1) vs Sonnet rate (3)** = 3× on the conversation read.
2. **Work runs in a worker that does NOT re-read the conversation** (compact spec only). Sonnet-solo
   re-reads the whole growing conversation every time it acts.
3. **Tier routing**: simple work → haiku worker (out 5) instead of sonnet (out 15).

This is the burnless thesis correctly stated: cache = commodity; the moat = cheap-model orchestration +
work isolation + tier routing ([[strategic-pivot-to-synapsis-2026-05-07]]).

## Honest caveats (the ~2.7× is ILLUSTRATIVE, not measured end-to-end)
- Depends on work-per-turn, worker tier mix, and the maestro routing WELL on a cheap model. The bench
  [[burnless-maestro-bench-opus-beats-haiku-2026-05-28]] showed haiku over-escalating (routing badly) →
  if the cheap maestro mis-routes, the gain erodes. Maestro model is an open A/B question.
- Burnless adds a maestro→worker round-trip per work turn (2 calls vs sonnet-solo's 1); for trivial single
  tasks that overhead can erase the gain (but per Roberto, real work is always contextual/multi-step).
- The REAL number requires the proper A/B: burnless (haiku-maestro + tiered workers, cached) vs Sonnet-solo
  (cached), on a realistic VERBOSE multi-turn session. That measurement is still owed.

## v1 maestro default (cost model, separate from the above)
v1 default = never-compact (rolling-recompact built+validated, toggle OFF). Rolling only beats never-compact
past ~50 turns and only with a fat window (L≈15); the 25k re-warm/rewind dominates. IN-FORK compaction
(Fable §2.3) is the lever to make rolling win earlier — implement + re-measure.

## Next (Roberto: measure+document → implement → measure again)
1. ✅ Gain reframed against the FAIR baseline (Sonnet-solo cached); ~2.7× illustrative; attributed to
   model-tier + work-isolation + routing, NOT cache.
2. Owed: the REAL A/B (burnless vs sonnet-solo, both cached, verbose multi-turn) for the true number.
3. Implement rolling toggle (default off) + IN-FORK compaction; re-measure the cost model with new re-warm.
