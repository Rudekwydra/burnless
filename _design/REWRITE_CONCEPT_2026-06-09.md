# Burnless — Concept & Rewrite Alignment (2026-06-09)

Status: **alignment draft** — map of what exists, what's broken, and the target
standard for the core rewrite. Source of truth for the rewrite. Not yet executed.

Decision locked with Roberto: **rewrite do core** (núcleo novo limpo, portando
peça por peça o que já funciona), not in-place refactor.

---

## 1. The pipeline — what happens, step by step

```
USER (raw natural language, PT-BR)
  │
  ▼  [ENCODER]  codec/encoder.py · Haiku · cache_control 1h
  │   reads: glossary (glossary_loader) + few-shots
  │   emits: capsule + confidence(0..1)
  │
  ▼  [POLICE]  codec/police.py · Silver · only if confidence < 0.8
  │   reads: raw msg + capsule + glossary
  │   emits: verified/corrected capsule
  │
  ▼  [MAESTRO]  maestro/core.py (in-process SDK)  OR  maestro_runner.py (stateless subprocess)
  │   reads: ONLY capsules + glossary + brain_role.md   (never raw text, cwd isolated)
  │   decides: tier (gold/silver/bronze) + emits "delegate lines"
  │   cache: 4 ephemeral breakpoints (glossary 1h, role 1h, recent capsules 5m, history tail 5m)
  │
  ▼  [DISPATCHER]  maestro/dispatcher.py
  │   parses delegate lines → spawns one worker per task (parallel, jittered)
  │
  ▼  [WORKER]  live_runner.py → subprocess (claude/codex)
  │   reads: worker_role.md (W0 brief, project-agnostic) + glossary + task spec + runtime ctx
  │          (NEVER conversation history — only its own task capsule)
  │   executes; returns capsule JSON {status, summary, files_touched, validated, evidence, issues, next}
  │
  ▼  [VERIFY GATE]  cli._apply_verify_gate  (deterministic, zero-LLM)
  │   re-runs the spec's "## Verify" shell block; demotes OK→PART on any failure
  │
  ▼  [COMPRESSION]  compression.py → writes .burnless/capsules/<id>.json
  │
  ▼  [DECODER]  codec/decoder.py · Haiku · cache_control 1h
  │   reads: capsule + glossary + style guide + voice sample (tone mirror)
  │   emits: natural prose back to user
  ▼
USER sees decoded text

CROSS-CUTTING:
  WARM/CACHE  warm_session.py + keepalive.py + warm_daemon.py  → keeps prefix hot (1h TTL, 59min heartbeat)
  CONFIG      config.py + cascade (defaults → ~/.config → project)  → resolves tier→model everywhere
```

---

## 2. Where each function lives today (current map)

| Surface | Live files | Dead / duplicate | Notes |
|---|---|---|---|
| **Encoder/Decoder** | `codec/encoder.py`, `codec/decoder.py`, `codec/glossary_loader.py`, `codec/police.py` | `encoder_prompt.py` (orphan), overlap with `compression.py` | round-trip works **in-session**; breaks cross-session |
| **Maestro** | `maestro/core.py` (chat brain), `maestro_runner.py` (stateless router), `maestro/dispatcher.py`, `maestro/session.py`, `maestro/counter.py`, `maestro/streams/*` | `maestro_legacy.py` (395 ln, default-off), `natural_planner.py` (never imported), `maestro_layer.py` (MCP-only legacy) | **2 live impls + 2 dead** |
| **Worker exec** | `live_runner.py` (1284 ln god-file), `agents.py`, `delegations.py`, `delegation_parse.py`, `dispatcher.py`, `spec_validator.py`, `parallel_jitter.py`, `cached_worker.py` (opt-in) | — | live_runner = 4 jobs in one file |
| **Warm/Cache** | `warm_session.py`, `warm_daemon.py`, `keepalive.py`, `cache_policy.py` | `warm_session_codex.py` (suspect-dead, never wired in CLI), `liveness.py` (not cache — I/O tracking) | 6 files for 1 concept |
| **Config/Tier** | `config.py`, `routing.py`, `profiles.py`, `provider_autodetect.py` | — | tiers in **19 files / 162+ refs** |
| **CLI** | `cli.py` (3047 ln god-file) | `cli.py.bak.*` (2 files, 223 KB, committed in src) | needs split into handlers |

---

## 3. The problems, named

### 3a. Tier is not single-source (your #1 pain)
- `config.DEFAULT_TIER_MODELS` is the *attempt* at one source, but **not enforced**.
- Tier **keywords** (routing.py), **priority/rank** (cli.py TIER_RANK, routing TIER_PRIORITY),
  **argparse choices** (cli.py ×2), **demote maps** (routing, live_runner), and **per-tier
  rebuild logic** (setup_wizard ×4 fns, provider_autodetect ×4 fns) are all *separate*.
- To change "silver = haiku" today you touch **~9 places**. That's the bug.

### 3b. The encoder/decoder "last part" that won't run (your discouragement)
Good news: it's **not deeply broken** — it's two unwired seams:
1. **Cipher keys are memory-only** (`cipher.py` `_MEMORY_KEYS`). v2 capsules reference a
   `key_id` but there's no key store → decode **fails after the process dies**. In-session OK,
   cross-session dead.
2. **`compress_transcript()` is orphaned** — the 3-layer compression (minify → Haiku → cipher)
   is built but never called from the hot path (only on-demand `cmd_compact`).
3. `GLOSSARY_SUPERBLOCK` / `CAPSULE_SUPERBLOCK` split (PROTOCOL.md §147) is designed, not built.
→ Fixing #1 + #2 is the smallest change that makes the round-trip work end-to-end.

### 3c. Two parallel Maestros + dead modules
`maestro_legacy.py` + `natural_planner.py` are dead weight. `maestro_runner.py` (stateless)
and `maestro/core.py` (interactive) are both live but duplicate decision logic.

### 3d. God-files
`cli.py` 3047 ln, `live_runner.py` 1284 ln. Hard to onboard, hard to reason about.

### 3e. Filesystem scatter (your new point — the real mess)
- **20 `.burnless/config.yaml`** across the HD, each with its own tier remap — including
  zombies: `semgit/` (archive), `.claude/worktrees/` (stale worktree), **Dropbox/CHARDON**.
- **TWO conflicting global configs**: `~/.config/burnless/config.yaml` (new XDG layer) AND
  `~/.burnless/config.yaml` (+ `.bak`). Ambiguous precedence.
- `~/.burnless/` mixes config + state + cache + instructions (`maestro.md`, 1.3 MB metrics,
  209 KB decisions cache, chats.db) in one dir.
- **11 instruction files** (CLAUDE.md / soul.md / AGENTS.md / GEMINI.md) each re-explain
  tier/maestro doctrine — divergent, some stale. **88 `.md`** mention "maestro".
  Workers auto-discover CLAUDE.md → read contradictory doctrine → confusion.

---

## 4. Target standard (the rewrite aims here)

### 4a. One config spine
```
burnless/config/
  schema.py     # TierDefinition{name, model, role, use_for, keywords, priority} + DEFAULTS
  resolver.py   # cascade: DEFAULTS → ~/.config/burnless/config.yaml → project/.burnless/config.yaml
                # public: resolve_model(tier), resolve_keywords(tier), resolve_priority(tier),
                #         resolve_route(text)  — EVERY caller goes through here, no literals elsewhere
```
- Change a tier in **one place**, propagates everywhere (your requirement).
- **Kill the duplicate global**: pick `~/.config/burnless/config.yaml` (XDG) as the only global.
  `~/.burnless/` becomes **state-only** (metrics, capsules, runs, warm) — no config there.
- Project configs become **minimal overrides** (only what differs from global), not full copies.

### 4b. One canonical doctrine doc (fixes the scatter)
- ONE worker brief (`worker_role.md`) + ONE maestro brief, **shipped with the package**,
  versioned. Project `CLAUDE.md`/`soul.md` **must not re-explain tier/maestro semantics** —
  they drift. They point to the canonical doc instead.
- A `burnless doctor` command audits the HD: stale `.burnless/` dirs, conflicting configs,
  CLAUDE.md files with inline tier doctrine → reports + offers cleanup.

### 4c. Clean module layout
```
burnless/
  config/    schema.py  resolver.py
  codec/     encoder.py  decoder.py  compress.py  glossary.py  _cipher.py(internal)
  maestro/   core.py  router.py  dispatcher.py  session.py  streams/
  worker/    executor.py  stream.py  ui.py  overflow.py  brief.py
  warm/      pool.py  keepalive.py  compaction.py        # 6 files → 3
  cli/       __init__.py  + one module per command group  # split the 3047-ln god file
```

### 4d. Kill list (immediate, safe)
- `src/burnless/cli.py.bak.20260521-224256`, `cli.py.bak.20260521-224917` (223 KB)
- `maestro_legacy.py` (395 ln), `natural_planner.py` (~180 ln)
- `~/.burnless/config.yaml.bak.*`
- Audit before delete: `encoder_prompt.py`, `warm_session_codex.py` (one map says orphan,
  another says used by tests — confirm wiring first).

---

## 5. Proposed sequence (rewrite, partner-paced)

0. **Snapshot + freeze** — commit working tree; tag current as `pre-rewrite`.
1. **Config spine** — `config/schema.py` + `resolver.py` (TierRegistry, single source, one global).
   *Unblocks everything; directly kills your #1 pain.*
2. **Codec round-trip rescue** — wire cipher key store + `compress_transcript` into hot path.
   *Morale win: the part you're stuck on starts working end-to-end.*
3. **Kill dead code** — .bak, legacy, natural_planner. *Instant clarity.*
4. **Split god-files** — cli → cli/, live_runner → worker/. *Onboarding becomes possible.*
5. **Consolidate maestro + warm** — one router/brain, 6 warm files → 3.
6. **HD hygiene** — `burnless doctor`, dedupe CLAUDE.md doctrine, minimal project configs,
   purge zombie `.burnless/` (semgit, worktrees, Dropbox).

Each step ported piece-by-piece against the current map (§2) so no hidden feature is lost —
the documented risk of a rewrite.

---

## 6. DECISION (2026-06-09): identity = core + optional layers

Roberto locked it: **burnless is the DELEGATION / ORCHESTRATION protocol.**
- **Core (must be rock-solid):** config/tier single-source · ONE dispatch path · verify gate · capsule trail · a Maestro that chains delegations.
- **Optional toggle modules (built ON the core, may be cut):** encoder/decoder, warm/cache, privacy levels (PROTOCOL.md §29 levels 1-3), redact/audit/burnkey.
- Consequence: the encoder/decoder that stalled Roberto is **no longer a blocker** — it's an opt-in module decided later. Grounded in his own prior calls: tier-routing = MoE not compression; compression is commodity (Synapsis pivot); Maestro = humble delegator.

## 7. FINDING: dispatch is fragmented (the real rot behind the `--force` bug)

There are **N divergent execution paths**, each with its own flag/gate/validator handling:
- CLI `burnless do` (argparse + hardcore gate + relative-path validator)
- CLI `delegate` + `run` (accepts `--force`; `do` does NOT — undocumented asymmetry; `do` forwards `--timeout`/`--stale-timeout-s` but not `--force`)
- `maestro/dispatcher.run_all()` (in-process, bypasses the CLI entirely — what the Maestro uses)
- `maestro_legacy.py` session backend (default-off)

**Requirement for the rewrite:** ONE execution core that both the human CLI and the
Maestro call. One flag model, one gate, one path validator. Flags + docs generated
from a single source (`--help` and `docs/COMMANDS.md` must not drift).

## 8. Progress log

- **2026-06-09** config-spine landed: `src/burnless/coreconfig/` (schema = single-source
  DEFAULT_TIERS; resolver = cascade + route, mirrors legacy without importing it).
  `tests/test_config_spine.py` **8 passed**. Additive — old config.py untouched; rename
  coreconfig→config when old retires.
- **2026-06-09** HD hygiene round 1: zombie `.burnless` dirs (semgit ×3 + Dropbox/CHARDON)
  moved to `~/.burnless_quarantine_2026-06-09/` (reversible via `restore.sh`); live project
  configs untouched. Reports in `_design/hd_hygiene_2026-06-09/` (config_inventory,
  global_config_reconciliation, doctrine_audit) — **proposals only, irreversible application
  gated on Roberto's review.**

### Pending Roberto OK (irreversible — do NOT auto-apply)
- Unify the two global configs (`~/.config/burnless` vs `~/.burnless/config.yaml`) per D3 proposal.
- Trim/fix inline burnless doctrine in the 11 CLAUDE.md/soul.md/AGENTS.md per D4 audit.
- Delete dead code (`cli.py.bak.*` DONE; `maestro_legacy.py`/`natural_planner.py` are WIRED — keep).

## 9. MCP is the 4th dispatch path (and `run` is broken)

burnless exposes itself to Claude via MCP (`mcp_server.py`, desktop app). Mapped:
- Tools: `delegate, route, run, capsule, read, status, maestro`.
- `handle_delegate` shares `routing.route()` with the CLI, but `handle_run` **reimplements**
  execution and calls `live_runner.run_with_overflow_retries()` with the **wrong signature**
  (3 args vs 8+) → **crashes at runtime**. The MCP `run` is effectively broken today.
- MCP **bypasses every gate**: no spec_validator (relative paths), no hardcore filter, no
  `## Verify` gate, no provider ranking. Hardcoded tier strings + `"bronze"`/`"haiku"` fallbacks.
→ Confirms §7: CLI + Maestro(dispatcher) + MCP must all call ONE execution core. The MCP
  fix is part of the unify-dispatch step, not a separate patch.

## 10. Build strategy: editable install ⇒ frozen builder

burnless is **editable-installed** (`_editable_impl_burnless.pth`) → every edit to `src/`
is live in the running `burnless`. Safe for additive/back-compat changes (done so far);
HAZARDOUS for the core-swap (the running tool imports half-edited hot-path files).

**Frozen builder** set up: `git worktree` at the last-green commit (`23cd308`) + copied-in
gitignored `_pro`, invoked via `/Users/roberto/antigravity/burnless-builder/bburn` (PYTHONPATH
to the frozen src). Use `bburn do "..."` to delegate the risky core-swap so the tool doing the
rewrite never moves under us. Refresh the builder to a new green commit between phases.
Version drift to fix on swap: dist-info says 0.6.3, code is 0.9.0.

## 10.5 RUNTIME control-flow Roberto wants (2026-06-09) — vs what exists
Most of this ALREADY EXISTS, scattered/hardcoded/unnamed (code more mature than roadmap).
Mapping his spec to reality + the real gaps:

| # | Desired runtime step | Exists? | Where | Gap |
|---|---|---|---|---|
| 1 | provider/llm set + gold/silver/bronze | YES | coreconfig Agent (landed 45c81f3) | — |
| 2 | check cache; if absent inject COLD-START sized to activate cache (per provider/llm/function) | PARTIAL | cached_worker.CACHE_MIN_TOKENS=1024 + padding to activate; chat_mode pad ≥1024 | hardcoded 1024 — NOT per-model (Haiku needs 2048) nor per-function; lives in 2 places. → make a `min_cache_tokens`/cold-start field on CacheMode, per provider/model |
| 3 | first worker job forks a branch for /rewind | YES | warm_session: `claude -p --resume <uuid> --fork-session` | already this |
| 4 | before worker: check cache; branch exists→same (parallels) else create; close/recycle policy | PARTIAL | is_alive (TTL+jsonl), fork_args reuses uuid, heartbeat TTL refresh, brief-drift→reinit | explicit recycle not deliberate/config/monitored. NOTE: fork-session base is immutable+cache-read-only, forks DON'T pollute base → parallels don't bloat; recycle trigger = TTL expiry OR brief-drift, not manual flush |
| 5 | the SYSTEM (not maestro/worker) writes disk; intercept on BOTH ends of maestro | PARTIAL | worker→done: execute_delegation calls write_summary+compress+write; human→maestro: codec/encoder.encode | concept exists but scattered in cli.py; not one clean I/O-interception layer. Human side intercept = the Encoder; worker side = the runner's compress. Consolidate into one layer |

THE THREE REAL GAPS to design: (a) cold-start sizing per provider/model/function (not a 1024
constant); (b) explicit, config-driven, monitored branch recycle policy; (c) one unified I/O
interception layer on both maestro ends (encoder in / compress out), instead of scattered cli calls.

### 10.5.E Warm-cache MONITORING → derives behavior per (provider/model/auth) (Roberto 2026-06-09)
PARTIAL. Exists: aggregate counters metrics.brain_cache_read_tokens / brain_cache_creation_tokens +
record_*_cache; warm_session.is_alive / needs_heartbeat (per model file). GAP: not keyed per
(provider,model,auth) and does NOT feed decisions — it's historical counting, not a live monitor.
TARGET: a monitor that, per resolved cache_mode, reports {alive?, age, size, hit_ratio} and feeds
resolve_cache_mode / recycle / cold-start decisions. Attaches to the Agent+CacheMode foundation.

### 10.5.F Context-SIZE monitor → auto-compact → disk history (relieve the maestro) (Roberto 2026-06-09)
ESSENTIALLY UNBUILT (only sketched). The math `cache_policy.should_compact` EXISTS (params in config:
expected_future_turns=8, min_hot_tail_tokens=1500, keep_recent_capsules=8) but has ZERO live callers —
an orphan stub (likely residue of desktop-version thinking, never wired). The maestro NEVER compacts
its history to disk today.
CONVERGENCE with the §10.5 #4 branch-recycle question: worker forks DON'T bloat (immutable base,
cache-read-only); it's the MAESTRO prefix that grows (one capsule per turn). So "recycle branch / keep
only hot header" APPLIED TO THE MAESTRO == F: each turn, monitor maestro hot-tail size → when over
threshold AND should_compact==yes → roll old capsules into a summary, write verbose to disk-history,
keep only keep_recent_capsules hot. This is "relieve maestro" + "hot header" in one mechanism.
Bonus evidence for gap (a): chat_mode pads to >=2048 ("safe for all models") while cached_worker uses
1024 — two cold-start numbers in two places → must become a per-model field on CacheMode.

### 10.5.G Autobalance (multi-provider) — IMPLEMENTED, not a sketch (Roberto 2026-06-09)
Real and working in agents.py: rank_providers() loads PERSISTED provider health, scores
`success_rate*0.6 + (1/norm_latency)*0.4`, sorts; AutobalanceWorker persists health; select_provider;
provider_health_snapshot (monitorable); automatic fallback to ranked[1] on retryable failure;
provider_autodetect.py detects installed CLIs. GAP: bound to the OLD shape (command-string + agents.
<tier>.providers[]) AND the provider pick does NOT carry cache policy — autobalance swaps anthropic
<->codex but cache does not follow (the root defect again). TARGET: autobalance = "rank an Agent's
provider candidates by health → pick → resolve_cache_mode(pick.provider) follows automatically." The
provider choice and the cache policy become THE SAME decision. Autobalance + cache_modes + Agent unify.

Other runtime components already present (the "que mais temos"): Encoder+Police (human→capsule+
confidence, police re-check if <0.8 = the human-side intercept) · Decoder (capsule→prose, voice_match) ·
Routing (keyword→tier) · `## Verify` gate · Retry loop + bronze-rescue · Parallel jitter (anti-529) ·
Keepalive daemon · Metrics/economy/footer · Compression modes (light/balanced/extreme) · Privacy modes
(cost/redact/audit/opaque) · Isolation/no-leak (worker cwd /tmp, F4 gate) · Glossary (shared compression vocab).

## 10.6 DECISIONS — front/IO/expander/privacy/encoder (Roberto, 2026-06-09)
- **D1 Worker output stays COMPACT, forever.** Worker returns a compact capsule; the SYSTEM
  stores it; NO expansion. The expander-out (LLM "expand for human") applies ONLY to
  maestro→human turns. Already exists: `codec/decoder.py::decode()`. Rewrite = scope it to
  maestro turns only.
- **D2 voice_match = NOT core.** Optional, maybe a future paid option. Default off; cut if it
  complicates. (Already an optional param of decoder.)
- **D3 Privacy modes = optional SEPARATE layer (toggle).** Affects WHERE capsules are written +
  HOW they are sent to providers. Conceptual until practiced. Not core; organized as a separable
  layer. (cost/redact/audit/opaque exist as config only today.)
- **D4 Encoder/front = DECOUPLE from core (defer the encoder/front decision).** Roberto distrusts
  the "Haiku-as-encoder just to keep the Claude terminal" hack. Resolution: the core must be
  FRONT-AGNOSTIC with a two-sided contract:
    - IN: "produce a compact capsule + raw-to-disk from a human turn" — Haiku-encoder is ONE
      implementation; a local-LLM standalone terminal is another.
    - OUT: "stream worker/maestro events on a channel" — ALREADY EXISTS: the liveness JSONL inbox
      (`live_runner._emit_suspect` → "future Maestro daemon"), `run_with_live_panel`/`_WatchRenderer`,
      and per-provider `maestro/streams/`. Any front subscribes to render real-time worker windows.
  Consequence: the Encoder stops being central → becomes a pluggable INPUT ADAPTER (front #1 =
  Claude Code + Haiku-encoder, kept working; front #2 = a local-LLM chat terminal = **Synapsis**,
  which consumes the same core + same event channel and gives "live worker windows WITHOUT writing
  to chat history" — Roberto's key requirement for this stage). Burnless = engine; Synapsis = front.
  Do NOT bake the encoder into the core. Trade-off: local-LLM front is more work + worse compression
  than Haiku, but is the only path to live-windows-without-chat-history.

## 11. Next session entry point (resume here)
1. Refresh frozen builder if HEAD advanced; delegate via `bburn` from now on for hot-path work.
2. Build unified execution core: extract `cmd_run` logic → shared `run_delegation(id, root, cfg)`;
   make CLI, `maestro/dispatcher`, and `mcp_server` all call it (one gate set, one validator).
3. Fix MCP `handle_run` signature as part of (2).

### 11.0 ROOT DEFECT — there is no unified "Agent" object (decided 2026-06-09, understand-first)
Read the full `config.py` surface. The decisive finding behind ALL of Roberto's
complaints (configs not consolidated, "change one var → propagate"):

THREE divergent mechanisms define who-runs-with-what, none ties cache to the agent:
- **Worker** (gold/silver/bronze): `agents.{tier}.command` is a SHELL STRING with
  `--model X` embedded; model is regex-parsed out (`_extract_model_token`). The
  "model variable" is buried in a command string, not a field.
- **Maestro**: a SEPARATE path — `preset` ("protocol"→Haiku/"direct"→off) +
  `encoder.model`/`maestro.model` via `resolve_layer_models`. Maestro is NOT
  modeled as a worker. (Roberto: maestro IS a worker — role=orchestrate,
  tools=delegate-only, rules=never-execute — and must resolve model+cache the
  same way.)
- **Cache**: `cache_worker.enabled`/`cache_prefix.enabled` = GLOBAL booleans +
  `if provider=="claude" else codex` hardcoded (`agents.py:609`). Not attached to
  any agent. `cache_policy.py` is MISNAMED (only compaction cost math, not runtime
  routing). cached_worker.py is Anthropic-LOCKED (`import anthropic`).

Consequence (answers "muda a variável e o cache segue?"): NO. Switching silver→codex
= edit the command string; cache policy does NOT follow (it's not on the agent) →
you LOSE the Anthropic cache path and fall to the codex warm branch. Not one variable.

Cache monitoring today: PARTIAL. metrics has `brain_cache_read_tokens`/
`brain_cache_creation_tokens` (maestro/brain side) + `keepalive_cache_renewed`.
Worker warm-pool hit/miss + cached_worker savings are NOT a mandatory monitored flow.

**TARGET (the rewrite foundation):** one `Agent` descriptor for workers AND maestro:
`{ name, role, provider, model, allowed_tools, rules }`. Cache is DERIVED from
provider via a single config table `cache_policy.<provider> = {mechanism, ttl, warm,
keepalive}`. Resolvers `resolve_agent(name)→Agent` and `resolve_cache_policy(provider)
→CachePolicy` that EVERY execution path + the maestro consult. Change `provider` →
model + cache mechanism + warm pool + keepalive all follow. Cache = first-class
monitored mandatory flow (hit/miss + saved tokens per agent, maestro included, in
`economy`/footer). Extends the `coreconfig` single-source spine already started.

Fragmentation evidence (file sizes): cli.py 3073 · live_runner.py 1284 · 7 maestro
files (maestro_layer/legacy/adapters/runner + maestro/core/dispatcher + natural_planner)
· 6 cache files (cached_worker/warm_session/warm_session_codex/keepalive/warm_daemon/cache_policy).

### 11.a Unify staging (decided 2026-06-09 — anti-big-bang)
Three divergent run impls: cli `_cmd_run_body` (full: backends+overflow+verify gate+retry),
`dispatcher.run_delegate` (parallel subprocess, no overflow/verify), mcp `_run_sync`
(calls `live_runner.run_with_overflow_retries(id=,burnless_root=,config=)` — WRONG sig → crash;
`handle_run` shells `burnless run`, OK). NOTE: extracted core named **`execute_delegation`**
(not `run_delegation`) — `burnless.__init__`/`core/delegations.py` already export a `run_delegation` stub.
- **Stage A (in flight, `bburn` b7cla7w6y):** extract `execute_delegation(opts: RunOpts, root=None)`
  from `_cmd_run_body`; CLI routes through it via thin adapter. Rename-only, behavior identical.
  Verify = import + signature + zero-`args`-in-body + run-path test suite.
- **Stage B (next):** point mcp `_run_sync` at `execute_delegation` (fixes the crash); converge
  `dispatcher.run_delegate` onto it (gains verify gate + overflow). One gate set everywhere.
4. Port callers of `config.py`/`routing.py` onto `coreconfig`; then rename `coreconfig`→`config`, retire old.
5. Pending Roberto-OK items (§8): regenerate stale `<!-- burnless -->` blocks in the 11 CLAUDE.md
   via the now-fixed render_block; finish global-config dedup.
