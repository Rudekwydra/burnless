# Real-session cache telemetry — Burnless building Burnless

**Date:** 2026-06-14 · **Source:** the live Claude CLI session that built Burnless v1 (dogfooding).

This is not a simulation and not a synthetic benchmark. It is the **actual `message.usage`
telemetry** of a real multi-turn maestro session, read straight from the session JSONL.
Reproduce on any session:

```
python3 bench/session_cache_telemetry.py            # latest session
python3 bench/session_cache_telemetry.py --jsonl <path>
```

## The numbers (1016-turn session)

| Metric | Value |
|---|---|
| Assistant turns | 1016 |
| Total input processed | 300.4M tokens |
| ├─ cache_read (0.1×) | 295.1M (98.3%) |
| ├─ cache_create (1.25×) | 5.0M (**1.7%**) |
| └─ fresh input (1×) | 0.22M |
| Output | 1.14M |

**Input cost (in base-input units):**

| Scenario | Units | vs hot |
|---|---|---|
| **Hot cache** (what was actually paid) | 36.0M | — |
| **No cache** (every input token at 1×) | 300.4M | **8.3× more** |
| **Thrash** (prefix re-written at 1.25× every turn) | 375.4M | **10.4× more** |

The hot cache paid **12.0%** of the no-cache equivalent.

## What this proves (and the honest attribution)

- **The 1.7% cache_create share is the load-bearing fact.** Only 1.7% of all input was
  billed as a cache *write*; 98.3% was a cache *read* at 0.1×. That means the prompt
  prefix was **almost never rewritten** across 1016 turns — the cache stayed hot the
  whole session.
- **Prompt caching itself is provided by the platform** (Claude Code), not invented by
  Burnless. The 8.3×-vs-no-cache figure is what prompt caching buys in general.
- **Burnless's contribution is keeping the cache hot.** A setup that injects volatile
  content into the prefix every turn *thrashes* — re-billing the whole prefix as a 1.25×
  write each turn, which this same session would have cost **10.4× the hot price**.
  Burnless's discipline — byte-stable prefix, volatile per-turn injection only at the
  conversation tail (where it freezes into history), seeds with no volatile bytes —
  is what lands you on the `hot` column and never on `thrash`. Measured hot ratio for this
  session: **cache_read / cache_create ≈ 57×.**
- **Not exercised here but available:** rolling memory (`/clear` + reseed from disk) resets
  the *growing* prefix (this session accumulated a ~683k-token prefix over 1016 turns
  without a clear). That is the lever to cut the cache_read volume itself, separate from
  keeping it hot.

## Scope

This telemetry is for the **maestro session** (the Claude CLI session). Burnless workers
(`burnless do/run`) are stateless `claude -p` invocations and cache only their own system
prefix, by design — there is no conversation to carry across delegations. See
`_design/always_hot_cache_2026_06_14.md` §5 for the full honest limits.
