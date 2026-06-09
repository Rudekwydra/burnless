# Burnless v1 — build plan (2026-06-09)

The design is settled (see TARGET_ARCHITECTURE_2026-06-09.md). This is the buildable contract:
what v1 IS, scope in/out, and the milestone sequence. Anti-big-bang: each milestone is additive
or independently verifiable; hot-path via the frozen builder `bburn`; `## Verify` on every code delegation.

## What v1 IS (one paragraph)
Burnless v1 = a front-agnostic delegation/orchestration ENGINE. ONE config spine (Agent + CacheMode +
Tier), ONE execution core (run_delegation shared by CLI/MCP/dispatcher), ONE maestro engine
(partner: no tools, no skills, capable model, rolling rewind-recompact so context stays FLAT, routes
over indexes, delegates ALL execution+investigation to workers), workers that fork the warm cache and
load tools/skills on-demand per task, cache derived per provider×auth, skill/capsule indexes that are
derived + mtime-managed. Encoder/decoder/police/glossary are OPTIONAL layers (default off). Clean
package layout. The 4 redundant maestros and the misnamed maestro_legacy are gone.

## Scope
IN (v1.0):
- config spine (Agent/CacheMode/Tier single-source)            [DONE]
- cache_modes provider×auth matrix + per-model cold-start       [DONE]
- unified execution core (CLI+MCP+dispatcher → one path)        [B1 done; B2 pending]
- maestro engine: partner + rolling rewind-recompact, tool-less/skill-less, parameterized (model/tools)
- skill index (derived, mtime-managed) + worker skills on-demand
- collapse 4 maestros → 1 engine; maestro_legacy → execute/
- encoder/decoder/police/glossary → optional layer (default off)  [cut gated by A/B]
- clean package layout (config/execute/providers/cache/maestro/io/capsule/metrics + layers/ + fronts/)
- metrics keyed by provider/cache_mode

OUT (v1.x / Pro / separate):
- Synapsis front (paid, separate project) — engine stays front-agnostic, CLI is front #1
- full autobalance (cost/headroom/quality + bridge capsule) — Pro
- privacy modes (opaque/audit encrypt-at-rest) — optional layer, later

## Milestones (sequence)
- **M0 [DONE]** config spine · cache_modes matrix · cold-start · MCP unify (B1) · warm-via-registry.
- **M1 — maestro engine (ADDITIVE, the centerpiece).** New `maestro/engine.py`: partner loop with
  rolling rewind-recompact (BASE warm fork → accumulate → should_compact → ultra-compact capsule →
  re-fork BASE → read capsule), tool-less/skill-less, parameterized by Agent (model/tools/skills) so
  the A/B can sweep configs. Built ALONGSIDE existing maestros (not yet default) → zero break.
- **M2 — A/B measurement.** Realistic VERBOSE multi-turn session (no clean synthetic specs), both cached,
  throttled, ~20-30 turns. Sweeps: {partner+rolling no-encoder} vs {current encoder pipeline}; maestro
  model {sonnet, opus, sonnet+delegate-plan-to-opus}; "does window cache tool-less?". Decides: encoder
  cut y/n, maestro model, tools y/n. Output = numbers, not guesses.
- **M3 — consolidate execution + maestros.** B2: extract shared execute primitives (verify gate +
  compress + worker runner) used by both CLI core and dispatcher. Route ALL maestro entries (chat,
  one-shot, mcp, shell) to the M1 engine. Delete maestro_runner / maestro_layer / natural_planner;
  move maestro_legacy → execute/ (rename, it's a worker backend). Re-validate F4 no-leak + cache.
- **M4 — skills.** skill_index (derived, mtime-drift rebuild, no LLM) + maestro routes over it + worker
  forks enable the needed skill on-demand.
- **M5 — layer demotion + layout.** Per M2: move encoder/decoder/police/glossary to layers/ (default off)
  or cut. Reorganize into the clean package layout. Rename coreconfig→config; retire legacy config.py.
- **M6 — finalize.** Regenerate doctrine/CLAUDE.md render_block blocks; version bump; dist-info fix → v1.0.

## Build rules
- Additive-first: build new modules alongside old; switch default only after A/B/verify; delete last.
- Hot-path delegations via `bburn` (editable-install hazard). `## Verify` gate every code spec.
- Commit working tree before each delegation; PART → reject + re-spec smaller.
- The A/B (M2) is the gate: nothing that loses to the realistic-verbose baseline ships.
