# Burnless instruction consolidation + rule re-evaluation — PLAN (2026-06-10)

Decisions from Roberto this session:
- **Public burnless = what Roberto uses.** The "how workers work / what to do / how to do it"
  doctrine is ONE source, public, and his personal layer reuses it (no separate copy).
- **Separate out:** forgetless / other less-family / user-specific instructions — these do NOT go in
  the consolidated burnless doctrine.
- **EVALUATE, don't just move:** many rules went redundant after this session's fixes; flag them.
- **Tiers = roles; models = user-configurable per level.** Ship ONE default config; user can put
  gemma in all levels or opus in all levels; (future) pick per task + save as default.
- No install/uninstall script now — just align the docs.

## A. Canonical structure (single source of truth)

| Layer | File | Role after consolidation |
|---|---|---|
| **Operating doctrine (THE source)** | `docs/DOCTRINE.md` (public repo) | All "how to use / how to make workers work" rules. Roberto's layer POINTS here, never duplicates. |
| Architecture | `PROTOCOL.md` | unchanged |
| CLI flags (verified) | `docs/COMMANDS.md` | unchanged |
| LLM operator manual | `docs/USING_BURNLESS_FROM_YOUR_LLM.md` | keep; dedupe tier table → link DOCTRINE |
| Roberto personal | `~/.claude/CLAUDE.md` + `~/antigravity/CLAUDE.md` (soul) | KEEP ONLY: profile, forgetless, less-family, the **fleet-remap** (his model choices), and a POINTER to DOCTRINE for operating rules. Strip duplicated burnless rules. |

## B. Tier→model reframe (kills the silver=haiku vs codex contradiction)

Rewrite DOCTRINE "Tiers" as: **tiers are ROLES, not models.** Each tier resolves to a
`provider + model` the user configures in `.burnless/config.yaml` (per level). Ship ONE default:

| tier | role | shipped default |
|---|---|---|
| gold | architecture / decisions | opus |
| silver | implementation w/ tight spec | sonnet |
| bronze | reads / summaries / ops / local code-draft | haiku **or** ollama-local gemma (tool-calling) |
| diamond | opt-in escalation, never auto-routed | — |

State explicitly: user may set ALL levels to one model (all-gemma, all-opus). Note the roadmap:
per-task config switch + save-as-default (Pro/Synapsis). DELETE every "silver=codex-5.4" / "silver=Haiku
empírico" contradiction; those become the user's fleet-remap in soul, clearly labeled "Roberto's choice,
not the burnless default."

## C. Rule-by-rule evaluation (KEEP / MERGE→DOCTRINE / DROP / FIX)

| # | Rule (where it lives now) | Verdict | Why |
|---|---|---|---|
| 1 | Commit working tree before delegating + worktree isolate (soul, ~/.claude, DOCTRINE) | **KEEP** (already in DOCTRINE #1) | still true; dedupe the 3 copies → DOCTRINE |
| 2 | `## Verify` deterministic gate + footguns (soul, ~/.claude, DOCTRINE) | **KEEP** (DOCTRINE #2) | core, current; dedupe copies |
| 3 | PART → reject + re-spec smaller (soul, ~/.claude, DOCTRINE) | **KEEP** (DOCTRINE #3) | current; dedupe |
| 4 | LLM auditor v0.8 retired (soul, ~/.claude, DOCTRINE) | **KEEP note, DROP version archaeology** | DOCTRINE already states it cleanly; drop the v0.8/v0.9/v0.9.1 timeline noise in soul |
| 5 | Tier cost ratios (bronze→silver 3.75×, silver→gold 5×) (~/.claude only) | **MERGE→DOCTRINE** | useful, currently orphaned in personal file |
| 6 | Bronze-Haiku: separate EDIT block from EXECUTE block (~/.claude only) | **KEEP + GENERALIZE→DOCTRINE** | still true for weak/local workers (bronze haiku/gemma); generalize to "local/bronze workers privilege edit over execute — separate the blocks" |
| 7 | Gold delegation output format (plan+spec+PROHIBITIONS in 1 roundtrip) (both) | **MERGE→DOCTRINE** | still valid; dedupe |
| 8 | `burnless brain` / `--capsule` references (.codex/commands, old soul) | **DROP (stale)** | brain retired → `burnless chat` |
| 9 | `burnless compress`/`decode` as live (README ## CLI) | **FIX→ deprecated** | deprecated this session (f03ec99) |
| 10 | chat_mode / natural_planner / maestro_runner / `--tools ""` mentions | **DROP (stale)** | modules deleted this session |
| 11 | "silver = codex-5.4 hoje" (soul line ~251) | **DROP (stale)** | contradicts the fleet table; codex left silver |
| 12 | Codex-out-of-silver incident narrative (soul) | **DEMOTE → capsule only** | historical reasoning, not an operating rule; keep in forgetless, out of DOCTRINE |
| 13 | "16× cheaper" claim | **VERIFY gone** | README already recalibrated; grep to confirm no live claim |
| 14 | Plugin Protocol v0.7 / 8-hooks / Brain-SDK as current | **DROP/mark historical** | per [[burnless-doctrine-dedup-protocol-canonical]] these were fiction removed from live docs |
| 15 | Engagement modes off/partner/on (README, USING_…; NOT in DOCTRINE) | **MERGE→DOCTRINE** | it's "how to use"; belongs in the canonical doc |
| 16 | ollama-local tool-calling worker (gemma=bronze) — NEW this session | **ADD→DOCTRINE** | shipped (c416376); document as a bronze option |
| 17 | cipher capsule + key custody | **note DEPRECATED→Pro/Synapsis** | f03ec99; so nobody re-documents it live |
| 18 | Agent-tool ≠ burnless-do (~/.claude) | **KEEP in personal** | Claude-Code-specific UX rule, not product doctrine → stays in ~/.claude |
| 19 | `BURNLESS_HARDCORE=1` gate, absolute PATH in config (~/.claude) | **MERGE→DOCTRINE** (ops section) | product behavior, currently personal-only |
| 20 | Forgetless rules, less-family map, autonomous burn-down loop, Roberto profile | **STAY personal** (soul/~/.claude) | not burnless doctrine per Roberto's split |

## D. Satellite drift fixes (mechanical)
- README `## CLI`: drop/mark `burnless decode`; fix config example `gold: sonnet`→`opus` (or label "example").
- `.codex/commands/burnless.md`: `burnless brain` → `burnless chat` (+ on/partner/off like .claude).
- `PAPER_2026-05-20.md`: add a one-line "historical draft — terminology 'Brain' = today's 'Maestro'" header; do not rewrite (it's a dated artifact).
- `~/.claude/CLAUDE.md`: fix stale `v0.7.4` version marker; replace the duplicated burnless rules block with a pointer to DOCTRINE; keep forgetless/profile/Agent-tool rule.
- soul (`~/antigravity/CLAUDE.md`): strip duplicated operating rules → pointer to DOCTRINE; keep fleet-remap (labeled "my choice, not default"), forgetless, less-family, loop.

## E. Execution order (after Roberto approves THIS table)
1. Update `docs/DOCTRINE.md`: reframe tiers (B), MERGE rows 5/6/7/15/16/17/19, keep 1-4.
2. Fix satellites (D) — repo files.
3. Slim soul + ~/.claude/CLAUDE.md to pointers + personal-only (rows 18/20 stay; rest point to DOCTRINE).
4. Capsule the demoted historical reasoning (row 12) if not already captured.
5. `public_git_check` + commit repo changes; soul/~/.claude are personal (Roberto edits or I do with his ok).
