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

## 11. Next session entry point (resume here)
1. Refresh frozen builder if HEAD advanced; delegate via `bburn` from now on for hot-path work.
2. Build unified execution core: extract `cmd_run` logic → shared `run_delegation(id, root, cfg)`;
   make CLI, `maestro/dispatcher`, and `mcp_server` all call it (one gate set, one validator).
3. Fix MCP `handle_run` signature as part of (2).
4. Port callers of `config.py`/`routing.py` onto `coreconfig`; then rename `coreconfig`→`config`, retire old.
5. Pending Roberto-OK items (§8): regenerate stale `<!-- burnless -->` blocks in the 11 CLAUDE.md
   via the now-fixed render_block; finish global-config dedup.
