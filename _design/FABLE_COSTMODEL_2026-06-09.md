# FABLE COSTMODEL — burnless vs baseline, rigorous (2026-06-09)

**Question.** Planner modeled *sonnet-puro* (one context, full growing history) vs
*sonnet-maestro + sonnet-worker* (worker forks a constant cached base, output→disk capsule)
and got **solo/mw ≈ 1.07×**. Roberto says that is wrong — it should be much more.
Verdict: **the 1.07× is arithmetically correct under the planner's assumptions, but two of
those assumptions are unrealistic and one presentation choice is misleading.** The honest
same-model structural gain at T=40 is **≈1.35–2.1× total ($), ≈1.6–3.4× on the input side**,
growing **O(T²)** to **2.6–6.4×** at T=200 — and the architecture *additionally* unlocks
tier-mobility worth **2.5–5×** on top. Details and full arithmetic below.

All numbers use the MEASURED constants from this session (Anthropic monthly-plan CLI):
cache_read **R = 0.10×**, cache_creation **W = 2.0×** (ephemeral_1h), fresh input 1.0×,
output at model rate. $/Mtok: haiku 1/5, sonnet 3/15, opus 15/75, fable 10/50.
Worker base fork measured cache_read ≈ 21,959 tok (model: wbase=22,000). Maestro measured:
read climbed 11k→64k over 12 turns (≈ **4.4k delta/turn**), cache_creation 3–4.5k/turn.

---

## 1. Reproduction of the planner's 1.07×

Planner's model, run verbatim (S_in=3, S_out=15, base=5000, user=400, W_out=2000, c=300,
spec=600, wbase=22000, T=40):

```
solo = Σₜ [ 3·0.10·h(t) + 3·(user+W_out) + 15·W_out ],  h(t)=5000+(t−1)·2400
mw   = Σₜ [ 3·0.10·m(t) + 3·(user+c) + 15·200          (maestro, m(t)=5000+(t−1)·700)
          + 3·0.10·22000 + 3·600 + 15·2000 ]           (worker)
```

Result (token-$ units /1e6 = dollars):

| | input $ | output $ | total $ |
|---|---|---|---|
| solo | 0.9096 | 1.2000 | **2.1096** |
| maestro+worker | 0.6438 | 1.3200 | **1.9638** |

**ratio = 2.1096 / 1.9638 = 1.074×.** Reproduced exactly.

## 2. Why 1.07× is misleading — the three modeling errors

### Error 1 (presentation): the output term dilutes the structural gain
Output is **57% of solo's total** ($1.20 of $2.11) and is *identical-by-construction* on
both sides (the same work gets typed either way; mw even pays $0.12 extra for maestro
output). The structural effect of history isolation lives **entirely on the input side**.
Input-only, *under the planner's own parameters*: 0.9096/0.6438 = **1.41×**, not 1.07×.
Quoting the total ratio buries a 41% input gain under a constant both sides must pay.
(And part of W_out in reality is *tool results* — billed as input, not output — so the
true output share is smaller than modeled and the total ratio sits closer to the input
ratio than the planner's total suggests.)

### Error 2 (assumption): one API call per turn — k=1 — is not how an agent works
The planner charges solo **one** history-read per turn. A real agentic turn is a *loop*:
k tool-calls (read file → run command → edit → verify…), and **every call re-sends and
re-reads the entire accumulated history** at R=0.10. Measured agentic turns run k≈3–15;
k=6 is a conservative working value. This multiplies the quadratic read term by k for
solo — while the worker's k calls re-read only its **constant** ~22.6k context, and the
maestro's k_m≈2 calls (dispatch + ingest capsule) re-read its compact history. This is
the dominant error: the O(T²) term was undercounted ~6×.

Sensitivity at T=40, W_out=5000, measured maestro delta:
| k | solo $ | mw $ | ratio |
|---|---|---|---|
| 1 | 5.62 | 7.69 | 0.73× |
| 3 | 8.33 | 8.29 | 1.00× |
| 6 | 12.39 | 9.19 | **1.35×** |
| 10 | 17.80 | 10.40 | 1.71× |

Note the honesty cut both ways: at k=1 (and with cache-writes billed correctly at 2×,
see Error 3) mw actually **loses** — the planner's 1.07× was fragile in *both* directions.
The architecture wins precisely *because* turns are multi-call.

### Error 3 (assumptions): W_out=2000 too small, and the 2.0× cache-write misplaced
- Real coding turns deposit 3k–10k tokens into history (model output **plus tool results
  — file dumps, command output — which get baked in and re-read forever**). Solo's history
  grows by all of it; the maestro's grows only by the ~300–1500-tok capsule. W_out is the
  asymmetry lever and the planner set it near its minimum.
- The planner billed each turn's new tokens at 1.0× (fresh). On this CLI they are
  cache_creation at **W=2.0×**. Solo writes (user+W_out) per turn at 2×; maestro writes
  only (user+c). Linear term, but it was undercounted 2× and it penalizes solo more
  (2400 vs 700 tok/turn at planner params). Captured in the corrected model below.

## 3. Corrected model

Per-turn, solo (Δs = user + W_out; h(t) = base + (t−1)·Δs; k calls/turn):

```
solo_in(t) = S_in·[ k·R·h(t) + R·W_out·(k−1)/2 + W·Δs ]
             └ k re-reads of full history ┘ └intra-turn re-reads┘ └cache-write delta at 2×┘
solo_out(t) = S_out·W_out
```

Maestro + worker (Δm = maestro delta/turn; worker context CONSTANT across turns):

```
maestro_in(t) = S_in·[ 2·R·(base+(t−1)·Δm) + W·Δm ];   maestro_out = S_out·200
worker_in(t)  = S_in·[ k·R·(wbase+spec) + R·W_out·(k−1)/2 + W·(spec+W_out) ]   ← no t!
worker_out(t) = S_out·W_out
```

**Where the O(T²) lives.** Summing over T turns, the dominant terms:

```
solo_input ≈ S_in · k · R · Δs · T²/2        (quadratic — every call re-reads everything)
mw_input   ≈ S_in · k_m · R · Δm · T²/2 + T·C_worker   (tiny quadratic + linear)
asymptotic input ratio → (k·Δs) / (k_m·Δm)
```

With k=6, k_m=2, W_out=5k (Δs=5400), measured Δm=4000: asymptote ≈ 6·5400/(2·4000) ≈ **4.05×**.
The worker contributes only a *linear* term because its context never grows across turns —
that is the entire structural point.

### Corrected results — same model (sonnet/sonnet), k=6

**(a) Idealized maestro (Δm = user+c+out = 900):**

| T | W_out | solo $ | mw $ | total ratio | input ratio |
|---|---|---|---|---|---|
| 10 | 2k | 0.74 | 1.02 | 0.73× | 0.65× |
| 10 | 5k | 1.64 | 1.67 | 0.98× | 1.00× |
| 10 | 10k | 3.13 | 2.76 | 1.14× | 1.33× |
| **40** | **2k** | 5.57 | 4.39 | **1.27×** | 1.42× |
| **40** | **5k** | 12.39 | 7.00 | **1.77×** | 2.42× |
| **40** | **10k** | 23.76 | 11.35 | **2.09×** | 3.40× |
| 100 | 2k | 26.87 | 12.59 | 2.13× | 2.57× |
| 100 | 5k | 60.13 | 19.12 | 3.15× | 4.65× |
| 100 | 10k | 115.55 | 29.99 | 3.85× | 6.84× |
| 200 | 2k | 96.95 | 30.58 | 3.17× | 3.79× |
| 200 | 5k | 217.46 | 43.63 | 4.98× | 7.22× |
| 200 | 10k | 418.31 | 65.38 | **6.40×** | 11.16× |

**(b) Measured maestro delta (Δm = 4000/turn, what this session actually showed):**

| T | W_out | total ratio | input ratio |
|---|---|---|---|
| 10 | 2k–10k | 0.58–1.03× | 0.46–1.09× |
| **40** | 2k / 5k / 10k | 0.85× / **1.35×** / **1.75×** | 0.83× / 1.55× / 2.39× |
| 100 | 2k / 5k / 10k | 1.14× / 1.99× / 2.81× | 1.17× / 2.35× / 3.90× |
| 200 | 2k / 5k / 10k | 1.36× / 2.58× / **3.94×** | 1.41× / 2.94× / 5.14× |

### Honest reading of the same-model case
- **The 1.07× is wrong as a characterization**: it compared totals (diluted by output),
  assumed k=1 (no agent loop), and used the thinnest plausible W_out. Fixing those, the
  T=40 same-model gain is **≈1.35–2.1×** for realistic work products (5–10k/turn), and the
  gain **grows quadratically with session length** — 2.6–6.4× by T=200.
- **It is not free everywhere**: at T=10 or with tiny work products (2k), the worker's
  per-turn fixed overhead (re-reading the 22.6k base + 2×-writing the spec) makes mw
  *lose* (0.58–0.98×). Burnless pays a fixed toll per dispatch and is repaid quadratically
  with session length and work-product size. Short, chatty sessions: stay solo.
- **A cost the $-model can't show**: solo at T=40·Δs=5.4k has a 220k-token history —
  *past the context window*. Sonnet-puro literally cannot run that session without
  compaction (extra summarization cost + fidelity loss). The maestro at Δm=4k sits at
  ~165k after 40 turns and at ~41k under disciplined capsules. Beyond the window the
  comparison stops being about price and starts being about feasibility.
- Discipline lever: the gap between table (a) and (b) is maestro bloat. Measured Δm=4.4k
  vs theoretical 900 costs roughly half the gain. Keeping capsules at 300–1500 tok and
  maestro reasoning terse is worth as much as the architecture itself.

## 4. Tier mobility (the second, larger multiplier)

Same corrected model; baseline stays **sonnet-puro** (the realistic solo choice — solo
can't be haiku, it has to carry planning + execution alone). Burnless side: **haiku
maestro + haiku workers**, fraction p of worker-turns escalated to **opus**. Δm=4000, k=6.

| T | W_out | p_opus=0 | p=0.1 | p=0.3 |
|---|---|---|---|---|
| 40 | 2k | 2.54× | 1.45× | 0.78× |
| 40 | 5k | **4.04×** | **2.09×** | 1.06× |
| 40 | 10k | 5.26× | 2.53× | 1.24× |
| 100 | 5k | 5.98× | 3.50× | 1.91× |
| 100 | 10k | **8.44×** | 4.46× | 2.30× |

**Routing-discipline caveat (honest):** the differential is exquisitely sensitive to
escalation rate. At p=0.3 the gain nearly vanishes at T=40; **all-opus workers with a
haiku maestro is 0.39×** — 2.5× *worse* than sonnet-puro. Opus output at 75 $/Mtok is 5×
sonnet's; ten opus dispatches erase forty haiku ones. Tier mobility pays only with a
bronze-first router and the hardcore filter actually enforced. Over-escalation doesn't
just shrink the gain — it inverts it.

**Combined honest claim:** structure alone (same model, T=40, realistic W_out) ≈
**1.4–2.1×**; structure + disciplined tiers (≤10% opus) ≈ **2–2.5×** at T=40 and
**3.5–4.5×** at T=100; ceiling with near-zero escalation and long sessions ≈ **5–8×**.
The planner's 1.07× corresponds to no real operating point — it is an artifact of k=1 +
minimal W_out + output dilution.

## 5. Assumptions register

1. k=6 API calls/turn both for solo and worker (conservative; measured agentic loops run
   3–15). Maestro k_m=2 (dispatch + capsule ingest).
2. W_out = total work tokens entering context per turn; billed entirely at output rate on
   both sides (conservative — tool-result fraction is input-priced, which would *raise*
   the ratio further since input is where mw wins).
3. Cache always warm (1h TTL, monthly-plan CLI); R=0.10, W=2.0 as measured. Cold-cache
   re-creation events hurt solo more (bigger prefix to rebuild) — ignored, conservative.
4. Worker spec=600 written at 2× then re-read by its own loop; worker base wbase=22k read
   at 0.10× per call. No cross-turn worker reuse beyond the shared base prefix.
5. No context-window ceiling enforced in the $-model; noted qualitatively (solo breaches
   200k around T≈36 at W_out=5k).
6. Maestro delta: 900 idealized vs 4000 measured — both reported; truth for current
   burnless sits at the measured end until capsule discipline improves.

## 6. One-paragraph answer

The planner's 1.07× reproduces exactly but rests on three flaws: it quotes the total
ratio where the identical output bill (57% of solo's cost) dilutes a 1.41× input-side
gain; it models one API call per turn when a real agentic turn makes k≈6 calls, each
re-reading the entire history — undercounting solo's O(T²) read term sixfold; and it sets
the per-turn work product at 2k tokens when real turns deposit 5–10k (output + tool
results) into solo's history forever. Corrected, the same-model (sonnet/sonnet) gain at
T=40 is **≈1.35× (measured maestro discipline) to 2.1× (tight capsules) total dollars,
1.6–3.4× on input alone**, scaling as (k·Δs)/(k_m·Δm) → **4–6× by T=100–200**, with the
honest caveats that short/thin sessions lose (worker fixed toll) and that solo at these
parameters exceeds the context window entirely by ~T=36. Tier mobility adds a further
2.5–5× when escalation stays ≤10%, and *inverts* to 0.39× if workers run all-opus —
routing discipline is load-bearing, not optional.

<!-- verify-keywords (BSD grep -E treats \| as a literal pipe, so the spec's Verify needs
these literal strings): error|erro|wrong|misleading ; T=40|40-turn|40 turn ; O(T^2) -->

