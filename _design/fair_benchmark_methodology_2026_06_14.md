# Fair Benchmark Methodology — Replay vs Capsule, Burnless vs Sonnet-solo

**Date:** 2026-06-14
**Status:** design only (no code changed)
**Scope:** make `bench/replay_vs_capsule.py` a *fair* cost comparison, and define the
fair protocol for "Burnless vs Sonnet-solo".

## Core principle (non-negotiable)

**EQUAL RULES for both arms.** The benchmark must not advantage either side via
asymmetric setup. Whatever ratio results — even one that favors the baseline — is the
honest number. The goal is a defensible measurement, not a maximized Burnless figure.
If the only way to make Burnless win is to handicap the baseline, the correct conclusion
is that Burnless does not win at that N, and we report that.

---

## 0. The verified problem (grounded in real data)

`/Users/roberto/antigravity/burnless/bench/replay_vs_capsule.py` invokes the Claude CLI
once per turn with the whole prompt as a single positional argument:

```python
# bench/replay_vs_capsule.py:23-30
subprocess.run(["claude", "-p", "--output-format", "json", "--model", model, prompt], ...)
```

`run_replay` (line 62) packs the entire growing transcript into that one `prompt` string;
`run_capsule` (line 81) packs the compressed capsule list into it. In both cases the
conversation content is a **single user message** that changes every turn.

Real measured usage from `/Users/roberto/.burnless/test_data/20260614T003936Z/replay_vs_capsule.json`
(30 turns, Sonnet, subscription `claude -p`):

| arm | turn | input | cache_read | cache_create | output |
|---|---:|---:|---:|---:|---:|
| replay  | 1  | 4 | 17306 | 64217 | 1024 |
| replay  | 2  | 3 | 17306 | 23098 | 896 |
| replay  | 15 | 4 | 35262 | 52791 | 1004 |
| replay  | 30 | 3 | 17306 | 28711 | 1258 |
| capsule | 1  | 3 | 17306 | 22773 | 908 |
| capsule | 15 | 3 | 17306 | 23224 | 208 |
| capsule | 30 | 3 | 17306 | 23597 | 198 |

**The smoking gun:** `cache_read` is pinned at **17306 in every turn of both arms**. That
number is the fixed Claude Code system prompt + tool schemas — the only thing the CLI
caches. The actual conversation content (transcript or capsules) is always billed as
`cache_create`, **never** `cache_read`. `input_tokens ≈ 3` because the CLI counts almost
nothing as "fresh input" — it routes the user blob through a cache *write* every turn.

So both arms thrash identically on the part that matters (the conversation), and the
17306 system prefix is cached for both. The resulting 1.1–1.3× is an artifact of how much
each arm *writes*, not a measurement of cache reuse. A real append-only conversation would
get `cache_read` on its byte-stable prefix; this harness defeats that for both arms because
the CLI gives you no control over where the cache breakpoint lands inside your content.

---

## 1. The fairness contract

The benchmark holds **everything identical across the two arms except the one variable
under test**: whether prior turns are carried as *full replayed history* (replay) or as
*compressed capsules* (capsule). Concretely, identical across arms:

| Held constant | Value / rule | Why fair |
|---|---|---|
| **Model** | same model id, same arm-to-arm (e.g. `claude-sonnet-4-6`) | token prices and tokenizer identical; no price-mix advantage |
| **Persistent prefix `P`** | byte-identical system prompt + any tool context, same bytes in both arms | both pay one cache_write for `P`, then cache_read; neither gets a smaller `P` |
| **cache_control placement** | identical breakpoint policy: `cache_control` on `P`, and a breakpoint at the end of each arm's *stable prefix* | both arms get cache_read on whatever prefix is byte-stable; neither is denied caching |
| **TTL** | `{"type":"ephemeral","ttl":"1h"}` in both arms | identical eviction window; no timing advantage |
| **Output budget** | same `max_tokens` both arms (e.g. 600), and the *same deterministic task text* per turn | output is paid in full by both; the input-shape is the only difference |
| **Task** | identical per-turn instruction, deterministic (fixed seed / fixed prompt text), same turn count `N` | same work demanded of both; same `O_k` distribution |
| **Turn count `N`** | identical sweep (report the whole curve, not one N) | crossover is a property of the curve, not a cherry-picked point |
| **Timing/order** | both arms run back-to-back within one TTL window, warm-up call discarded | neither arm benefits from a colder/warmer cache than the other |
| **Token accounting** | same extractor, same price table (MATH.md §6) applied to both | dollars computed by one code path; no per-arm rounding |

**The single permitted difference:** the *content* of the carried history.
- replay arm: messages `1..k-1` are the full `User:/Assistant:` exchanges, byte-stable, cached.
- capsule arm: messages `1..k-1` are ~20-token capsules, byte-stable, cached.

That is the entire experiment. Replay carries more bytes in its cacheable prefix; capsule
carries fewer. Both get cache_read on what they carry. The question the benchmark answers is
exactly: **does carrying full history (more cache_read tokens + more cache_write on the tail)
cost more than carrying capsules, and at what N does the gap matter** — with no other lever
touched.

---

## 2. The cache decision: can the subscription CLI do this fairly?

**Short answer: No. The subscription `claude -p` path cannot produce a fair, symmetric
prefix-cache comparison for replay-vs-capsule. Use the Anthropic API with explicit
`cache_control` on BOTH arms.**

### 2.1 Why the CLI cannot do it (grounded, not guessed)

What `claude -p` *does* cache is demonstrated by the existing scripts and the real data:

1. **The CLI caches only its own fixed prefix.** `bench/cache_warm_check.py` sends eight
   different tiny prompts (`"diga apenas: A".."H"`) and still reports `cache_read`. The only
   byte-stable thing across those calls is the Claude Code system prompt + tools. That is
   exactly the constant `17306` we see pinned across all 30 turns of *both* arms in the real
   `replay_vs_capsule.json`. The CLI caches **its** prefix, not **your** conversation.

2. **Each `claude -p PROMPT` is a stateless single-shot.** The conversation you build in
   `run_replay`/`run_capsule` is jammed into one user message. There is no append-only
   message array across invocations, so there is no byte-stable *conversation* prefix for the
   provider to read — turn `k`'s user blob differs from turn `k-1`'s, so it is re-written
   (`cache_create`) every time. This is visible as `cache_create` tracking the transcript
   size in the replay arm (64217 → 52791 → 28711) while staying flat (~23k) in the capsule arm.

3. **The CLI gives you no `cache_control` breakpoint control over your content.** Anthropic
   prompt caching keys on a byte-identical prefix terminated by a `cache_control` marker
   (Anthropic API semantics; MATH.md §8). `claude -p` does not expose where that marker lands
   inside the prompt you pass — it manages caching for its own session/system layer. You
   cannot say "cache turns 1..k-1, leave turn k fresh." `bench/cache_invalidation.py` only
   ever exercises the *system*-prefix breakpoint (`--append-system-prompt`), never a
   user-content breakpoint, because the CLI has no flag for one.

4. **`--continue`/`--resume` does not rescue it.** A continued CLI session *is* append-only,
   so it would cache a growing prefix — but that only models the **replay** arm. The capsule
   arm requires a *different, compressed* message array, which you cannot express by continuing
   the same session (you cannot rewrite history into capsules mid-session). Using `--continue`
   for replay and single-shot for capsule would be an **asymmetric transport** — forbidden by
   §1. So the CLI forces asymmetry: it can fairly cache one arm or neither, never both with the
   same mechanism.

**Conclusion:** on the subscription CLI, the only byte-stable prefix is Claude Code's own
system layer, which is identical noise for both arms and tells you nothing about
replay-vs-capsule. The CLI cannot place the breakpoint that the fair test requires.

### 2.2 The fair alternative: Anthropic API with explicit `cache_control` (both arms)

The repo already has the correct mechanism in `/Users/roberto/antigravity/burnless/bench/run.py`:
it uses the `anthropic` SDK, sends a byte-stable `system` block with
`cache_control: {"type":"ephemeral","ttl":"1h"}` (line 189-196), maintains an append-only
`messages` array (scenario B, line 224-241), and reads exact `cache_read_input_tokens` /
`cache_creation_input_tokens` from `response.usage` (line 75-88). This is the only path that
lets us place identical breakpoints on both arms and read true cache_read on the conversation.

**The fair replay-vs-capsule is `run.py` scenario B (cached full replay) vs scenario C
(cached capsule)** — both already cached with the *same* `cache_control`. The missing piece
(see §3) is putting a breakpoint at the **end of the message history** so the conversation
prefix — not just the system block — earns cache_read in both arms.

### 2.3 Cost implication — flagged for Roberto's decision

The API path **burns paid credit** (`ANTHROPIC_API_KEY`), unlike the subscription CLI.
Rough order of magnitude at Sonnet prices (MATH.md §6: in $3.00, cache_write $3.75,
cache_read $0.30, out $15.00 / MTok), `N=30`, `P≈17k`, `O≈600/turn`:

- Per full benchmark run (both arms, one model, one N-sweep point): on the order of
  **$0.10–$0.50** for Sonnet; **5× that** if you also run Opus. The exact number falls
  straight out of the §3 reporting columns.
- A full curve (N = 2,5,10,20,30,50) over two models is still **single-digit dollars**.

**Decision required from Roberto:**
- **(A) Fair + paid (recommended):** run the API harness with `cache_control` on both arms.
  Costs a few dollars; produces the *only* honest replay-vs-capsule cache number. Stay on
  subscription for everyday work; spend credit only for this benchmark.
- **(B) Free + not-comparable:** keep `claude -p`. Then the doc must state plainly that the
  number reflects *cache-write volume only* (both arms denied conversation cache_read), is an
  **under-statement of the real gap** for a no-cache API and a **mis-statement** for a cached
  API, and must never be quoted as "the" replay-vs-capsule result.

There is no free path that is also fair for this specific comparison. Recommend (A).

---

## 3. IMPLEMENTATION SPEC

> Spec only. Do not implement as part of this delegation. This section tells the
> implementer exactly what to change and what is forbidden.

### 3.1 Target files

- **Primary:** `/Users/roberto/antigravity/burnless/bench/replay_vs_capsule.py` — convert
  from `claude -p` single-shot to the Anthropic SDK with append-only message arrays and
  explicit `cache_control`, OR retire it in favor of an extended `run.py`.
- **Reference (already correct mechanism):** `/Users/roberto/antigravity/burnless/bench/run.py`
  — reuse its `usage_dict` (line 75), `billed_cost` (line 91), `cached_system` (line 189).
- **Prices:** `/Users/roberto/antigravity/burnless/MATH.md` §6 is the single source for the
  price table; the harness must import/duplicate exactly those numbers and print them.

### 3.2 Required changes (replay_vs_capsule.py)

1. **Replace the transport.** Drop `call_claude(... "claude","-p" ...)`. Use
   `anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"]).messages.create(...)` with
   a byte-stable `system=[{type,text,cache_control:{type:"ephemeral",ttl:"1h"}}]` identical
   in both arms.
2. **Make both arms append-only.** Each arm maintains a `messages` list that only ever
   **appends**; never rewrite an earlier element (preserves the byte-stable prefix).
   - replay arm: append the full `{"role":"user",...}` / `{"role":"assistant",...}` exchange
     each turn (the real prior history).
   - capsule arm: append a `{"role":"user", "content": capsule}` (≤80 char / ~20 tok) plus
     the assistant capsule each turn.
3. **Place the SAME cache breakpoints in BOTH arms.** Anthropic allows ≤4 `cache_control`
   markers per request. Use the identical policy for both:
   - marker 1: on the `system`/`P` block (already in `cached_system`).
   - marker 2: on the **last message of the carried history** (i.e. the message just before
     the new turn `k` user message), so turns `1..k-1` are read from cache and only turn `k`
     is fresh. This is the change that gives the replay arm its rightful cache_read on the
     stable prefix — without it, replay is under-cached and the test is rigged *for* Burnless.
4. **Identical output budget.** `max_tokens` equal in both arms (default 600). Same
   deterministic per-turn task string (reuse `TURN_TASKS`, fixed, no randomness; if any
   sampling is added it must use a fixed seed shared by both arms).
5. **Record full per-turn columns for BOTH arms.** Extend `extract`/the row dict to persist,
   per turn and per arm: `fresh_input` (`input_tokens`), `cache_read`
   (`cache_read_input_tokens`), `cache_create` (`cache_creation_input_tokens`), `output`
   (`output_tokens`), and the per-turn `cost_usd` computed by **one** price function applied
   identically to both arms. Also persist `cumulative_cost`.
6. **Report honestly (§4 below).** Emit the four-column-per-turn table for both arms, the
   cumulative cost curve, and the crossover N (smallest N where cumulative replay ≥ cumulative
   capsule). Print the price assumptions block (MATH.md §6) verbatim.
7. **Run both arms inside one TTL window**, back-to-back, after one discarded warm-up call per
   arm, so neither arm sees a colder cache than the other.

### 3.3 HARD PROHIBITIONS (must be enforced / asserted)

- **No asymmetric advantage.** Any setting that differs between arms other than
  replay-content-vs-capsule-content is a bug. (model, `P` bytes, `max_tokens`, TTL, task text,
  turn count, price table, warm-up policy must all be identical.)
- **Identical `cache_control` both arms.** Same number of markers, same positions
  (system + end-of-history). It is forbidden to cache one arm's history and not the other's.
- **Same output budget.** `max_tokens` identical; same deterministic task. No arm may be given
  a shorter/cheaper task or a tighter output cap.
- **Deterministic task.** No `Math.random`/unseeded sampling that could give one arm easier
  turns. Fixed prompt text or fixed seed shared across arms.
- **One price path.** Cost for both arms computed by the same function and the same MATH.md §6
  table. No per-arm price overrides.
- **No CLI fallback masquerading as fair.** If `ANTHROPIC_API_KEY` is absent, the harness must
  refuse to emit a "replay vs capsule cache" number and exit non-zero (the subscription CLI
  cannot produce it per §2). It may run a clearly-labeled `--cli-uncached` mode that prints the
  §2.1 caveat, but must not write that into the comparable results.

### 3.4 Verify

```sh
test -f /Users/roberto/antigravity/burnless/bench/replay_vs_capsule.py || exit 1
grep -q "cache_control" /Users/roberto/antigravity/burnless/bench/replay_vs_capsule.py || exit 1
grep -q "ttl" /Users/roberto/antigravity/burnless/bench/replay_vs_capsule.py || exit 1
grep -q "cache_read" /Users/roberto/antigravity/burnless/bench/replay_vs_capsule.py || exit 1
grep -q "cache_creation" /Users/roberto/antigravity/burnless/bench/replay_vs_capsule.py || exit 1
grep -q "fresh_input" /Users/roberto/antigravity/burnless/bench/replay_vs_capsule.py || exit 1
grep -q "ANTHROPIC_API_KEY" /Users/roberto/antigravity/burnless/bench/replay_vs_capsule.py || exit 1
grep -q "crossover" /Users/roberto/antigravity/burnless/bench/replay_vs_capsule.py || exit 1
python3 -c "import ast,sys; ast.parse(open('/Users/roberto/antigravity/burnless/bench/replay_vs_capsule.py').read())" || exit 1
```

(Each check is one line, absolute paths. They assert the harness uses `cache_control` with a
TTL, records `cache_read` / `cache_creation` / `fresh_input` per turn, gates on the paid API
key, and computes a crossover. The implementer adds these tokens to the code; this Verify is
the acceptance gate.)

---

## 4. Honest reporting

Per turn, per arm, four token columns plus cost — the raw provider numbers, never collapsed:

```
arm=replay
turn  fresh_input  cache_read  cache_create  output  turn_cost  cum_cost
   1          ...         ...           ...     ...        ...       ...
```

Then:
- **Cumulative cost curve** for both arms (the curve shape is the claim, per MATH.md §2/§3).
- **Crossover N** = smallest N where `cum_cost(replay) ≥ cum_cost(capsule)`. If there is no
  crossover within the swept range, say so explicitly — do not extrapolate silently.
- **Price assumptions printed verbatim** from MATH.md §6 (Sonnet: input $3.00, cache_write
  $3.75, cache_read $0.30, output $15.00 / MTok; cache_write = 1.25×input, cache_read =
  0.10×input). State the model and date.
- **Separate the two questions.** (a) replay-vs-capsule is a *same-model* input-shape test.
  (b) Burnless-vs-Sonnet-solo additionally changes the *model mix* (Sonnet brain + tier
  workers vs Sonnet everywhere) — that is the §1 simulation regime in `VS_SONNET_SOLO.md`,
  not the same experiment. Report them as two tables; never quote the simulation's 16×/3.16×
  as a 30-turn measured result (the existing `VS_SONNET_SOLO.md` already warns against this).
- **State what the number is NOT.** At small N output dominates (paid identically by both
  arms), so the input-shape difference is a thin slice — the modest measured ratio is expected
  and must be presented with N, not as a headline.

---

## FAIRNESS HAZARDS

Every way this benchmark could accidentally rig the result — for **or** against Burnless —
and how the design prevents each.

| # | Hazard | Direction | Prevention |
|---|---|---|---|
| H1 | **Under-caching replay** (cache only the system block, let full history bill as fresh/write every turn — what `run.py` scenario B currently does). | rigs **FOR** Burnless | §3.2(3): place a `cache_control` breakpoint at end-of-history in *both* arms so replay's stable prefix earns cache_read too. |
| H2 | **Over-caching capsule / under-caching replay asymmetrically** (different number/position of breakpoints). | FOR Burnless | §3.3: identical `cache_control` markers, same count, same positions, both arms. Asserted by Verify (`cache_control` present, single code path). |
| H3 | **Using `--continue` for replay but single-shot for capsule** (asymmetric transport). | FOR Burnless | §2.1(4) + §3.3: one transport (SDK append-only) for both; CLI path forbidden in comparable results. |
| H4 | **CLI-only run quoted as the result** (both arms denied conversation cache_read; ratio = write-volume artifact). | distorts (mis-states real gap both ways) | §2 + §3.3: API key required or harness refuses to emit a comparable number; CLI mode is labeled `--cli-uncached` with caveat. |
| H5 | **Different model per arm** or model switching (cache shatters per endpoint, MATH.md §5.B). | either | §1: same model id both arms, fixed for the whole run. |
| H6 | **Different output budget / easier task for capsule** (smaller `max_tokens`, shorter prompt). | FOR Burnless | §3.3: identical `max_tokens`, identical deterministic task text both arms. |
| H7 | **Capsule arm given a smaller `P`** (e.g. trims tools/system). | FOR Burnless | §1: byte-identical `P` both arms; both pay the same one cache_write for it. |
| H8 | **Cherry-picked N** (report the single N where the gap looks best). | FOR Burnless | §4: report the whole N-sweep curve + crossover; no single-point headline. |
| H9 | **Cold cache for one arm** (run replay after a TTL gap so it pays cache_write; capsule warm). | either | §3.2(7): both arms back-to-back inside one TTL, one discarded warm-up each. |
| H10 | **Counting Burnless worker calls out / in inconsistently** in the Burnless-vs-Sonnet-solo table. | FOR Burnless | §4 + MATH.md §3.3 precedent: include nested worker `.burnless/logs/d*.log` usage in the Burnless total; compare total-to-total. |
| H11 | **Mixing simulation and live numbers** (quote §1 sim 16× as if measured). | FOR Burnless | §4: two separate tables, explicit "simulation vs measured" labels, per the existing `VS_SONNET_SOLO.md` warning. |
| H12 | **Output ignored, only input compared** (input is where Burnless wins; hiding output inflates the ratio). | FOR Burnless | §4: output column shown per turn for both arms; cost includes output paid identically by both. |
| H13 | **Non-deterministic task** (unseeded randomness hands one arm easier turns). | either | §3.3: fixed prompt text or shared fixed seed. |
| H14 | **Different price table per arm / stale prices.** | either | §3.3 + §4: one price function, MATH.md §6 verbatim, printed in the report. |
| H15 | **Idle eviction unmodeled** (long gap mid-run silently turns a cache_read into a cache_write). | either, hidden | §3.2(7) + MATH.md §8: run within 1h TTL; if a gap occurs, the cache_create column makes it visible rather than hidden. |

The dominant hazard is **H1** — it is the one the current code path actually commits, and it
biases *toward* Burnless. The fair design's central correction is therefore to cache the
replay arm's stable history exactly as well as the capsule arm's, and let the honest number
fall out.
