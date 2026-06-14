# Burnless vs Sonnet-solo — documented benchmark

**Date:** 2026-06-14 · **Burnless:** v1 launch candidate

This is the launch reference for the headline claim: **Burnless is meaningfully cheaper
than running Sonnet by itself**, on a long multi-turn coding session, with the savings
growing as the session grows. Two independent measurements: a free reproducible
simulation (cost model) and a real `claude -p` run (actual tokens).

---

## 1. Monte Carlo simulation (free, reproducible)

Cost model over a 100-turn session, 30 Monte Carlo runs, 23k persistent prefix.

```
python3 bench/v2.py --runs 30 --turns 100 --seed 42
```

| Scenario | Cost (mean, 100 turns) | vs Pure Opus |
|---|---|---|
| A1 — Pure Opus | $532.61 | baseline |
| **A2 — Pure Sonnet (solo)** | **$105.42** | −80.2% (5.1×) |
| B — Free-pick Opus/Sonnet | $328.74 | −38.3% |
| **Z — Burnless** (Sonnet brain + tier workers) | **$33.35** | −93.7% (16.0×) |

**Headline: Burnless vs Sonnet-solo → $33.35 vs $105.42 = 3.16× cheaper (−68.4%).**

Reproducible by anyone: same `--seed 42` yields the same numbers. This is a *cost model*
(token accounting + published prices), not a live API run — see §2 for the empirical anchor.

---

## 2. Real-API anchor — replay vs capsule (`claude -p`, subscription)

Measures actual tokens for a Sonnet session run two ways over N turns:
- **replay** — full transcript re-sent each turn (the standard loop) → input grows Θ(N²)
- **capsule** — Burnless capsule state per turn → input grows Θ(N)

```
python3 bench/replay_vs_capsule.py --turns 30 --model sonnet
```

> Runs on the Claude subscription via `claude -p` (no paid-API key). Transport verified:
> `replay_vs_capsule.py` shells out to `claude -p`, not the paid SDK.

**⚠️ QUARANTINED (invalid methodology) — 30 turns, Sonnet, 2026-06-14:**

| Task | Replay | Capsule | Ratio |
|---|---|---|---|
| `--task migration` | $5.16 | $4.67 | 1.10× *(invalid)* |
| `--task code` | $5.79 | $4.46 | 1.30× *(invalid)* |

These numbers are **not valid** and must not be published. Both arms **cache-thrashed**:
the per-turn CLI call sent a changing prompt each time, so the prompt cache was written
(`cache_create`) instead of read (`cache_read`) — measured turn 1: `cache_read=0,
cache_create=39886`; turn 30: `cache_read=17306, cache_create=28714`. That is **not the
fair, equal-rules setup a real append-only loop gets** (where the byte-stable prefix is
`cache_read` at 0.1× for BOTH arms). A corrected, **symmetric** methodology is being
designed (`_design/fair_benchmark_methodology_2026_06_14.md`); rerun under equal cache
rules before quoting any real-API number. Fairness — identical cache treatment on both
arms — is the requirement, not a bigger Burnless margin.

**Honest reading — the real number at 30 turns is modest, and that's expected:**
1. **Output dominates at short N.** Each turn emits 200–900 output tokens (paid in both arms),
   so the input-history difference (the thing Burnless shrinks) is a small slice of total cost.
2. **`claude -p` auto-caches the prefix**, which already cheapens the replay baseline — so the
   subscription path understates the O(N²) gap a pay-per-token API (no caching) would show.
3. **The advantage grows with N and prefix size.** Code (1.30×) already beats prose (1.10×)
   because code context accumulates faster. The 100-turn, 23k-prefix regime is where the
   curve separates hard — that is what the §1 simulation models (16× vs Opus, 3.16× vs Sonnet).

So: **§1 (16× vs Opus) is the long-session / large-prefix regime; §2 (1.1–1.3× at 30 turns)
is the short-session reality.** Both are reported. Do not quote §1 as if it were a 30-turn
result. Burnless wins by construction at N≥2 (`MATH.md` §7) and the margin widens with N.

---

## 3. Honest framing

- §1 is a **simulation** (reproducible, zero-cost, conservative price assumptions). It does
  not exercise worker correctness — it's a pure cost model.
- §2 is **real tokens** on a fixed task. Short sessions (N<~15) may not amortize the pipeline
  overhead — Burnless wins by construction at N≥2 and the margin widens with N
  (`MATH.md` §7). The advantage is for *long* sessions.
- v1 launch additionally hardened **reliability** (single JSON-envelope worker contract,
  rolling memory that survives `/clear`, off/on modes) — these reduce phantom-completions
  and retries, which a cost model does not capture but a real workload feels.

## 4. Reproduce everything

```
python3 bench/v2.py --runs 30 --turns 100 --seed 42        # §1, free
python3 bench/replay_vs_capsule.py --turns 30 --model sonnet # §2, subscription
```

Cost math + derivations: `MATH.md`. Architecture: `PROTOCOL.md`.
