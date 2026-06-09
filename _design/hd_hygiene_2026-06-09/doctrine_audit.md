# HD Hygiene — Burnless/Maestro Doctrine Audit

**Date:** 2026-06-09 · **Mode:** read-only report, ZERO edits · **Delegation:** d518 (gold)
**Ground truth used:** `src/burnless/config.py`, `src/burnless/cli.py`, `docs/COMMANDS.md`, `PROTOCOL.md`, `src/burnless/__init__.py` (`__version__ = 0.9.0`).
**Rule applied:** when an instruction file disagrees with CODE / COMMANDS.md / PROTOCOL.md, **the code wins**.

---

## 0. CANONICAL CURRENT TRUTH (from code, not memory)

| Fact | Source of truth | Value |
|---|---|---|
| Version | `src/burnless/__init__.py` | **v0.9.0** |
| Default tier→model | `config.py DEFAULT_AGENTS` + `DEFAULT_TIER_MODELS` | **gold=opus (`claude-opus-4-8`), silver=sonnet (`claude-sonnet-4-6`), bronze=haiku (`claude-haiku-4-5`)** |
| `diamond` tier | `config.py _normalize_legacy_tiers` | **NOT a default tier.** Opt-in escalation only — never auto-routed, reachable only via `--tier diamond`. Legacy diamond w/ same cmd as silver collapses into silver. |
| Compression knob | `COMMANDS.md` + `do --help` | **`--mode {light\|balanced\|extreme}`** (NOT `--timeout`). `light` = anti-phantom default; all active projects set to `light` (2026-05-28). |
| `--timeout` / `--stale-timeout-s` | `COMMANDS.md` | accepted by `do` (since **v0.9.0**) and `run`; **NOT** by `delegate`. |
| Worker verification | `cli.py _apply_verify_gate`, `delegation_parse.extract_verify_block` | **`## Verify` deterministic shell gate** re-runs DoD checks post-worker, demotes OK→PART on failure. Zero-LLM. LIVE. |
| LLM auditor (v0.8) | `cli.py` (only verify-gate present) | **RETIRED.** No live LLM-judges-LLM auditor. Any text describing it as live is STALE. |
| Exit codes | `cli.py` | `run` returns 0 only on worker `OK`, else 1; `do` propagates. Never pipe `do/run` through `tail` (masks exit code). |
| Worker read scope | `COMMANDS.md` | workers run `--permission-mode bypassPermissions` → CAN read outside project root. |

**The single biggest live contradiction:** several files say **silver = Haiku** and **gold = Sonnet**. The CODE default is **silver = Sonnet, gold = Opus**. The "silver=Haiku" mapping is a **per-project `config.yaml` remap** (nutri, decided empirically 2026-05-16) — legitimate *for that project only*, but it has leaked into global/default doctrine where it is wrong.

---

## 1. PER-FILE EXTRACTION + FLAGS

### A. `/Users/roberto/.claude/CLAUDE.md` (GLOBAL, auto-loaded everywhere)
Burnless content: tier-selection table (bronze/silver/gold/diamond by *spec quality*, no models — OK), command list (`do/route/metrics/status`), `## Verify` workflow doctrine (lines 68–75, **current & correct**), extras (post-mortem, hardcore filter, PATH-absolute, gold output format). Header `<!-- burnless version: v0.7.4 -->`.
- **STALE — line 33:** "Auditor burnless quebra em outputs >100k raw_tokens ... Fix em flight (d271)" + `[[feedback-burnless-auditor-large-output-bug-2026-05-18]]`. The LLM auditor is **retired**; this warning describes a dead subsystem as live. Internally contradicts its own lines 68–75 ("Auditor-LLM v0.8 segue aposentado").
- **STALE — line 37:** version stamp `v0.7.4` (code is v0.9.0).
- **CORRECT:** the `--mode` vs `--timeout` note (line 43) and `## Verify`-first workflow match COMMANDS.md/code. This file is the **most current** doctrine of the set.

### B. `/Users/roberto/antigravity/CLAUDE.md` (soul-style operator guide, auto-loaded in `~/antigravity/`)
Largest doctrine surface. Already **points to PROTOCOL.md for architecture** (line 116 — good) and **COMMANDS.md for commands** (line 113 — good). But carries a full **tier→model table (lines 137–144)**:
- **CONTRADICTS CODE — line 141:** `silver = **Haiku**`. Code default silver = Sonnet.
- **CONTRADICTS CODE — line 140:** `gold = Sonnet / Opus`. Code default gold = Opus.
- **MISLEADING — line 139:** lists `diamond` as a standard band; code treats diamond as opt-in-only, never auto-routed.
- Line 144 *does* say "config.yaml REMAPEIA tiers — sempre cheque" (mitigates), but the table presents the nutri remap as if it were the default. Reads as global truth, isn't.
- Also duplicates worktree-isolation, PART-rejeita, and `## Verify` doctrine at length (lines 330–425) — same content as `~/.claude/CLAUDE.md` and nutri. Heavy triplication.

### C. `/Users/roberto/antigravity/calibration/soul.md`
**No burnless/maestro/tier doctrine.** Pure behavioral calibration (anti-hype). Not a doctrine source — out of scope, nothing to reconcile.

### D. `/Users/roberto/antigravity/fw-social/CLAUDE.md`
Entire file = the auto-generated `<!-- burnless:start -->` block, `v0.7.4`.
- **STALE — version stamp v0.7.4.**
- **SUPERSEDED — workflow rule #2:** "Audit DoD point-by-point after worker returns OK — don't trust status=OK blindly". Replaced by the deterministic `## Verify` gate (zero-LLM, the runner does it). No mention of `## Verify` at all.
- **MISLEADING:** tier table lists `diamond` as a routing signal (it's opt-in-only).
- No `--mode`/`--timeout` guidance. Pure stale boilerplate.

### E. `/Users/roberto/antigravity/fw-social-next/CLAUDE.md`
Same stale `v0.7.4` burnless block as D (identical text) → same flags. **Plus** genuinely project-specific, current sections (codex OAuth recurrence, "Criar com IA" pauta pipeline) that are **fine and must stay**. Only the generated burnless block is stale.

### F. `/Users/roberto/antigravity/nutri/CLAUDE.md`
- **Tier table (lines 13–20): silver=haiku, bronze=haiku, gold=opus, diamond=codex5.5.** This is a **legitimate per-project `config.yaml` remap**, documented as empirical (2026-05-16, 3 codex incidents). Code explicitly supports per-project overrides → **NOT stale, valid for this project.** It already points to COMMANDS.md (line 7) and states `--mode` vs `--timeout` correctly.
- **Duplication:** re-explains worktree-isolation, PART-rejeita, `## Verify` footguns at length — same doctrine as B and `~/.claude`. Project-specific lessons (auth/DB/deploy/`next build` gate/zsh `rm` paren bug) are valuable and unique → keep those.

### G. `/Users/roberto/antigravity/burnless/AGENTS.md`
Repo-hygiene only (public-vs-local, release scripts, vocabulary). **No tier/maestro doctrine.** Correct and current. Out of scope.

### H. `/Users/roberto/antigravity/burnless/GEMINI.md`
"Read AGENTS.md first" + hygiene echo. No tier doctrine. Correct pointer pattern — already a model of what the others should be.

### I. `/Users/roberto/antigravity/burnless/soul.md` (injected at start of every `burnless chat`)
Tier table gold/silver/bronze (**no models**, conceptual — OK), keyword routing, command list.
- **COMMAND DRIFT vs COMMANDS.md:** lists `burnless plan` and `burnless log` (no id); omits `do`, `route`, `capsule`. COMMANDS.md canonical core = `init/route/delegate/run/do/read/capsule/log dXXX/status/metrics`. `plan` is not in the canonical core lifecycle.
- **OK in context:** "delegations são síncronas, worker timeout 900s, BLK on excess" — accurate for chat mode. `diamond` omitted (consistent with it being opt-in-only). This is a distinct artifact (chat injection), so it stays — but its command list should track COMMANDS.md.

### J. `/Users/roberto/.../chardon_catalog/CLAUDE.md`
Same stale `v0.7.4` auto-generated burnless block as D (identical) → identical flags: stale version, superseded "Audit DoD point-by-point", diamond-as-routing-signal. No project-specific content beyond the block.

### K. `/Users/roberto/antigravity/burnless/CLAUDE.md`
**Does not exist.** (No conflict.)

---

## 2. ROOT CAUSE OF THE DRIFT

`src/burnless/claude_integration.py: render_block(version, project_name)` injects the `<!-- burnless:start --> ... <!-- burnless:end -->` doctrine into every project's `CLAUDE.md` at `burnless init`. Files D/E/J are frozen copies from a **v0.7.4** init and never re-rendered, so:
- the version stamp is stale, and
- the block still ships the **"Audit DoD point-by-point"** rule, which the `## Verify` gate superseded.

Because the doctrine is *inlined per project*, every project is an independent copy that drifts. The fix direction is structural: the generated block should be a **1-line pointer**, not inlined doctrine.

---

## 3. THE CANONICAL DOCTRINE SNIPPET (≤60 lines)

> Put this in **exactly one place** and point everything else to it.
> **Recommended home:** `burnless/docs/COMMANDS.md` is already the code-adjacent SSOT for commands; extend it (or a sibling `docs/DOCTRINE.md`) with the tier/workflow block below, and make `claude_integration.render_block()` emit only a pointer line. The global `~/.claude/CLAUDE.md` keeps Roberto's cross-tool rules and points here.

```markdown
## Burnless — how to use (canonical; v0.9.0)

Delegate work instead of editing files directly. Architecture: PROTOCOL.md. Commands: docs/COMMANDS.md (verified vs --help). Code wins over memory.

### Commands (core)
- burnless route "TASK"      # preview tier, no run
- burnless delegate "TASK"   # create dXXX, no run
- burnless run dXXX          # execute (exit 0 only if worker OK)
- burnless do "TASK" --tier T  # atomic delegate+run
- burnless read|capsule|log dXXX · status · metrics

### Tiers (DEFAULT model map — per-project .burnless/config.yaml may REMAP; always check)
| tier   | default model | use for                                            |
|--------|---------------|----------------------------------------------------|
| gold   | opus          | architecture, structural refactor, decisions       |
| silver | sonnet        | implementation w/ tight spec + HARD PROHIBITIONS    |
| bronze | haiku         | reads, summaries, classification, ops shell         |
| diamond| (opt-in only) | NEVER auto-routed; reachable only via --tier diamond |

Spec quality picks the tier: compiles-in-your-head → bronze; needs thinking → silver; needs deciding-between-architectures → gold. Wrong tier costs money (over-provision) — don't tier-creep.

### Compression
--mode {light|balanced|extreme} controls compression (NOT a timeout). `light` = anti-phantom default (all active projects on light). `extreme` = read-only/summary only.

### Timeouts
`do` and `run` accept --timeout / --stale-timeout-s (do: since v0.9.0). `delegate` does NOT.

### Workflow
1. Commit working tree before delegating (workers share the tree, may reset files; isolate with `git worktree add` for shared files).
2. End every code spec with a `## Verify` fenced shell block asserting the DoD. The runner RE-RUNS it post-worker and demotes OK→PART on failure (deterministic, zero-LLM). Trust an OK that survived the gate; manual audit only for what shell can't encode. Footgun: use `! grep -q PATTERN file` for absence checks — never `grep -c`/`diff` (exit 1 on the good state).
3. PART output → reject + re-spec smaller. Never merge partial work.
4. Never pipe `do/run` through `tail`/`head` (masks exit code). Capture `> file 2>&1`.

NOTE: the v0.8 LLM-auditor is RETIRED. The `## Verify` shell gate is the only live post-worker verification.
```

---

## 4. PER-FILE RECOMMENDED ACTIONS (report only — no edits performed)

| File | Action | Specifics |
|---|---|---|
| `~/.claude/CLAUDE.md` | **FIX + designate as cross-tool home** | Drop/reframe the stale auditor-bug line 33 (auditor retired). Replace inline tier/command doctrine with a pointer to the canonical snippet; keep Roberto-specific cross-tool rules + extras. Bump version note v0.7.4→v0.9.0. |
| `~/antigravity/CLAUDE.md` | **FIX (tier table) + TRIM (duplication)** | Correct tier table to default **gold=opus / silver=sonnet / bronze=haiku**; mark `diamond` as opt-in-only; label the silver=Haiku mapping as a *per-project* remap, not default. Trim the triplicated worktree/PART/`## Verify` prose → 1-line pointer to canonical. Keep PROTOCOL.md/COMMANDS.md pointers (already good). |
| `calibration/soul.md` | **KEEP** | No burnless doctrine. Leave as-is. |
| `fw-social/CLAUDE.md` | **TRIM → pointer** | Replace the entire stale v0.7.4 block with a 1-line pointer to the canonical doctrine. (Superseded "Audit DoD" rule + stale version + diamond-as-route all vanish.) |
| `fw-social-next/CLAUDE.md` | **TRIM (burnless block only)** | Same pointer swap as fw-social. **KEEP** the codex-OAuth + "Criar com IA" pauta sections (project-specific, current). |
| `nutri/CLAUDE.md` | **KEEP (tier remap) + TRIM (shared doctrine)** | Tier remap is a valid documented per-project override — keep, keep its rationale. Trim the duplicated worktree/PART/`## Verify`-footgun prose → pointer. Keep unique auth/DB/deploy/`next build`/zsh lessons. |
| `burnless/AGENTS.md` | **KEEP** | Hygiene only, current. |
| `burnless/GEMINI.md` | **KEEP** | Already the correct pointer pattern (→ AGENTS.md). |
| `burnless/soul.md` | **FIX (command drift)** | Align command list with COMMANDS.md: add `do`/`route`, make `log` take `dXXX`, demote/clarify `plan`. Tier conceptual table OK. It is a distinct chat-injection artifact → keep, don't pointerize. |
| `chardon_catalog/CLAUDE.md` | **TRIM → pointer** | Same stale v0.7.4 block as fw-social; replace with 1-line pointer. |
| `burnless/CLAUDE.md` | **N/A** | Does not exist. |

**Structural fix (root cause):** change `src/burnless/claude_integration.py:render_block()` so the generated `<!-- burnless -->` block emits a **pointer** to the canonical doctrine instead of inlining it, and drop the superseded "Audit DoD point-by-point" rule in favor of the `## Verify` gate. This stops new projects from re-forking stale doctrine. *(Out of scope for this read-only task — flagged for a follow-up code delegation.)*

---

## Summary of contradictions found
1. **silver/gold model map** — files B (antigravity) say silver=Haiku/gold=Sonnet; **code default = silver=Sonnet / gold=Opus**. The Haiku mapping is a legit *per-project* remap (nutri) that leaked into global doctrine.
2. **diamond tier** — presented as a standard auto-routable band (B, D, E, J); code treats it as **opt-in-only, never auto-routed**.
3. **Retired LLM auditor described as live** — `~/.claude/CLAUDE.md` line 33 (auditor >100k bug "fix in flight"); the auditor is retired, replaced by the `## Verify` deterministic gate.
4. **Superseded workflow rule** — "Audit DoD point-by-point / don't trust OK" in D/E/J; replaced by the runner's `## Verify` gate.
5. **Stale version stamps** — D/E/J carry `v0.7.4`; code is **v0.9.0**.
6. **Command drift** — `burnless/soul.md` lists `plan` and bare `log`, omits `do`/`route`/`capsule`; COMMANDS.md is canonical.
7. **Doctrine triplication** — worktree-isolation + PART-rejeita + `## Verify` prose repeated nearly verbatim across `~/.claude`, `~/antigravity`, and `nutri`.
