# FABLE REVIEW — Burnless v1 design (2026-06-09)

Reviewer: claude-fable-5 (diamond, second opinion / red-team). Read: TARGET_ARCHITECTURE_2026-06-09.md,
BURNLESS_V1.md, REWRITE_CONCEPT_2026-06-09.md, src/burnless/maestro/engine.py, src/burnless/cache_policy.py,
src/burnless/warm_session.py. All math below uses the measured ground truth (cache_read×0.10,
cache_creation×1.25 or ×2.0 — see §2.3, this ambiguity matters), model prices haiku $1/$5, sonnet $3/$15,
opus $15/$75 per M in/out.

**TL;DR:** The architecture (one Agent spine, one execution core, one maestro, capsules-on-disk) is right
and earned. But the centerpiece — rolling rewind-recompact — is built on a trigger that, with the shipped
defaults, **never fires** (cache_write_ratio=2.0 → expected_savings is negative for EVERY window size), and
with the measured write price (1.25) it **fires every ~2 turns** and thrashes. The ROI formula is
scale-invariant: it is not a size trigger at all, it is a config-determined constant boolean. Separately,
the tool-less-maestro "wash" claim only holds for a stateless single-shot maestro; for the multi-turn
partner the design actually describes, tools-empty is 2.5–4× more expensive than keeping the cached prefix
unless the window still caches under `--tools ""` — which the same measurement (cache_creation=0 on BOTH
calls) suggests it does not.

---

## 1. OVERALL VERDICT

**Holds up at the architecture level; does not yet hold up at the economics level.**

Strongest parts (genuinely earned, not flattery):

- **Agent + CacheMode keystone.** "Change provider → cache mechanism follows" is the correct cure for the
  root defect (§11.0 of REWRITE_CONCEPT: three divergent who-runs-with-what mechanisms, cache attached to
  none). Making cache a derived property of (provider, auth) instead of a global boolean is the single best
  decision in the design. Landed, tested, correct.
- **One execution core.** Four dispatch paths with divergent gates (MCP bypassing spec_validator AND the
  Verify gate, with a runtime crash in `_run_sync`) is exactly the rot that kills tools like this. B1 done,
  B2 correctly deferred as its own phase rather than force-merged — that judgment call (extract shared
  primitives, don't regress the tuned dispatcher path) is right.
- **Capsules = lossy only in hot context, never destructive.** Writing the verbose history to disk before
  compacting is the correct memory model and what makes aggressive hot-context compaction defensible at all.
- **Collapse 4 maestros → 1.** Obviously right; no notes.
- **The A/B gate as a shipping criterion** ("nothing that loses to the realistic-verbose baseline ships") is
  the right epistemics — though the methodology as specced will not produce a trustworthy answer (§4.5).

Weakest parts:

- **The rolling-recompact trigger is broken as shipped** (§2.2). This is the v1 centerpiece (M1) and the
  thing the whole "FLAT per turn vs quadratic" claim rests on.
- **Tool-less maestro economics are mis-modeled** (§2.4). The measured "wash" is a turn-1 artifact.
- **Compaction call cost is treated as zero** (`compaction_cost_tokens=0`, and engine.py never passes it).
  Priced honestly it is a first-order term that can exceed the savings (§2.2.3).
- **The M1 prototype's ModelFn shape precludes the caching the design depends on** (§4.1).

The idea is good. The numbers, as currently wired, would deliver either "never compacts" (quadratic growth —
the exact disease v1 claims to cure) or "compacts every other turn" (cost + quality thrash). Both are
fixable with small changes (§5), which is why the verdict is "holds up, fix the trigger" and not "rethink."

---

## 2. CACHING

### 2.1 Rolling-recompact vs keep-everything-cached — the honest math

Setup: v ≈ 800 tok per turn-pair (verbose user + maestro reply), r = 0.10 cache-read, w = cache-write ratio,
maestro = sonnet. Both schemes pay w·v to cache-write each new turn — that term cancels; compare the carry.

**Keep-cached forever:** turn t carries 0.10·(BASE + t·v) ≈ 80·t effective tokens/turn, growing linearly;
total over T turns has the 0.05·v·T² quadratic term the design correctly identifies.

**Rolling, at a sane operating point** (fat window W = 24k ≈ 30 turns, constant capsule budget S = 1.6k):
- carry: 0.10·(S + W/2 avg) ≈ 1,360 eff tok/turn
- compaction, fresh-haiku compactor: input (W+S)=25.6k haiku-fresh = 8,533 sonnet-eq + output 1.6k haiku-out
  = 2,667 sonnet-eq → ≈ 11,200 per cycle = ~373/turn amortized
- total ≈ **1,730 eff tok/turn, flat.**

**Crossover: keep-cached is CHEAPER until t ≈ 22 turns** (80·t < 1,730). At t=60 keep-cached pays 4,800/turn
and rolling still pays 1,730. So:

> Rolling-recompact wins, but ONLY as a **lazy, fat-window** mechanism for genuinely long sessions. For
> sessions under ~20–25 turns, never compacting is strictly cheaper. Any trigger that fires earlier than
> roughly break-even ~20 turns of window is burning money AND information.

Two arguments FOR rolling that the design under-sells:
1. **TTL-death insurance.** If a session idles past the cache TTL, keep-cached re-pays cache_creation on the
   ENTIRE history (w·t·v — at t=60, ~60k tokens written again). Rolling caps the re-warm cost at S + window.
   For a human partner session with coffee breaks, this is arguably the strongest practical argument for
   rolling, and it's absent from the docs.
2. **Context-quality ceiling.** Past ~100k of accumulated chatter, model attention degrades regardless of
   price. Rolling bounds the live context. (Quality, not cost — but real.)

### 2.2 The should_compact trigger is broken — three distinct defects

**2.2.1 Scale-invariance: it's not a trigger, it's a constant.** The formula is
`K·r·(B−S) > w·S + M` with S = ρ·B (proportional, `estimate_compacted_tokens(window, ratio=0.30)`) and M=0
(engine.py never passes `compaction_cost_tokens`). Substitute: `K·r·(1−ρ)·B > w·ρ·B`. B cancels.
The decision is **independent of window size**:

- Defaults as shipped (K=8, r=0.10, ρ=0.30, **w=2.0**): saved/turn = 0.07·B, upfront = 0.60·B,
  expected_savings = 8·0.07·B − 0.60·B = **−0.04·B < 0 for every B. The maestro NEVER compacts.**
  M1's engine, wired to the documented config, silently degrades to accumulate-forever — quadratic growth,
  the exact failure mode §0.A claims to eliminate. break_even = 8.57 turns, just above K=8: this is not a
  designed margin, it's a coincidence of defaults.
- Same formula with the measured write price (**w=1.25**, ground-truth claim #3): upfront = 0.375·B,
  savings = +0.185·B > 0 → **fires for EVERY window ≥ min_hot_tail_tokens=1500** — i.e. ~2 verbose turns.
  The maestro compacts every other turn: a compaction call every ~2 turns, the user's just-said words
  immediately squeezed through a lossy summarizer, capsule churn on disk. Cost thrash + quality disaster.

So the live behavior is decided by a config constant nobody is watching (w), and BOTH attainable behaviors
are wrong. The root cause is the **proportional capsule** (S = ρ·B). Make the capsule a **constant budget**
(S ≈ 1–2k tokens — "ultra-compact" should mean a fixed-size state summary, not 30% of whatever the window
happens to be) and scale-dependence returns naturally:
`K·r·(B−S) > w·S + M` with S const → real threshold **B* = S + (w·S + M)/(K·r)**.
E.g. S=1.5k, w=1.25, K=8, M≈4k sonnet-eq → B* ≈ 1.5k + (1.9k+4k)/0.8 ≈ **8.9k window ≈ 11 turns** — a sane,
size-driven trigger. With K=20 (realistic for a partner session): B* ≈ 4.3k. Tune K, get laziness.

**2.2.2 The w = 2.0 vs 1.25 contradiction must be resolved, because the decision boundary sits exactly
between them.** cache_policy.py defaults and the documented config say 2.0 (= 1h-TTL write price);
ground-truth claim #3 says 1.25 (= 5m-TTL write price). These imply different TTLs, and the warm machinery
is internally inconsistent about which one is real: warm_session.py heartbeats at 59 min against a 60-min
TTL (`HEARTBEAT_INTERVAL_MIN=59`, `CACHE_TTL_MIN=60`) and records `ephemeral_1h_input_tokens` — that's the
1h cache, write price 2.0×. If the CLI is actually writing 5m cache (1.25×), the 59-minute heartbeat is
refreshing a cache that died 54 minutes ago and the warm pool is a placebo between tasks more than 5 min
apart. **One of (claim #3's 1.25, warm_session's 1h TTL) is wrong. Measure which, then set w from the
measurement.** This is a one-hour experiment with outsized consequences: it decides both the compaction
boundary and whether the keepalive layer does anything.

**2.2.3 M=0 is not a simplification, it's a first-order error.** The compaction call reads (W+S) and writes
S. The OUTPUT side dominates and everyone ignores it: 1.6k capsule tokens of haiku output = 1.6k×(5/3) ≈
2.7k sonnet-eq; at sonnet output prices the same capsule costs 8k sonnet-eq. With the thrash regime above
(compact every 2 turns), you pay a full compaction per 1.6k of window — the compactor costs more than the
carry it saves. Honest M makes the trigger lazy by itself. Corollary: the capsule must be SMALL not because
context is precious but because **capsule tokens are output tokens, the most expensive tokens in the
system** (5× input price at every tier).

### 2.3 Where to run the compaction call — in-fork vs fresh-haiku (a real fork in the design)

The design assumes a fresh bronze call. There's an alternative nobody priced: **run the ultra-compaction as
the LAST turn of the dying fork** — the window is already sitting in that session's cache, so the compactor
reads it at 0.10× instead of re-sending it fresh to haiku. The trade: in-fork pays the MAESTRO's output
price for the capsule; fresh-haiku pays haiku's cheap output but full cross-model fresh input (caches are
per-model — haiku cannot read sonnet's cache; warm pools are keyed per model in warm_session.py).

In-fork (maestro model m) beats fresh-haiku iff:
`0.10·(W+S) + S·out_m/in_m < (W+S)·in_h/in_m + S·out_h/in_m`
For sonnet maestro: 0.10·(W+S) + 5S < 0.333·(W+S) + 1.667S → **W > ~13·S**. With S=1.6k: W > ~21k.
For opus maestro: in_h/in_m = 1/15, out_h/in_m = 5/15 → fresh-haiku is almost free relatively; in-fork wins
already at small W on the input side but opus output (5× opus input) makes S brutal — keep S tiny.

Conclusion: **at the recommended operating point (fat window ≥ 20k, lazy rewind), in-fork compaction is the
cheaper path for a sonnet maestro, and it has a quality bonus: the compactor IS the maestro, with full
cached attention over the window it's summarizing — no cross-model hand-off loss.** Below ~20k windows,
fresh-haiku wins on price. Make it a computed choice, not an assumption. (This is the "better caching idea
we missed" the task asked for; the second one is §2.5.)

### 2.4 Tool-less maestro: the "wash" claim is a turn-1 artifact — attack sustained

Ground truth #3 compared 4,084 fresh vs 0.10×39,574 = 3,957 for ONE call. A partner maestro is multi-turn.
Add accumulated history W_hist to both arms:

- keep-tools-cached: 0.10·(39,574 + W_hist) per turn
- tools-empty, IF the cache-kill extends to the window (and ground truth #2 says cache_creation=0 on both
  calls — nothing was even WRITTEN, so there is no reason to believe conversation content caches either):
  4,084 + 1.0·W_hist per turn

| W_hist | keep-tools | tools-empty | ratio |
|---|---|---|---|
| 0 | 3,957 | 4,084 | wash ✓ (the measured case) |
| 8k | 4,757 | 12,084 | 2.5× worse |
| 24k | 6,357 | 28,084 | 4.4× worse |

The one-time 24,734×1.25 ≈ 31k creation spike repays in ~10 turns of any real history. So: **the "wash" is
true only for the stateless maestro the design explicitly rejected.** §0.A.1 does say "MEASURE: does the
rolling WINDOW still cache under tool-less?" — good instinct, but it's scheduled inside M2 while M1 builds
tool-less as the default. Wrong order: that micro-benchmark is one hour of work and should gate the M1
design, because if the window doesn't cache, tool-less-by-stripping-defs is not a lean choice, it's a 2.5–4×
cost regression plus full-price quadratic growth within each cycle.

The fix preserves Roberto's actual goals (maestro structurally can't execute; no 22k of tool-def bloat
blurring the boundary): **enforce tool-lessness by POLICY, not by stripping the defs that anchor the
cache.** Options in descending preference:
1. Keep a minimal tool set (even 1–2 harmless defs) purely as cache anchor if measurement shows that
   restores caching — the structural-safety property comes from permission mode / disallowed-tools, not
   from the defs' absence.
2. In `anthropic_api` cache mode (SDK, explicit `cache_control`), tool-less + cached window is likely fine —
   the `--tools ""` kill looks like a CLI heuristic, not an API property. So the maestro could be the one
   agent that prefers api-auth mode. CacheMode already models this split; use it.
3. Accept tools-empty ONLY if measurement shows the window caches anyway.

### 2.5 Other caching ideas worth stealing

- **Keep a verbatim tail across the rewind.** engine.py's `maybe_compact` clears the ENTIRE window —
  including the exchange that just happened. The user's last sentences exist only as lossy summary on the
  very next turn. Carry the last 2–3 turns verbatim into the new fork (the config already has an unwired
  `keep_recent_capsules=8` and `min_hot_tail_tokens` pointing at exactly this idea). Cost: w×~1.6k once per
  cycle. Buys: no conversational whiplash at cycle boundaries — the most user-visible failure mode of the
  whole scheme.
- **Multi-breakpoint laddering in api mode**: BASE | capsule | window as separate cache_control breakpoints
  (ttl 1h on BASE+capsule, 5m on window). Subscription CLI can't express this; the SDK can. One more reason
  the maestro may want `anthropic_api` mode even while workers ride the subscription.
- **Lazy rewind as an explicit policy, not an emergent one**: carrying D tokens of dead weight costs only
  0.10·D/turn. Rewind when 0.10·(W−S)·K_remaining clears w·S + M (the fixed trigger does this), and never
  on a schedule. "Fat window, lazy rewind, constant-size capsule" should be written down as the operating
  point — it is the regime where every number above favors the design.

---

## 3. ENCODER/DECODER: cut from core — but keep one narrow guard. Agree with the demotion, with sharper reasoning than the docs give.

The docs justify the cut with "the rolling window makes pre-compaction's edge thin." The stronger argument
is arithmetic: compact-BEFORE saves r×(raw − capsule) per future turn the raw text would have been carried —
at r=0.10, encoding a 1,000-token verbose turn down to 300 saves **70 effective tokens per future turn**,
while costing a haiku call (input + output, ≥ several hundred eff tokens) plus police escalations plus
irreversible info loss at the maestro's INPUT — the worst place to lose information, before the smartest
model in the loop has seen it. The encoder was designed for a world without a 0.10× cached window; in the
rolling world its economics are simply gone. Police (silver re-check on confidence<0.8) dies with it —
paying silver to verify haiku's compression of text the maestro could have just read is self-parody in the
new architecture. Glossary already dropped (correct; §9).

Keep exactly ONE inbound mechanism, and not as a default pipeline: a **size-trip guard** — when a single
human turn exceeds ~8–10k tokens (log paste, dump), file the raw to disk (capsule, addressable) and hand the
maestro a haiku summary + the address. That is the only regime where compact-BEFORE ever paid. It's 30 lines
in the io/ boundary, not a layer.

Decoder: D1 (worker output stays compact, forever) is right. The maestro→human expander is a FRONT concern —
the maestro is a capable model talking to a human; it can write prose natively. voice_match = cut from core,
fine as future paid toggle. Net: **encoder/police/glossary cut; decoder cut from core; one size-trip guard
in io/in.** The A/B (M2) then doesn't need to gate the encoder's death — the arithmetic already did; spend
the A/B budget on the questions that are actually open (window-caching under tool-less, maestro model tier,
trigger constants).

---

## 4. BLIND SPOTS

**4.1 The M1 prototype can't express the caching it was built to prove.** engine.py's
`ModelFn = (assembled_prompt) -> (response, tokens)` re-sends capsule+window+user as ONE flat string per
call. Prompt-prefix caching on the CLI/API operates on message-block boundaries; a single growing user
string is not an incrementally cached conversation. The real design ("turns accumulate IN this fork, cached
re-reads") requires session-continuation (`--resume <fork>` chaining), which this interface cannot express.
As a unit-testable skeleton of the control flow, fine; but the A/B must NOT run through this shape, or arm A
will pay fresh input for its whole window every turn and lose to baselines for a reason that has nothing to
do with the architecture. The wiring layer needs to be conversation-native before any measurement.

**4.2 Recursive summarization decay.** capsule_{N+1} = compact(capsule_N + window) is a telephone game;
decisions and constraints from cycle 1 fade by cycle 5 with nothing noticing. Disk capsules make it
recoverable in principle, but the tool-less maestro cannot read them (no Read tool — "read-by-address
later" in §0.A quietly contradicts §0.A.1; only the SYSTEM can inject capsule bodies). Mitigation: make the
rolling capsule a STRUCTURED schema, not prose — `{decisions[], constraints[], open_threads[], state}` —
where decisions/constraints are carried VERBATIM across cycles (append-only ledger, cheap: they're short)
and only the chatter is re-summarized. Lossy compaction for talk, lossless for commitments.

**4.3 Latency will disappoint before cost does.** Tool-less partner means every "what's in that file?" is a
delegated worker: subprocess spawn + cold process init + model round-trip — 5–15s where a tool-ful maestro
answers in 2. A partner that pauses 10 seconds to look anything up stops feeling like a partner. Plus the
compaction call lands mid-conversation at an unpredictable turn (in-fork compaction, §2.3, at least
parallelizes poorly with nothing — it blocks the next turn). Consider: pre-warmed investigation worker
(persistent bronze fork held open), and running compaction asynchronously AFTER responding to the user
(respond from the old fork, swap forks between turns — the rewind is invisible if it happens between turns,
not before the response).

**4.4 Subscription-quota economics are assumed, not measured.** All ground truth is monthly-plan CLI, where
marginal dollars are zero and the real currency is rate-limit headroom. The 0.10/1.25 weights are dollar
ratios; whether Anthropic's subscription limiter weights cache_read at 10% of fresh is undocumented and may
change. The design's autobalance-PRO already thinks in headroom — the cache math should state its currency
explicitly per auth mode (CacheMode is the natural home: dollar weights for `*_api`, measured-or-assumed
quota weights for `*_subscription`).

**4.5 The A/B as specced will not produce a trustworthy verdict.** One ~25-turn session per arm is N=1 with
confounds: different architectures produce different maestro replies → different user trajectories → you're
comparing two different conversations. Minimum fix: scripted/replayed user turns (fixed transcript both
arms), 3+ session replicates, token-level accounting from usage fields (not wall-clock or vibes), and a
quality rubric judged blind. Also don't change architecture AND maestro model in the same sweep without a
factorial layout — M2 currently lists both.

**4.6 Crash/concurrency holes in the rolling state.** The window lives in process memory (PartnerState);
a crash between compactions loses up to a full window of conversation that exists nowhere on disk (raw turns
should be journaled to disk as they happen — disk is free, this is the same "lossy only in hot context"
principle the design already espouses). Concurrent: maestro fork + N parallel worker forks all `touch()` the
same warm state file; last-writer-wins races are mostly benign (timestamps) but `prune_ghost` racing a live
fork is not — worth one lock.

**4.7 Provider-switch cold-starts.** Caches are per (provider, model). Autobalance swapping sonnet→codex
mid-session orphans the maestro's whole cached lineage; the bridge-capsule idea (PRO) covers workers, but
a maestro provider switch means re-paying BASE + capsule + window creation on the new provider. Fine if
rare — but autobalance must treat the maestro as sticky (switch only at cycle boundaries, where the rewind
already pays the re-warm anyway). Cycle boundaries are the natural — and free — provider-switch points;
nobody has written this down.

**4.8 `expected_future_turns` K is a static guess that decides everything.** K=8 vs K=20 moves the trigger
threshold by 2.5×. Estimate it live (rolling average of session lengths, or time-of-day priors); it's one
number, but the whole ROI hangs on it.

---

## 5. The ONE highest-leverage change

**Fix the rolling-recompact economics end-to-end before M1 ships: constant-size capsule budget (S ≈ 1.5k)
instead of proportional ρ·B, honest compaction cost M in the inequality, w set from a measured TTL test —
yielding a real size trigger (B* ≈ S + (w·S+M)/(K·r)) — and run the compaction in-fork with a verbatim
2–3-turn tail carried across the rewind.**

One change, because everything else in v1 survives contact with the numbers and this doesn't: as shipped,
the centerpiece either never compacts (w=2.0 — quadratic growth returns, silently) or thrashes every two
turns (w=1.25 — cost and quality churn). The fix is ~20 lines in cache_policy.py + maybe_compact, plus one
hour of TTL measurement, and it converts the design's central claim ("FLAT per turn") from accidentally
false to provably true at the fat-window/lazy-rewind operating point where rolling genuinely beats
keep-cached (sessions > ~22 turns, with TTL-death insurance as the bonus). Runner-up, do it the same week:
the 1-hour "does the window cache under `--tools ""`?" micro-benchmark, moved BEFORE M1's tool-less default
rather than inside M2 — §2.4 shows a 2.5–4× regression is the likely answer, and policy-enforced
tool-lessness (or api-mode cache_control) preserves every goal tool-stripping was meant to serve.
