# Burnless — Consolidated Target Architecture (2026-06-09)

Blueprint for the core rewrite. Companion to `REWRITE_CONCEPT_2026-06-09.md` (which holds
the is/should map, decisions, and gap analysis). This doc is the TARGET: one diagram, one
module layout, the contracts, and where every existing piece lands. Source of truth for
sequencing the delegations. Roberto: "faz o alvo consolidado que se faltar algo eu pego rápido."

---

## 0. Identity (one line)

**Burnless = a front-agnostic delegation/orchestration ENGINE.** Core (rock-solid) +
optional toggle layers. A *front* (Claude Code today; Synapsis later) drives it. The engine
never assumes who is talking to it or how output is rendered.

---

## 1. The single keystone

Everything derives from ONE descriptor and ONE rule.

```
Agent = { name, role, provider, auth, model, tools, rules }     # workers AND maestro
CacheMode = derived from (provider, auth)                        # never hand-set per agent
```

**The rule:** change ONE config variable (a tier's `provider`/`auth`, or an autobalance
pick) → model + cache mechanism + warm pool + keepalive + cold-start size all follow, with
zero code edits. This is the cure for every "configs not consolidated" complaint.

Landed already (commit 45c81f3): `Agent`, `CacheMode`, `DEFAULT_AGENTS` (incl. maestro),
`DEFAULT_CACHE_MODES` (4 files), `resolve_agent`, `resolve_cache_mode`. Proven by tests:
flipping provider/auth flips the cache policy.

---

## 2. Runtime pipeline (target)

```
        ┌─────────────────────────────────────────────────────────────────┐
        │  FRONT  (pluggable, NOT core)                                     │
        │   #1 Claude Code + Haiku-encoder   #2 Synapsis (local-LLM term.)  │
        └───────────────┬──────────────────────────────▲──────────────────┘
                        │ human turn (verbose)          │ expanded answer
                        ▼                                │
   ┌────────── IO BOUNDARY (system, both ends) ─────────┴───────────────────┐
   │  IN: encode(human_turn) → (capsule, raw→disk)   [encoder = ONE adapter] │
   │  OUT: intercept(maestro_answer) → expander-out (decoder, maestro→human) │
   └───────────────┬────────────────────────────────────▲───────────────────┘
                   │ capsule only                        │ compact maestro result
                   ▼                                     │
        ┌──────────────── MAESTRO (Agent role=orchestrate) ─────────────────┐
        │  reads ONLY capsules + role; cwd isolated (no leak)               │
        │  (glossary DROPPED — telegrammer compaction covers it)            │
        │  routes (keyword→tier), emits delegate lines                       │
        │  [F] context-size monitor → auto-compact old capsules → disk hist  │
        └───────────────┬───────────────────────────────────────────────────┘
                        │ delegate lines
                        ▼
        ┌──────────── EXECUTION CORE (the ONE path) ────────────────────────┐
        │  run_delegation(id, root, cfg)  ← CLI, MCP, dispatcher ALL call it │
        │  parallel + jitter (anti-529)                                      │
        │   per task:                                                        │
        │     resolve_agent → [G] autobalance ranks providers by health      │
        │       → pick → resolve_cache_mode(pick.provider)                   │
        │       → [a] cold-start: if cache absent, inject sized padding      │
        │       → fork warm branch (--resume <uuid> --fork-session, /rewind) │
        │       → run worker                                                 │
        │     ## VERIFY gate (deterministic DoD re-run; OK→PART demote)      │
        │     OUT: system compresses worker result → capsule→disk            │
        │           (worker output STAYS COMPACT, never expanded)            │
        └───────────────┬───────────────────────────────────────────────────┘
                        │ events (per-token / suspect / done)
                        ▼
        ┌──────────── STREAM CHANNEL (already exists) ──────────────────────┐
        │  liveness JSONL inbox + maestro/streams/<provider> + live panel    │
        │  → FRONT subscribes → real-time worker WINDOWS, off chat history   │
        └───────────────────────────────────────────────────────────────────┘
```

---

## 3. Target module layout (clean packages)

Cure for "arquivos grandes, organização ambígua". cli.py 3073 + the 7 maestro files + 6 cache
files explode into purpose-named packages. Each package = one responsibility, small files.

```
burnless/
  config/        SINGLE SOURCE. Agent, CacheMode, TierDefinition, DEFAULT_*, resolvers,
                 cascade load (DEFAULT → ~/.config/burnless → project/.burnless).
                 (= today's coreconfig, renamed once legacy config.py retires)
  execute/       THE ONE execution core. run_delegation(), dispatcher (parallel+jitter),
                 verify gate, retry loop + bronze-rescue. (extracted from cli._cmd_run_body)
  providers/     autobalance (rank_providers, health scores, fallback), autodetect,
                 provider→command resolution. [G]
  cache/         cache_modes/ — provider×auth MATRIX (anthropic_subscription | anthropic_api |
                   codex_subscription | codex_api | gemini_subscription | gemini_api | none); key=f"{provider}_{auth}",
                 warm/ (session + codex), cold_start (per-model sizing) [a],
                 recycle (TTL/drift policy) [b], monitor (per provider/auth state) [E].
  maestro/       orchestrate (route, decide, emit delegates), isolation (no-leak flags),
                 context_compaction → disk history [F].
  io/            boundary: in/ (encode adapter PROTOCOL), out/ (compress+store),
                 stream/ (event channel the front subscribes to).
  capsule/       capsule model, compression modes (light|balanced|extreme), store/state,
                 disk-history (rolled-up maestro context).
  metrics/       counters, economy report (4-bucket), footer (tier/model badge).
  layers/        OPTIONAL TOGGLES (engine works with any off):
                   encoder_police/  (the default IN adapter; Haiku encode + police<0.8)
                   decoder_voice/   (expander-out; voice_match — maybe future paid)
                   privacy/         (capsule location + provider redaction; cost|redact|audit|opaque)
                   keepalive/       (idle ping daemon to hold warm cache)
  fronts/        ENGINE CONSUMERS (separate from core):
                   cli/      (Claude Code terminal)
                   mcp/      (desktop app MCP server — fix _run_sky to call execute.run_delegation)
                   synapsis/ (future local-LLM chat terminal with live windows)
```

---

## 4. Core vs toggle-layer classification

**CORE** (remove any → engine can't delegate): config · execute · providers/autobalance ·
cache (modes+warm+cold-start) · maestro · io boundary · capsule/store · metrics.

**TOGGLE LAYERS** (default-switchable, engine delegates fine without): encoder_police (it's
one IN adapter — a front could supply capsules another way) · decoder_voice · privacy ·
keepalive · compression `extreme`. Each obeys Roberto's rule "toda feature nasce com toggle".

Note the elevation: **cache moved from "maybe cut" to CORE** ("sem cache nada vale a pena").
The encoder moved the other way: from central to a toggle IN-adapter (decoupled per D4).

---

## 5. Contracts (interfaces to implement)

```python
# config/  — keystone (Agent landed; CacheMode to extend)
@dataclass
class CacheMode:
    name; module; mechanism; warm_module; keepalive; ttl
    cold_start_min_tokens: int        # [a] PER provider/model (Haiku 2048, Sonnet/Opus 1024) — kills the 1024/2048 split
    recycle: dict                     # [b] {trigger: "ttl"|"brief_drift", keep_hot_header: True}

# io/ — both ends, system-owned
class InAdapter(Protocol):            # encoder_police is the default impl; Synapsis another
    def encode(self, human_turn: str, project_root) -> tuple[Capsule, Path]: ...   # capsule + raw→disk
def intercept_out(worker_result) -> Path: ...        # system compresses → capsule on disk (worker stays compact)
def expand_for_human(maestro_capsule) -> str: ...    # decoder; ONLY maestro→human; voice_match optional
class StreamChannel(Protocol):
    def emit(self, event): ...        # liveness JSONL + provider stream
    def subscribe(self): ...          # front reads → live windows (off chat history)

# execute/ — the ONE path (CLI/MCP/dispatcher converge here)
def run_delegation(id, root, cfg) -> Result: ...     # gates: spec_validator + ## Verify, one set

# providers/ + cache/ — the convergence (the root-defect cure)
agent   = resolve_agent(name, cfg)
pick    = autobalance.rank(agent).best()             # [G] health-scored
cmode   = resolve_cache_mode(pick.provider, pick.auth)   # cache FOLLOWS the pick
cold_start_if_absent(cmode); warm = fork_branch(cmode)

# cache/monitor [E] + maestro/context_compaction [F]
warm_state = monitor.state(cmode)     # {alive, age, size, hit_ratio} per (provider,model,auth)
if should_compact(maestro_hot_tail, **cfg.cache_policy):   # [F] wire the orphan stub
    roll_old_capsules_to_disk(keep_recent_capsules)        # relieve maestro; keep hot header
```

---

## 6. Where each piece lands (migration map)

| Piece | Today (scattered) | Target home | State |
|---|---|---|---|
| Agent/CacheMode/Tier + resolvers | coreconfig/ | config/ | ✅ landed |
| execution core | cli._cmd_run_body (3073-line file) | execute/ run_delegation | ⚠️ extracted (execute_delegation), needs Stage B wiring |
| dispatch (4 paths) | cli do/run, maestro/dispatcher, mcp _run_sync(broken) | execute/ (one path) | ❌ unify; fix MCP crash |
| autobalance [G] | agents.py rank_providers/AutobalanceWorker | providers/ | ✅ works; rebind to Agent + carry cache_mode |
| cache modes | cache_worker/cache_prefix booleans + if provider hardcode | cache/cache_modes/ | ✅ registry landed; wire into execute |
| warm pools | warm_session.py / warm_session_codex.py | cache/warm/ | ✅ works; key by cache_mode |
| cold-start [a] | cached_worker 1024 + chat_mode 2048 | cache/cold_start | ❌ unify to per-model field |
| recycle [b] | implicit TTL + brief-drift | cache/recycle | ⚠️ make explicit/config/monitored |
| warm monitor [E] | metrics brain_cache_* counters | cache/monitor | ⚠️ per-(provider,auth) + feed decisions |
| maestro compaction [F] | cache_policy.should_compact (ORPHAN, 0 callers) | maestro/context_compaction | ❌ wire stub + disk history |
| IO in (encode) | cli + codec/encoder + police | layers/encoder_police via io/in Protocol | ⚠️ decouple as adapter |
| IO out (compress) | cli execute_delegation compress calls | io/out (system-owned) | ⚠️ consolidate |
| expander-out | codec/decoder.decode | layers/decoder_voice | ✅ exists; scope to maestro turns |
| stream/live windows | live_runner panel + liveness JSONL + maestro/streams | io/stream/ | ✅ infra exists; expose to fronts |
| verify gate | cli._apply_verify_gate | execute/ | ✅ keep |
| retry/bronze-rescue | cli inline | execute/ | ✅ keep |
| jitter | parallel_jitter | execute/ | ✅ keep |
| isolation/no-leak | maestro_runner flags (F4 gate) | maestro/isolation | ✅ keep; add 3 missing flags (see capsule) |
| privacy | config block only | layers/privacy | ⚠️ conceptual; build as toggle |
| keepalive | keepalive.py/warm_daemon.py | layers/keepalive | ✅ keep as toggle |
| glossary | codec/glossary_loader | config/ or capsule/ | ✅ keep |
| metrics/economy/footer | metrics.py + cli | metrics/ | ✅ keep |

---

## 7. Build sequence (anti-big-bang, each independently verifiable)

0. ✅ Foundation: Agent + cache_modes + resolvers (landed).
1. **Stage B — unify execute:** point MCP `_run_sync` + `maestro/dispatcher` at
   `execute_delegation` (one gate set; fixes the MCP crash). The "one path" win.
2. **Convergence wiring:** in execute, replace command-string model parse with
   `resolve_agent` → autobalance pick → `resolve_cache_mode` → so the provider choice
   carries cache policy (cures the root defect in LIVE behavior, not just tests). [G]
3. **cold_start field [a]:** per-model `cold_start_min_tokens` on CacheMode; retire the
   1024/2048 split.
4. **io boundary [c]:** consolidate encode-in (adapter Protocol) + compress-out (system) +
   stream-out into io/; encoder becomes layers/encoder_police adapter.
5. **maestro compaction [F]:** wire should_compact into the maestro turn loop + disk history.
6. **warm monitor [E] + recycle [b]:** per-(provider,auth) state feeding cold-start/recycle.
7. **Rename** config: coreconfig→config, retire legacy config.py; regenerate the 11 CLAUDE.md
   `<!-- burnless -->` blocks via the fixed render_block.
8. **Front decoupling (last):** keep CLI front working throughout; Synapsis is a separate
   project that consumes the engine + stream channel.

All hot-path delegations go through the FROZEN BUILDER (`bburn`) because the live install is
editable. `## Verify` gate on every code delegation.

---

## 8. Open holes — ANSWERED by Roberto (2026-06-09)

1. **Maestro multi-provider?** Per-agent config: each Agent is either FIXED (one provider, set once
   in config) or AUTOBALANCE (a providers pool). Independent per agent → any combo: worker-autobalance
   + maestro-fixed, both-autobalance, or both-fixed. (Agent gains `provider` xor `providers: [...]`.)
2. **Recycle [b] trigger for long batches?** DECIDED: worker fork-branches need NO size cap (immutable
   base, forks disposable) — they recycle on TTL / brief-drift / provider-switch (bridge). The only
   size-driven recycle is the MAESTRO context = [F] compact-to-disk. So: no separate batch size cap.
3. **Privacy opaque/audit encrypt-at-rest?** Optional; deferred. Layer concern, design later.
4. **Synapsis API vs CLI?** It WILL be an API (paid). CLI-invocation OK at first. Consequence:
   the paid API/Synapsis CANNOT ship on free PyPI → see §9 open-core split.

## 9. Roberto corrections — round 2 (2026-06-09, authoritative)

- **Glossary DROPPED from the maestro read-set.** Telegrammer (Haiku) compaction already covers what
  the glossary did. Glossary survives ONLY as an optional APPEND inside the encoder layer for privacy
  (vocab substitution) — a `layers/encoder_police` concern, never a maestro input. (Diagram fixed §2.)

- **Autobalance = a PRO feature, not free core. Corrected definition** (capsule autobalance-multi-
  provider-todo): pick the best provider FOR THIS USER NOW by `w_cost·cost + w_speed·latency +
  w_quality·failure`, where cost = cumulative month spend per provider and the key signal is
  **rate-limit HEADROOM left on each subscription tier of the account** (Roberto's "comparar os tiers
  mensais de assinatura disponíveis na conta"). On provider switch → **bridge capsule**: bronze writes
  a short prior-session summary reformatted for the new provider (context continuity without paying
  full input). Pending cross-provider BENCH before implementing. What's SHIPPED today (agents.
  rank_providers: success·0.6 + latency·0.4) is only a health-failover SEED, not the real feature.
  Target home: `providers/` for the free seed (fixed + simple health failover); the cost/headroom/
  quality scorer + bridge capsule live in the PRO layer (_pro/).

- **cache_modes = provider × auth MATRIX.** Not just codex single — every provider splits by auth:
  anthropic_subscription, anthropic_api, codex_subscription, codex_api, gemini_subscription,
  gemini_api, ... + none. Resolve key = f"{provider}_{auth}". (DEFAULT_CACHE_MODES must be expanded
  from today's 4 to the full matrix.)

- **Capsule = ONE compression model** (drop the multi-mode light/balanced/extreme as the capsule's own
  knob — keep a single canonical compression). PLUS an **emotion/salience WEIGHT** to bias how much a
  capsule matters. DECISION: the weight is a **Forgetless** concern (memory ranking = its BM25+
  PageRank+salience domain). Burnless capsule carries an OPTIONAL `weight`/`salience` field that
  Forgetless populates & ranks by; Burnless does not score it. Keeps burnless to one compression model.

- **Metrics follow the provider automatically.** Metrics keyed by (provider, cache_mode); change the
  provider → metrics re-key with zero code. Same single-source rule as everything else. Non-negotiable.

- **Open-core split (from hole 4).** Free core (MIT, PyPI): engine, fixed-provider + health-failover
  seed, CLI front. PRO (paid, private `_pro/`, NOT on free PyPI): full autobalance (cost/headroom/
  quality + bridge capsule), Synapsis API + front, multitenant. Mirrors the existing free.burnless.pro
  vs burnless.pro domain split. The engine exposes a stable API that the Pro/Synapsis side consumes.
