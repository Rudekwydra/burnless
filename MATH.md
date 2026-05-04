# The Math

> **Burnless is intent-compressed intelligence orchestration.**
> The math below derives the cost shape that follows from that definition.

Burnless makes one claim: multi-turn agent loops cost `Θ(N²)` in the standalone case and `Θ(N)` in the Burnless case. This document derives both, then composes them with per-model pricing into a single dollar formula you can parameterize against any provider.

The formula is less than half the story — orchestration of intelligence by compressing intent is the *what*; the cost shape is only the consequence. We publish the math because it is the consequence that pays for your API bill, but read it knowing the bigger thesis sits above it.

The unit of comparison is **`$` per session of `N` turns**. Token counts alone are misleading because Opus, Sonnet, and Haiku tokens have different prices — a fair comparison must price each token at its own model's rate.

---

## 1. Notation

| Symbol | Meaning | Typical value |
|---|---|---|
| `N` | turns in the session | 10–100 |
| `P` | persistent prefix (system prompt + tools) tokens | 5,000–25,000 |
| `O_k` | output tokens at turn `k` | 200–1,500 |
| `U_k` | new user input tokens at turn `k` | 100–10,000 |
| `C` | capsule size (Burnless history line) tokens | ≈ 20 (≈ 80 chars) |
| `α` | compression ratio of capsule vs full turn (`C / (U_k + O_k)`) | 0.70–0.80 economy → α ≈ 0.20–0.30 |
| `m` | model identifier ∈ {opus, sonnet, haiku, …} | — |
| `p_in_m`, `p_out_m`, `p_cr_m`, `p_cw_m` | input / output / cache_read / cache_write price per MTok for model `m` | see §6 |

---

## 2. Standalone loop — derivation of `Θ(N²)`

Every turn replays the full conversation. At turn `k`, the model receives:

```
P  +  Σ_{j<k} (U_j + O_j)  +  U_k       as input
                                          → produces O_k
```

Summing input tokens across all `N` turns:

```
input_total(N)  =  N · P  +  Σ_{k=1..N} Σ_{j<k} (U_j + O_j)  +  Σ_{k=1..N} U_k
                =  N · P  +  Σ_{j=1..N-1} (N - j)(U_j + O_j)  +  Σ U_k
```

The middle term is the killer. With uniform `U_j + O_j ≈ T`, it expands to `T · N(N-1)/2`. That's the `Θ(N²)`. The `N · P` term is linear (and the reason caching `P` matters so much), but the quadratic term dominates as `N` grows.

**Output stays linear**: `output_total(N) = Σ O_k = Θ(N)`. Output is never the bottleneck at the cost level — input dominates because of the replayed history.

---

## 3. Burnless loop — derivation of `Θ(N)`

Brain holds a capsule per turn instead of the raw exchange. At turn `k`, the Brain sees:

```
P_cached  +  Σ_{j<k} C  +  U_k       as input
                                       → produces orchestration tokens (small)
                                       → spawns worker(s) on demand
```

The worker is a *separate API call* with its own usage. It receives a focused prompt (`P_cached + capsules of relevant turns + new task`), executes, returns a compact result.

Brain's input across `N` turns:

```
brain_input(N)  =  P  (1× cache_write)
                +  (N-1) · P    (cache_read on every subsequent turn)
                +  Σ_{k=1..N} (k-1) · C    (capsules accumulating)
                +  Σ U_k
```

The capsule term is `C · N(N-1)/2`, technically still `Θ(N²)` — *but* with `C ≈ 20` instead of `T ≈ 1,500`, the constant is **~75× smaller**. For `N ≤ 1000` it stays beneath the linear cache-read term. Practically: linear.

The `(N-1) · P` cache-read term dominates for typical `N`, and it's billed at `p_cr` (≈ 10× cheaper than `p_in`). That's the real win.

**Worker calls** add a constant per delegation (not per turn) — the worker's own `P + capsule_subset + task_input` paid once per spawn.

---

## 4. From tokens to dollars

Compose §2 and §3 with per-model pricing. For any cenário with model assignments:

```
cost($)  =  Σ_m  ( in_m   · p_in_m
                +  cr_m   · p_cr_m
                +  cw_m   · p_cw_m
                +  out_m  · p_out_m )
```

Where `in_m`, `cr_m`, `cw_m`, `out_m` are the tokens of each kind charged at model `m`'s rate. Every scenario reduces to picking the model mix and plugging it into this single equation.

This is why the unit is `$`, not tokens. A "token saved" on Opus is worth 5× a "token saved" on Sonnet, and 19× on Haiku. Aggregating tokens loses that information; aggregating dollars preserves it.

---

## 5. The four scenarios

All scenarios use the same `P`, `U_k`, `O_k`, and `N`. They differ only in **model mix** and whether the loop replays history (`Θ(N²)`) or capsules it (`Θ(N)`).

### A1 — Pure Opus 100

```
brain         = opus    (every turn)
delegations   = none    (single-agent loop)
history       = full
cache         = opus prefix (high hit rate, single model)
cost_dominant = N · (Σ_{j<N} (U+O)) · p_cr_opus     +  output_total · p_out_opus
              = Θ(N²) · p_in_opus  (effectively, since cache_read still scales with N)
```

Best capability, worst cost. The reference upper bound.

### A2 — Pure Sonnet 100

Same shape as A1, swap `opus → sonnet` in every term. **5× cheaper than A1** by the input price ratio alone, before any other optimization. Loses Opus-grade reasoning on hard turns.

### B — Free-pick (developer behavior today)

Each turn, the developer picks Opus or Sonnet ad-hoc. Random split, summing to N.

```
brain_at_turn_k   = random.choice([opus, sonnet])
history           = full
cache             = SHATTERED
```

Critical detail: prompt caches are **per endpoint**. Switching `opus → sonnet` invalidates the Sonnet cache (it never had one for this prefix), and switching back to Opus reads stale-or-cold. Effective cache hit rate collapses toward 0 on alternating turns. **B is more expensive than A2 even when most calls are Sonnet**, because of the cache thrash.

This is the single most important scenario for the pitch — it's what real developers are doing today without an orchestration layer.

### Z — Burnless hardcore

```
brain         = sonnet  (fixed → cache always hot)
workers       = mix:    opus    on hardcore_filter-mandated turns  (~5–10%)
                        sonnet  on medium tasks                    (~25–35%)
                        haiku   on bulk / cheap tasks              (~55–70%)
history       = capsules (C ≈ 20 tokens per turn)
cache         = shared brain↔workers prefix (single byte-identical P)
```

Four economy vectors stacked simultaneously:

1. **Brain model**: Sonnet, not Opus → 5× cheaper baseline.
2. **Worker tier mix**: Haiku on majority → average `$/token` collapses further.
3. **Cache continuity**: Brain never switches model → cache_read price applies on every turn after the first.
4. **History compression**: capsules instead of full turns → kills the quadratic term.

Each vector alone is incremental. Stacked, they multiply.

---

## 6. Reference prices (Anthropic, May 2026)

Per million tokens (MTok). Cache write = 1.25 × input. Cache read = 0.10 × input (10× cheaper).

| Model | Input | Cache write | Cache read | Output |
|---|---:|---:|---:|---:|
| `claude-opus-4-7` | $15.00 | $18.75 | $1.50 | $75.00 |
| `claude-sonnet-4-6` | $3.00 | $3.75 | $0.30 | $15.00 |
| `claude-haiku-4-5` | $0.80 | $1.00 | $0.08 | $4.00 |

Ratios at a glance: Opus is **5× Sonnet** is **3.75× Haiku** on input, **roughly the same ratios on output**. A Haiku cache_read at $0.08/MTok is **187× cheaper** than an Opus fresh input at $15/MTok. That spread is the whole game.

---

## 7. The challenge

Plug your own `(P, U_k, O_k, N, model_mix, α)` into §4. If your numbers contradict the simulation in `bench/v2.py`, open an issue with the JSON. We don't care if Burnless wins your specific workload — we care that the formula is right.

Burnless loses in exactly one regime: **`N = 1`**. A single-turn query with no delegation pays Brain overhead for a Worker call you didn't need. The orchestration layer is pure cost when there is nothing to orchestrate.

For every other workload — `N ≥ 2`, any non-trivial prefix, any reasonable model mix — the four economy vectors of §5.Z stack by construction:

- Brain Sonnet undercuts Brain Opus on input price (5×).
- Tier-down workers undercut Opus on average per-token cost.
- A fixed-model Brain holds cache hot; a switching loop thrashes it.
- Capsules collapse the quadratic history term to a linear one.

Each vector is independently positive. Their composition cannot net negative against any baseline that forgoes them. The math forces Burnless ahead — there is no "your workload is different" escape clause for `N ≥ 2`.

**Burnless only loses if you don't use it.**

---

## 8. Keeping the cache hot (architectural, not temporal)

Sections 3–4 assume `cache_read` applies on every turn after the first. That assumption is load-bearing: if the prefix cache silently dies between turns, every "read" silently becomes a "write" at 12.5× the price, and Burnless collapses toward the standalone curve. We don't depend on developer discipline to keep it hot — we depend on architecture.

The Maestro session enforces four properties simultaneously:

1. **TTL = 1 hour, not 5 minutes.** Every persistent block is sent with `cache_control: {"type": "ephemeral", "ttl": "1h"}`. Anthropic's default ephemeral TTL is 5 min — long enough to die mid-conversation. 1h covers virtually every real interactive session without eviction.

2. **Append-only chat, persisted on disk.** The session lives in `.burnless/maestro_session.jsonl`. Every new turn *extends* the message array; it never rewrites earlier blocks. The prefix that was cached on turn 1 is byte-identical on turn `k`, so the cache lookup hits.

3. **Four cache breakpoints, nested.** Anthropic allows at most 4 `cache_control` markers per request. Maestro places them at the boundaries of `system → memory → plan → capsules`, so Brain and Workers all share the same prefix and pay `cache_read` from their second call onward.

4. **Realtime ROI compaction, not fixed capsule counts.** Burnless treats cached context as immutable layers: protocol header, glossary/schema, memory/plan, frozen capsule blocks, hot tail, and the new user capsule. It never rewrites a cached block. When the hot tail grows, Burnless creates a new super-capsule only if the future cache-read savings pay for the new cache write and the compaction call:

   ```text
   K · r · (B - S) > W · S + M
   ```

   Where `B` is the old hot-tail token count, `S` is the compacted token count, `K` is expected future turns inside the TTL, `r` is cache-read/fresh-input price, `W` is cache-write/fresh-input price, and `M` is the one-time compaction cost expressed as input-token-equivalent. With `r = 0.10` and `W = 2.0`, a 70% compression (`S/B = 0.30`) breaks even after about 9 future turns before compaction cost; a 90% compression breaks even after about 3. Fixed rules like "compact every 6 capsules" are therefore wrong except by accident.

   This preserves the cache invariant: old frozen blocks stay byte-identical; the new super-capsule is appended as the next frozen block and becomes profitable only when the math says it should.

**Known gap (roadmap):** there is no keepalive loop. If a session sits idle > 1h with zero calls, the TTL expires and the next call pays `cw` again instead of `cr`. A `--keepalive` mode that fires a 1-token ping every ~50 min would close this for daemon-style usage; tracked as a TODO because the §3 derivation breaks on idle eviction.

For the simulation in `bench/v2.py`, sessions are treated as contiguous within the TTL — true for any normal interactive workload. If your use case includes long idle gaps, model them by inflating `cw` accordingly.

---

## 9. What `bench/v2.py` does with this

- Loads §6 prices (override via flag).
- Samples `N` turns with `U_k ~ Uniform(2k, 10k)`, `O_k ~ Uniform(200, 1500)`, `α ~ Uniform(0.70, 0.80)`.
- Runs each scenario through §4 with its model mix and history mode.
- Repeats `R` times (default 30) → distribution per scenario.
- Reports `p10 / p50 / p90 / mean` of `$ per session`.
- Plots all four on a single chart, log-scale Y axis (because the spread between A1 and Z exceeds 100×).

No API calls. No keys. `python bench/v2.py --simulate` reproduces the published table on any laptop.

For real-API validation: `bench/v2.py --real --runs 1` exists but is not advertised on the landing. Contributors who want to spend their own credit can run it and submit the JSON via PR — that's how the published numbers get audited.

---

## 10. Epistemic fidelity — a third axis

Sections 2–9 treat cost as the only axis. There is a second property that capsule compression affects and that cost math cannot capture: **how much of the argumentative trajectory a session preserves**.

### The anchoring problem in standard chat

In a full-transcript loop, the model at turn `k` sees not only what was decided at turn `j < k`, but *why* — the arguments, the concessions, the explicit agreements. This creates anchoring bias: the model defends prior decisions not because they are correct, but because the argumentative context makes reversal costly. Changing course requires relitigating the original argument inside the same session, against a model that participated in constructing it.

The same content, summarized into a compact document and presented to the same model in a *fresh* session, can produce a different evaluation. The model's position on the document is not anchored to the trajectory that produced it. This is not inconsistency — it is evidence that the prior session's output was shaped by its own history, not only by the content.

**The anchoring is proportional to argumentative richness.** A full transcript anchors strongly. A capsule — "cfg db → postgres :: OK" — anchors weakly: the Brain knows the result, not the argument. Weak anchoring makes past decisions more revisable.

### Workers are always pure

Workers receive a task, a cached system prompt, and the capsules relevant to that task. They do not receive the argumentative history of the Brain session. Every Worker call is epistemically fresh. This is not a limitation — it is the correct design for execution. An executor that inherits the Brain's accumulated debate would defend architectural decisions when its job is to implement them.

### Compression modes as an epistemic trade-off

The three compression modes (`light`, `balanced`, `extreme`) are not only cost settings. They control where on the **cost × epistemic fidelity** plane the session runs.

| Mode | Compression layers active | Anchor preserved | Friendly | Savings vs standalone | Use when |
|---|---|---|---|---|---|
| `light` | Minifier only (L1) | **Yes** | On | ~40% | Design sessions, architecture debates, decisions that may need revisiting |
| `balanced` *(default)* | Minifier + semantic encoder (L1 + L2) | No | On | ~88% | Project execution, multi-step implementation, standard workflows |
| `extreme` | All layers (L1 + L2 + L3 opt-in) | No | **Off** | ~93%+ | CI/CD pipelines, batch automation, no human in the loop |

`light` mode preserves the argumentative structure in capsules — the encoder is skipped and only deterministic minification runs. The Brain accumulates context that a human would recognize as reasoning, not just state. The cost is a fatter history and lower savings; the benefit is that the session can genuinely reconsider.

`balanced` discards the trajectory and retains only the semantic result. The Brain knows what was decided; it does not know how. This is the correct default for execution-heavy sessions where continuity of *state* matters but continuity of *argument* does not.

`extreme` adds maximum compression and disables natural-language expansion (friendly mode off). Output is machine-readable capsules without prose wrapping. Appropriate for pipelines where no human reads the intermediate output.

### Setting the mode

```yaml
# .burnless/config.yaml
compression:
  mode: light       # light | balanced | extreme
```

Or per-invocation: `burnless --mode light "review this architecture decision"`.

The choice is not about how much you trust the model. It is about what the task requires: **decisions need anchoring; execution needs purity.**
