# Design — `burnless economy` (real $ savings report)

**Date:** 2026-05-29 · **Tier:** gold-plan · **Author:** claude-opus (d505)
**Goal:** a report command that shows REAL token/cost savings split into Roberto's four
buckets, fixing today's under-count (flat $15/MTok on a summed token bag; Opus output
unpriced; cache priced at full input; tier downgrade = prompt-length only).

---

## 1. Architecture / decision + trade-offs

### 1.1 Root cause of the under-count (grounded in source)

| Symptom | Where | Why it under-counts |
|---|---|---|
| Single flat rate | `metrics.py:143-145, 207, 264, 324` + `dashboard.py:90` | `estimated_cost_avoided_usd = burnless_tokens/1e6 × 15.0`. Sums **input-equivalent** and **output-equivalent** tokens, then prices ALL at one $15 rate. |
| Opus output ignored | decoder savings `output_decompression_avoided` (`metrics.py:228-281`) | These are **Maestro output** tokens (Opus output = $75/MTok), priced at $15 → 5× under. Also lands in `compute_free` "other" bucket (`savings_formula.py:96`), not surfaced. |
| Cache priced at full | `repeated_context_avoided` = `cache_read_tokens` (`metrics.py:315-320`) | A cache hit saves `input_rate − cache_read_rate` (≈0.9× input), not full input. Priced at full $15 → ~11% **over** on rate, but lumped into flat sum so net signal is noise. |
| Tier downgrade = prompt len | `cli.py:654-665` records `estimate_tokens(body)` | Counts only the **prompt** that skipped Opus. Ignores worker **output** tokens entirely, and ignores the input/output **price differential** between cheap tier and Opus baseline. |

### 1.2 Counterfactual (the baseline we price against)

WITHOUT burnless, the orchestrator (Maestro) + all work would run on the **expensive
baseline model = gold/Opus**. Every saved token is priced at the rate of the model that
*would* have paid for it, by **token class** (input / output / cache-read). This is the
only way to make "cheap vs expensive baseline" honest.

### 1.3 Four buckets — data source, status, pricing rule

| # | Bucket | Token source (already tracked) | Price rule | Status |
|---|---|---|---|---|
| 1 | **Input compression (encoder)** | `by_source.capsule_compression` (`metrics.py:203`) | tokens × **Opus input** ($15/MTok) | ✓ tokens tracked; ✗ priced flat |
| 2 | **Maestro history/cache linearization** | `compact_state` (input-eq) + `output_decompression_avoided` (output-eq) (`metrics.py:260`) | compact_state × Opus input; decoder × **Opus output** ($75/MTok) | ✓ tokens; ✗ output unpriced + buried in "other" |
| 3 | **Worker tier downgrade** | `by_source.expensive_model_avoided` (`cli.py:659`) input-side proxy | tokens × (**Opus input − cheap-tier input**); worker **output** = MISSING | ⚠ input proxy only; worker output not instrumented |
| 4 | **Cache hits** | `repeated_context_avoided` + `keepalive_cache_renewed` (`metrics.py:318`, `metrics.py:430`) | tokens × (**input − cache_read rate**) ≈ 0.9× input | ✓ tokens; ✗ priced at full input |

**Genuinely missing data = worker output tokens per tier.** The worker run
(`live_runner.py`) never calls `record_brain_call`-style usage capture for the *worker*;
only Maestro brain calls are metered. v1 economy prices bucket 3 **input-side only** and
prints an explicit "worker output not yet instrumented (v2)" note — **no silent cap**
(per CLAUDE.md B-rule). v2 hook noted in §2 step 6.

### 1.4 Pricing table (Jan 2026 public Anthropic rates, $/MTok)

| Model | input | output | cache_read | cache_write |
|---|---|---|---|---|
| opus (claude-opus) | 15 | 75 | 1.50 | 18.75 |
| sonnet (claude-sonnet-4-6) | 3 | 15 | 0.30 | 3.75 |
| haiku (claude-haiku-4-5) | 1 | 5 | 0.10 | 1.25 |

`baseline_model = "opus"`, `cheap_tier_model = "haiku"` (bronze is the dominant cheap
tier). Both overridable later via config; bronze hardcodes the table with a comment.

### 1.5 CLI surface — **new `burnless economy`**, do NOT extend `burnless metrics`

- `metrics` stays the **raw auditable ledger** (every source, counters, legacy, the flat
  $15 number). Existing tests + `--global`/`--diff`/`--snapshot` consumers untouched.
- `economy` is the **user-facing $ report**: 4 buckets × {tokens, USD} + grand total +
  assumptions footer. Pure read/derive over the same `metrics.json`. New module, new
  command, additive — zero risk to the ledger. Decouples "truth" from "presentation."

**Trade-off accepted:** two commands instead of one flag. Worth it — different audiences
(audit vs founder $ story), and extending `metrics` would entangle the new per-class
pricing with the legacy flat number that tests pin.

---

## 2. Implementation plan (ordered, absolute paths)

1. **`/Users/roberto/antigravity/burnless/src/burnless/pricing.py`** (new)
   - `MODEL_PRICES: dict[str, dict[str,float]]` (opus/sonnet/haiku × input/output/cache_read/cache_write).
   - `BASELINE_MODEL = "opus"`, `CHEAP_TIER_MODEL = "haiku"`.
   - `rate(model: str, kind: str) -> float` — $/token (per-MTok ÷ 1e6), unknown→opus fallback, clamps ≥0.
2. **`/Users/roberto/antigravity/burnless/src/burnless/economy.py`** (new)
   - `@dataclass Bucket: name:str; tokens:float; usd:float; note:str=""`
   - `@dataclass EconomyReport: buckets:list[Bucket]; total_tokens:float; total_usd:float; assumptions:list[str]`
   - `compute_economy(metrics: dict, cfg: dict | None = None) -> EconomyReport` — pure, never raises, clamps bad input to 0. Formulas = §3.
3. **`/Users/roberto/antigravity/burnless/src/burnless/dashboard.py`** — add `render_economy(r: EconomyReport, *, show_cost: bool=True) -> str`. Per-bucket line: `name  tokens  $usd  (note)`; then total; then assumptions footer.
4. **`/Users/roberto/antigravity/burnless/src/burnless/cli.py`** — `cmd_economy(args)`: load root/paths/cfg/metrics (mirror `cmd_metrics` head, `cli.py:1477-1495`), call `economy.compute_economy`, print `dashboard.render_economy`. Support `--json` (dump report as JSON). Register `economy` subparser next to `metrics` in the parser-build block.
5. **`/Users/roberto/antigravity/burnless/tests/test_economy.py`** (new) — pure-function tests: known by_source → expected per-bucket USD; empty metrics → all zero; output bucket priced at $75 not $15; cache bucket priced at differential.
6. **(v2, NOT bronze) worker-output instrumentation** — capture worker `input_tokens`/`output_tokens` from `live_runner.py` stream-json + new `metrics.record_worker_usage(... tier=)`; economy bucket 3 then adds output differential. Logged as known gap in v1 footer.

---

## 3. Bucket formulas (exact — bronze types these verbatim)

```
P(model, kind) = MODEL_PRICES[model][kind] / 1_000_000        # $/token
by = metrics["by_source"]; n(k) = max(float(by.get(k,0) or 0), 0.0)

# Bucket 1 — input compression (encoder)
b1_tok = n("capsule_compression")
b1_usd = b1_tok * P("opus","input")

# Bucket 2 — Maestro history/cache linearization
b2_tok = n("compact_state") + n("output_decompression_avoided")
b2_usd = n("compact_state") * P("opus","input") \
       + n("output_decompression_avoided") * P("opus","output")   # <-- fixes Opus-output under-count

# Bucket 3 — worker tier downgrade (input-side only in v1)
b3_tok = n("expensive_model_avoided")
b3_usd = b3_tok * (P("opus","input") - P("haiku","input"))        # differential, not full
# note: "worker output not yet instrumented (v2)"

# Bucket 4 — cache hits (priced at the real cache differential)
b4_tok = n("repeated_context_avoided") + n("keepalive_cache_renewed")
b4_usd = b4_tok * (P("opus","input") - P("opus","cache_read"))    # ~0.9x input, not full

total_tokens = b1_tok+b2_tok+b3_tok+b4_tok
total_usd    = b1_usd+b2_usd+b3_usd+b4_usd
```

Assumptions footer (printed): baseline=opus, cheap tier=haiku, Jan-2026 public rates,
floors not ceilings, worker-output not yet metered.

---

## 4. Bronze-ready spec

(see returned spec block — copy verbatim into `burnless do --tier bronze`)
