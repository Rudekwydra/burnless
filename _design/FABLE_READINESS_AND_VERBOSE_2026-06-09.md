# FABLE — Readiness, Instruction Map, and the Verbose-Input Symmetry (2026-06-09)

Diamond analysis, d533. Grounded in code as of this date and in the corrected cost model
(`_design/FABLE_COSTMODEL_2026-06-09.md`): k=6 calls/agentic turn, maestro k_m=2,
cache_read R=0.10×, cache_write W=2.0× (ephemeral_1h), $/Mtok haiku 1/5, sonnet 3/15,
opus 15/75. Three parts: (1) what is missing for v1 to RUN; (2) where every instruction
lives and which are canonical; (3) whether the human-verbose INPUT end can be linearized
like the worker end, with arithmetic — deciding the encoder/telegrammer's fate.

---

## PART 1 — Functional readiness: what is missing for v1 to run end-to-end

### 1.0 What exists and its actual state

| Piece | File | State |
|---|---|---|
| Rolling engine (`partner_turn`, `partner_turn_session`, `maybe_compact`, `RollingCapsule`, `PartnerState`) | `src/burnless/maestro/engine.py` | Built, unit-tested (`tests/test_maestro_engine.py` incl. session-backend tests at L311+). **Wired to nothing.** Docstring says so: "ADDITIVE prototype (M1 of v1) — not wired into any command yet." |
| Conversation-native session (`MaestroSession` fork/continue/rewind, `MAESTRO_DISALLOWED`) | `src/burnless/maestro/session_runner.py` | Built, tested (`tests/test_session_runner.py`), validated live. **No production `RunnerFn` implementation exists anywhere** — the runner is an injected callable; only tests inject one. |
| Old SDK maestro (`run_maestro_turn`, `build_system_blocks`) | `src/burnless/maestro/core.py` | Live — this is what `burnless brain` (cli.py:1579 `cmd_brain`) actually runs. Anthropic SDK streaming, [THINK]/[CAPSULE]/[DELEGATE] protocol, O(t) history via `maestro_history.jsonl` + a 4th cache breakpoint. |
| Old subprocess maestro (`MAESTRO_SYSTEM_PROMPT`, `run_maestro`) | `src/burnless/maestro_runner.py` | Live — `burnless maestro` (cli.py:2444 `cmd_maestro`). Stateless one-shot telegram router. Different protocol entirely (one-line JSON). |
| Worker dispatch loop | `src/burnless/maestro/dispatcher.py` `run_all`/`run_delegate` | Live and healthy: parses delegate lines → spawns worker subprocess → extracts capsule → writes `capsules/<did>.json` + exec_log → returns capsule lines. Used ONLY by `cmd_brain`'s `run_one` while-loop (cli.py:1782). |
| Warm worker base | `src/burnless/warm_session.py` | Live, proven (fork cache_read ≈ 21,959 tok). Worker-flavored only. |
| New config spine (`Agent`, `CacheMode`, `DEFAULT_TIERS`) | `src/burnless/coreconfig/` | Built + tested, but imported only by `cached_worker.py` and `agents.py`. Everything else (cli, dispatcher, maestro/core, warm_session, routing) still resolves through legacy `config.py`. Dual config NOT reconciled. |

### 1.1 The precise gaps (why nothing runs on the new core today)

1. **No production `RunnerFn`.** `MaestroSession.send()` takes `runner: RunnerFn`
   (`(cmd: list[str]) -> dict` parsing `claude --output-format json`). engine.py L13-15:
   "the real implementations wire ModelFn to the claude warm-fork machinery… this module
   never touches LLM/network/subprocess itself." That real implementation was never written.
   It is ~20 lines (subprocess.run + json.loads), but it is load-bearing and absent.

2. **No production `CompactFn`.** `maybe_compact` needs a callable returning
   `{decisions, constraints, open_threads, summary}`. No haiku/bronze compaction call
   exists. (~30-50 lines: one haiku call with a JSON-output prompt + parse.)

3. **No maestro BASE session.** `MaestroSession.base_uuid` presumes a warm, cached,
   tool-defs-present maestro base to fork. `warm_session.init()` creates a *worker* base
   (worker brief, `--allowedTools` for execution, no maestro role). There is no
   `maestro_base_init()` that seeds a session with the maestro role +
   `--disallowedTools MAESTRO_DISALLOWED`. Without it, `--resume base_uuid` has nothing
   to resume.

4. **No command drives the engine.** Neither `cmd_brain` (old SDK path) nor `cmd_maestro`
   (old subprocess path) touches `partner_turn_session`. There is no `burnless chat`.

5. **Worker loop not connected to the partner engine.** Confirmed by code:
   `partner_turn_session` returns `response` (a string) and stops. The
   delegate-line→`dispatcher.run_all`→capsule→maestro-ingest cycle lives only inside
   `cmd_brain.run_one`'s `while True` (cli.py:1663-1794), built around the OLD
   `run_maestro_turn` result dict (`delegate_lines` key). The new engine has no
   delegate-line extraction and no result-ingest turn.

6. **PartnerState not persisted.** `RollingCapsule` cycles persist to
   `maestro/rolling/capsule_N.json`, but `window`, `pending_seed`, `fork_session_id`
   live in memory. Fine for a single REPL process; lost across invocations.

7. **Rolling compaction is opt-in and defaults OFF.** engine.py L96-97:
   `rolling_compaction_enabled: False` → "v1 default: never-compact". A live `burnless
   chat` without flipping this is the partner WITHOUT the linearization — quadratic again.

### 1.2 Minimal path to a working `burnless chat` (priority order)

**Needed to WORK at all:**

| # | Change | Size | Detail |
|---|---|---|---|
| 1 | `runner_claude_json(cmd) -> dict` — subprocess RunnerFn | **small** | subprocess.run, json.loads, return dict with `result`/`usage`/`session_id`. Strip ANTHROPIC_API_KEY like warm_session does. ~20 lines, new file or `maestro/runners.py`. |
| 2 | `maestro_base_init(model)` — warm maestro BASE | **medium** | Mirror `warm_session.init()`: `--session-id <uuid>`, iso-cwd, `--append-system-prompt <maestro role>`, tool defs PRESENT (no `--tools ""` — session_runner L29 comment: defs stay as cache anchor), persist uuid at `~/.burnless/warm/claude/maestro-<model>.json`. Reuse heartbeat/is_alive/prune machinery (parameterize the existing module rather than copying it). |
| 3 | `compact_fn_haiku(blob) -> dict` — production CompactFn | **small/medium** | One haiku `-p` call (or SDK) with a fixed compaction prompt → JSON `{decisions, constraints, open_threads, summary}` + fallback on parse failure (return `{summary: blob[:N]}` — never crash the turn). |
| 4 | `cmd_chat` — the glue loop | **medium** | New CLI command: build MaestroSession + PartnerState, REPL loop calling `partner_turn_session`; after each response, extract delegate lines (reuse `dispatcher.parse_delegates` regexes — they match plain `del …` lines, no [DELEGATE] block needed if the new role emits bare lines), call `dispatcher.run_all` **unchanged** (it is transport-agnostic), then feed the joined capsule lines back as the next `session.send(...)` user message with `source=worker_results`, depth-capped like cli.py:1775. This closes the maestro↔worker cycle on the new core. |
| 5 | Partner maestro role text | **small** | A slim role (see Part 2 conclusion) stating: tool-less partner, delegate-line grammar `del T<id> {tier} {action} {target} :: {spec}`, telegraphic spec rules, capsule-result ingestion. brain_role.md's [THINK]/[CAPSULE]/[DELEGATE] format is bound to `maestro/core._extract_block` parsing and must NOT be reused verbatim. |
| 6 | Config: flip `rolling_compaction_enabled: true` for chat + `maestro` section (model, base) | **small** | Without it the engine never compacts (gap 7). |

**Polish (works without):**

| Change | Size | Why deferrable |
|---|---|---|
| PartnerState persistence (window + pending_seed + fork id to JSON) | small | REPL session works in-process; persistence only needed for one-shot `burnless chat -m "..."` continuity. |
| coreconfig ↔ config.py reconciliation | medium/large | Dual config is ugly but functional — `cmd_chat` can read legacy `config.py` like everything else does today; migrate after chat works. |
| Decommission old paths (cmd_brain SDK loop, cmd_maestro, maestro_layer MCP, maestro_legacy backend) | medium | Old paths keep working in parallel; delete after A/B gate (TARGET_ARCHITECTURE §0.A gate). |
| Maestro keepalive on the new base | small | First hour is free anyway; warm daemon already exists to extend. |
| Usage accounting / metrics for chat turns | small | Observability, not function. |

**Single biggest thing missing:** item 4 — there is no glue. Engine, session, dispatcher
all exist and are individually tested, but no code path composes them: no RunnerFn, no
CompactFn, no maestro base, no command. The smallest end-to-end demo is items 1+2+4 with
compaction disabled (defer 3+6 one step) — that is a working partner-maestro driving real
workers; rolling then switches on with 3+6.

---

## PART 2 — Instruction map: where every instruction lives and what it is for

### 2.1 The full inventory

| # | Instruction | Location (file:symbol) | Layer | Purpose | Status |
|---|---|---|---|---|---|
| M1 | `MAESTRO_SYSTEM_PROMPT` ("You are MAESTRO, the conducting layer…", one-line-JSON router) | `maestro_runner.py:22` | maestro | System prompt for the stateless one-shot telegram router (`burnless maestro`). | **STALE** for v1 — superseded by the partner design; protocol (1-line JSON telegrams) incompatible with partner chat. Noted in TARGET_ARCHITECTURE §0.A.1 as bloated (3,672 tok measured). |
| M2 | `brain_role.md` (CRITICAL OPERATING DIRECTIVES + Burnless Brain + [THINK]/[CAPSULE]/[DELEGATE] format + telegraphic-spec rules + tier table) | `_design/maestro_v1/brain_role.md`, loaded by `maestro/core.py:104 build_system_blocks` | maestro | Role for the live `burnless brain` SDK path; cached system block ttl=1h. | **CURRENT for the OLD path**; the anti-hype directives, telegraphic-spec-writing section and tier table are the highest-value text in the repo and should be carried into the partner role. The [THINK]/[CAPSULE]/[DELEGATE] envelope is path-specific and dies with the old parser. |
| M3 | `MAESTRO_HARD_RULES` ("[MAESTRO ROLE — read this every turn]… escapulida") | `maestro_layer.py:17` | maestro | Per-message prompt for the MCP 3-layer-pipeline maestro (IDE-Haiku → mcp__burnless__maestro). | **STALE/DUPLICATED** — third maestro variant; the 3-layer MCP pipeline is not the v1 core. |
| M4 | Inline system "You are the Burnless executor for project…" | `cli.py:163` (`_run_with_maestro`, via `maestro_legacy.MaestroSession`) | maestro (misnamed) | System prompt for the `maestro_legacy` SDK *worker backend* (the misnamed non-maestro per TARGET_ARCHITECTURE §0.A). | **STALE/MISLEADING** — it is a worker-executor prompt living under a maestro name. |
| M5 | `MAESTRO_DISALLOWED` ("Edit,Write,Bash,…Read,Grep,Glob,LS") | `maestro/session_runner.py:8` | maestro | Tool blocklist enforcing tool-less-by-policy on the NEW partner (defs present as cache anchor; usage blocked). | **CURRENT** — part of the new core. Not a prompt; a flag value. |
| M6 | `DEFAULT_AGENTS["maestro"]` (`rules="never_execute"`, `tools=["delegate"]`) | `coreconfig/schema.py:137` | maestro | Declarative descriptor in the new config spine. | **CURRENT** (declaration only; nothing reads `rules` yet). |
| W1 | `worker_role.md` (CRITICAL OPERATING DIRECTIVES + Burnless Worker + capsule output shape + exec_log template + PART/BLK escalation) | `_design/maestro_v1/worker_role.md`, loaded by `maestro/dispatcher.py:330 _worker_system_prompt_payload` and `cached_worker.py:267 build_system_blocks` | worker | THE worker role on the dispatcher path (injected as `--system-prompt` or stdin-prepended) and the SDK CachedWorker path. | **CURRENT — canonical worker role.** |
| W2 | `"BURNLESS_WORKER_MODE_v1 You are a WORKER. The task spec… is your only rule. Do not read CLAUDE.md… Return the FINAL OUTPUT JSON envelope."` | `.burnless/config.yaml`, baked as `--append-system-prompt` into EVERY claude agent command (gold/diamond/silver-anthropic/bronze) | worker | Anti-CLAUDE.md-as-operator vaccine on the `burnless do/run` path (agents.run), where W1 is NOT injected. | **CURRENT but DUPLICATED** — overlaps W1 ("execute the spec, emit envelope") and W4 (near-identical text). Two different role texts can reach one worker depending on path. |
| W3 | `_FALLBACK_WORKER_ROLE` / `_FALLBACK_GLOSSARY` | `cached_worker.py:227/221` | worker | 2-line fallbacks when `_design/maestro_v1/` is absent (wheel install). | **CURRENT as fallback** — fine; by design tiny. |
| W4 | W0 warm brief: `"You are a Burnless worker invoked via claude -p --resume… Your role and behavior are determined entirely by the task spec… Execute the spec exactly as written and emit the result envelope."` | `warm_session.py:88 build_project_brief`, baked via `--append-system-prompt` into the cached warm BASE prefix | warm-base | The instruction every warm-forked worker inherits from the cached prefix. | **CURRENT — and it is nearly word-for-word W2.** This is the duplication to collapse. |
| W5 | `_QTP_F_FIXED_SUFFIX` (Output contract: JSON envelope fields) + `_TELEGRAPHIC_OUTPUT_HINT` (telegraphic output style) | `cli.py:263/271` | worker | Per-delegation suffixes appended to the spec text on the do/run path. | **CURRENT** — spec-level, not role-level; correct place. |
| W6 | `_build_cacheable_runtime_prefix` ("## Burnless Runtime Context… search likely project roots… do not return BLK solely because…") | `cli.py:300` (+ variant `cli.py:1127`) | worker | Cacheable per-project runtime context prefix (QTP-F). | **CURRENT.** |
| W7 | Natural-language preflight block ("## Natural Language Preflight… resolve operationally before BLK") | `natural_planner.py:plan_objective` | worker | Injected into conversational shell-originated tasks. | **CURRENT, niche** (shell path only). |
| E1 | `FEW_SHOTS` + glossary cached prefix (tone tags, capsule few-shots) | `codec/encoder.py:86` | encoder/codec | Haiku encoder: verbose human → capsule, used by `cmd_brain.run_one` (cli.py:1649) — **the PRE-telegrammer already live on the old path.** | Status decided by Part 3. |
| E2 | `STYLE_GUIDE` (capsule→output tone examples) | `codec/decoder.py` | encoder/codec | Haiku decoder: capsule → user-facing PT-BR, voice-matched. | Same fate as E1 (output side). |
| E3 | "You are the Burnless Police…" inline prompt | `codec/police.py:maybe_police` | encoder/codec | Sonnet verification of low-confidence encodings (confidence<0.8). | Same fate as E1. |
| E4 | `ENCODER_DECODER_SYSTEM_PROMPT` ("[BURNLESS PIPELINE ACTIVE — Haiku encoder/decoder role]") | `encoder_prompt.py:5` | encoder/codec | IDE-Haiku role for the 3-layer MCP pipeline (UserPromptSubmit hook). | **STALE** — pairs with M3; not the v1 core. |
| G1 | `glossary.md` (tiers, statuses, capsule grammar, abbreviations) | `_design/maestro_v1/glossary.md`, via `codec/glossary_loader.py`; consumed by maestro/core system block, dispatcher worker prompt, encoder/decoder/police | shared | The shared compression vocabulary; byte-identical cached block. | **CURRENT** while old path lives. TARGET_ARCHITECTURE §2 marks it "DROPPED" from the future maestro (telegrammer compaction covers it) — pending the same A/B gate. |
| P1 | `preamble.py` (`MORAL_BLOCK` empty placeholder + `_PAD` "[burnless-protocol-extended-reference]" + `system_prompt_with_suffix`) | `preamble.py` (pad duplicated from `chat_mode.py:52 _CACHE_PAD`) | warm-base/shared | Byte-frozen pad to clear the Haiku 2048-token cache floor; used by maestro_runner. | **CURRENT mechanically, STALE in purpose** — exists to pad M1's path; dies with M1. `_CACHE_PAD` exists in two files (deliberate copy-verbatim, still a duplication hazard). |

### 2.2 Conclusion: canonical set, deletions, the ONE standard

**Canonical to keep (one per layer):**
- **Maestro-role — ONE place:** a new slim `partner_role.md` (successor of M2), distilled
  from brain_role.md's three durable sections — anti-hype directives, telegraphic
  delegate-spec rules, tier table — plus the bare delegate-line grammar. Baked once into
  the warm maestro BASE (Part 1 item 2). Target ≤800 tok (vs M1's measured 3,672).
- **Worker-brief — ONE place:** `warm_session.build_project_brief` (W4) is the natural
  single home because it lives inside the *cached base prefix* every fork inherits — zero
  marginal cost, impossible to forget. Fold W2's two unique clauses (CLAUDE.md-is-not-
  operator; JSON envelope mandatory) into W4 — they are already ~90% identical — then
  delete the four `--append-system-prompt "BURNLESS_WORKER_MODE_v1…"` copies from
  `.burnless/config.yaml` once all worker paths fork warm (live_runner.py:341 and
  agents.py:620 already inject `fork_args`). W1 (`worker_role.md`) remains the canonical
  *long-form* role for the dispatcher/SDK paths; W3 stays as its wheel fallback.
- **Spec-level suffixes** (W5, W6, W7) stay where they are — they are per-task contract,
  not role, and correctly travel with the spec.

**Stale / duplicated — delete (after the A/B gate where marked):**
- M1 `MAESTRO_SYSTEM_PROMPT` + its pad plumbing P1 (dies with `cmd_maestro` one-shot path).
- M3 `MAESTRO_HARD_RULES` + E4 `ENCODER_DECODER_SYSTEM_PROMPT` (the 3-layer MCP pipeline pair).
- M4 the inline "Burnless executor" string in `_run_with_maestro` (rename/retire maestro_legacy as the worker backend it actually is).
- W2 config.yaml `BURNLESS_WORKER_MODE_v1` ×4 — after folding into W4 (above).
- M2 `brain_role.md` [THINK]/[CAPSULE]/[DELEGATE] envelope section — after `cmd_brain` is rewired (gate).
- E1/E2/E3 + G1: per Part 3 verdict below — encoder pipeline as a default layer dies;
  one compaction prompt survives in a different role.

**Why this matters functionally:** today a worker on the do/run path receives W4 (cached
prefix) + W2 (append) and the spec; a worker on the dispatcher path receives W4 + W2 +
glossary+W1 (system-prompt). Three role texts with slightly different output contracts
("capsule line" vs "JSON envelope") is exactly the "instructions confuse workers" failure —
the Bronze-Haiku edit-vs-execute drift feeds on contract ambiguity. One brief in the cached
base + one long-form role per path + contract in the spec is the standard.

---

## PART 3 — The symmetry calculation: can the verbose human-INPUT end be linearized, and does it pay?

### 3.0 Framing

The worker end is O(1) because each worker **forks a constant cached base** and never
carries the conversation; T turns cost T·C_worker (linear total, flat per-turn). The
maestro's history is the remaining O(t) term, and the human's verbose input V is its
biggest per-turn deposit. Two linearizers:

- **PRE (telegrammer/encoder)** — compact each verbose turn *before* it enters history.
  Already implemented and live on the old path: `codec/encoder.encode` (haiku, cached
  few-shot prefix, ~4× compression per its own docs), called per message in
  `cmd_brain.run_one`. History slope drops from (V+c) to (ρV+c). **Still O(t)** — a slope
  reduction, not a flattening.
- **POST (rolling recompact)** — already built: `engine.maybe_compact` +
  `cache_policy.should_compact`. Window accumulates raw; when ROI trips, ultra-compact to
  a capsule, rewind the fork. History is a **bounded saw-tooth: O(1)** per turn. This IS
  the symmetric counterpart of the worker trick — fork a constant base, carry only a
  capsule + bounded window.

So the symmetry question answers itself structurally: POST is the same trick applied to
the input end. PRE is a different, weaker lever. The numbers below quantify both.

### 3.1 Model and constants

Maestro = sonnet (S_in=3, S_out=15 $/Mtok), k_m=2 calls/turn, R=0.10, W=2.0.
base=5,000 tok (system+role). Maestro answer entering history c=300 tok/turn.
Human verbose V ∈ {400, 2,000, 5,000} tok/turn (light chat / verbose debate / giant paste).
PRE: haiku encoder, cached prefix 2,000 tok, compression ρ=0.25 (conservative vs the
encoder's claimed 2–5×); encode cost/turn = 1e-6·(0.10·2000 + V) + 5e-6·(0.25V)
= **$0.0002 + 2.25e-6·V**.
POST: with engine defaults (K=8 future turns, capsule S=1,500, compaction_cost M=4,000,
R=0.10, W=2.0), `should_compact` trips when 8·0.10·(B−1500) > 2·1500+4000 →
**window threshold B\* ≈ 10,250 tok**. Compaction call (haiku): reads B\*+1,500 ≈ 11,750
fresh ($0.0118) + writes 1,500 ($0.0075) = **$0.0193/cycle**; new-fork capsule re-write
2·1,500·3e-6 = $0.009/cycle → **$0.0283/cycle**. Cycles over T turns = Δ·T/B\*.

Per-turn maestro input cost: reads k_m·R·H(t)·S_in = 0.6e-6·H(t); writes W·Δ·S_in = 6e-6·Δ.
Output term (S_out·c·T) is identical across all variants and excluded.

### 3.2 (a) History-growth rate (the structural comparison)

| Regime | dH/dt (V=400) | dH/dt (V=2k) | dH/dt (V=5k) | Class |
|---|---|---|---|---|
| RAW verbose | 700/turn | 2,300/turn | 5,300/turn | O(t) history → O(T²) read cost |
| PRE only | 400/turn | 800/turn | 1,550/turn | **still O(t)** — slope ÷1.75–3.4 |
| POST only | bounded ≤ base+S+B\* ≈ 16.8k; avg H̄ ≈ 11.6k | same | same | **O(1)** |
| PRE+POST | same bound; compaction cycle 1.75–3.4× longer (e.g. V=2k: every 12.8 turns vs 4.5) | | | O(1), fewer compactions |

Key asymmetry PRE cannot touch: the maestro's own answers (c) and worker capsules also
accumulate; POST compacts *everything* in the window, PRE only the human side.

### 3.3 (b) Net $ over T=40 / T=100 (maestro input side + overheads)

Arithmetic shown for V=2,000, T=40 (others computed identically):
RAW reads: 0.6e-6·[40·5000 + 2300·(40·39/2)] = 0.6e-6·(200k+1.794M) = $1.196;
writes 6e-6·2300·40 = $0.552 → **$1.75**.
PRE reads: 0.6e-6·(200k + 800·780) = $0.494; writes $0.192; encoder 40·$0.0047 = $0.188 → **$0.87**.
POST reads: 0.6e-6·40·11,625 = $0.279; writes $0.552; cycles 2300·40/10,250 = 9.0 → $0.254 → **$1.08**.
BOTH: reads $0.279; writes $0.192; cycles 3.1 → $0.088; encoder $0.188 → **$0.75**.

| V | T | RAW | PRE only | POST only | PRE+POST |
|---|---|---|---|---|---|
| 400 | 40 | $0.62 | $0.45 | $0.52 | $0.46 |
| 400 | 100 | $2.80 | $1.84 | $1.31 | $1.16 |
| 2,000 | 40 | $1.75 | $0.87 | $1.08 | $0.75 |
| 2,000 | 100 | $8.51 | $3.63 | $2.71 | $1.87 |
| 5,000 | 40 | $3.87 | $1.68 | $2.14 | $1.28 |
| 5,000 | 100 | $19.22 | $6.98 | $5.34 | $3.21 |

Readings:
- **POST dominates PRE as T grows** (O(1) beats reduced-slope O(t)): at T=100 POST-only
  beats PRE-only at every V. At T=40 with large V, PRE-only edges POST-only ($1.68 vs
  $2.14 at V=5k) because POST still cache-writes the full verbose every turn AND pays a
  compaction every ~2 turns — but extend the session and POST wins anyway.
- **PRE+POST is complementary, not redundant**: under POST, PRE still (i) cuts the
  per-turn cache-write 6e-6·0.75V, (ii) stretches the compaction cycle 1/ρ-ish (V=5k:
  every 1.9 turns → 6.6 turns — 20.7 vs 6.1 compactions at T=40), which also means
  **less cumulative summarization loss on the POST side**. Combined saves 31–40% vs
  POST-only at V≥2k.

### 3.4 (c) Does PRE pay? Threshold and regime

Marginal PRE economics per turn, with POST already on:
- saves: write reduction 6e-6·0.75V + amortized compaction 0.0283·0.75V/10,250 ≈ **6.6e-6·V**
- costs: encoder **$0.0002 + 2.25e-6·V**
- net = 4.3e-6·V − 0.0002 > 0 ⟺ **V ≳ 47 tok** — pure-$ break-even is trivially low, BUT
  the absolute margin is small: $0.0015/turn at V=400, $0.0084/turn at V=2k, $0.021/turn
  at V=5k.

The decision is therefore **risk-adjusted, not raw-$**. PRE's failure mode is the
"emburrece" one: irreversible compression of the human's words *before* the maestro
reasons over them. One nuance lost → one misrouted/mis-specced delegation → one worker
re-dispatch ≈ $0.10 (bronze) to $0.90 (silver, per measured ~$0.84 post-mortems). At
V=400, PRE banks $0.15 per 100 turns — **a single bad compression per ~100–500 turns
erases the entire gain**. At V=5,000, PRE banks $2.10 per 100 turns and the content
(pasted logs, dumps, transcripts) is precisely what compresses safely — high redundancy,
low per-token semantic density.

**Risk-adjusted threshold: V ≈ 2,000 tok/turn.** Below it, PRE is negative-EV once error
risk is priced; above it, savings/turn ≥ ~$0.01 (≈4× the encoder call) and the input is
paste-like. Session length matters only in that the gain is linear in T — the threshold
itself is per-turn and T-independent.

### 3.5 (d) Recommendation

1. **POST (rolling recompact) = core, default-ON for `burnless chat`.** It is the true
   symmetric counterpart of the worker's O(1) trick and the only O(1) option. Flip
   `rolling_compaction_enabled` (Part 1 item 6). It alone captures 53–72% of the
   achievable saving at T=100.
2. **PRE (telegrammer/encoder): dies as a default pipeline layer, lives as a
   size-gated adapter.** Do NOT run every human turn through haiku (the current
   `cmd_brain` behavior — encode+police on "ok" is pure overhead plus risk). Auto-trigger
   only when the incoming turn exceeds ~2k tok (config knob, e.g.
   `encoder.min_verbose_tokens: 2000`): compress, file the RAW to disk, inject the
   compact form with a `[raw:<path>]` address so the maestro (or a worker) can fetch the
   original at 0.10× when nuance matters. This kills the every-turn tone-tag pipeline
   (FEW_SHOTS/police/decoder-in) while keeping the one regime where the math is solidly
   positive. The number that decides it: **at V=2k, T=100, PRE on top of POST saves $0.84
   (2.71→1.87, −31%); below ~2k/turn the per-turn margin (<$0.008) is smaller than the
   priced risk of one mis-compression per ~100 turns.**
3. **Mitigating "emburrece" even at V≥2k:** raw always on disk and addressable (the
   engine already persists capsules to `maestro/rolling/`; the encoder must do the same
   for raws); compression prompt biased to extract-verbatim (paths, IDs, numbers,
   quoted constraints carried literally — the `RollingCapsule.decisions/constraints`
   append-only-verbatim pattern at engine.py L38-39 is the right template); and the
   maestro told the capsule is lossy and where the raw lives.
4. **PRE+POST are complementary, not redundant** — but only in the paste regime. The
   tentative cut of the encoder was directionally right for the default path and wrong
   as an absolute: keep ~50 lines of it (haiku call + size gate + raw-to-disk), delete
   the rest (tone detection, police, every-turn invocation).

<!-- verify-keywords: telegrammer|PRE ; POST|recompact ; encoder -->

---

## Report (3 bullets)

1. **Biggest thing missing to run:** the glue — no production RunnerFn, no CompactFn, no
   warm maestro BASE, and no command composes engine + session_runner + dispatcher. All
   parts exist and are tested; ~4 small/medium pieces (runner ~20 lines, base-init
   mirroring warm_session, haiku CompactFn, `cmd_chat` loop reusing `dispatcher.run_all`
   unchanged) produce a live `burnless chat` end-to-end.
2. **Canonical instructions:** keep `worker_role.md` (+ tiny fallbacks) as the worker
   long-form, fold `BURNLESS_WORKER_MODE_v1` (config.yaml ×4) into
   `warm_session.build_project_brief` (they are near-identical) so the worker brief lives
   in ONE place — the cached base prefix; distill `brain_role.md` into a slim partner
   role as the ONE maestro text. Delete: `MAESTRO_SYSTEM_PROMPT` (maestro_runner),
   `MAESTRO_HARD_RULES` (maestro_layer), `ENCODER_DECODER_SYSTEM_PROMPT`, the
   maestro_legacy inline executor string, and the preamble pad plumbing that exists only
   to serve them.
3. **Verbose linearization verdict:** POST recompact is the true symmetry trick (O(1),
   default-on); PRE telegrammer is a slope-reducer that pays in pure $ from V≈50 tok but
   is risk-negative below **V≈2k tok/turn**. Encoder **dies as the default every-turn
   layer, survives as a size-gated giant-paste adapter** (raw to disk, addressable).
   The deciding number: PRE on top of POST at V=2k, T=100 saves **$0.84 (−31%,
   $2.71→$1.87)**; at V=400 it saves $0.15/100 turns — less than one mis-compressed
   delegation costs to redo.
