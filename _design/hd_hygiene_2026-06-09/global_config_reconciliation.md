# Global Config Reconciliation — HD Hygiene

**Date:** 2026-06-09
**Scope:** read-only analysis + written proposal. No live writes performed.
**Goal:** one canonical global CONFIG (`~/.config/burnless/config.yaml`); `~/.burnless/` becomes STATE-only.

---

## 0. TL;DR — the premise is half-wrong (critical finding)

The two globals do **not** "fight in one cascade." The main loader `config.load()`
reads **only** `~/.config/burnless/config.yaml`. The old `~/.burnless/config.yaml`
is **invisible to the main loader** — but it is **still live** through a *second,
independent* code path: the **profiles subsystem** (`profiles.py`), which hardcodes
`~/.burnless/config.yaml` as its base.

So this is **split-brain**, not a merge-order conflict:

| Subsystem | Reads which global | Code |
|---|---|---|
| Main loader (`do`/`run`/`route`/`brain`/MCP) | `~/.config/burnless/config.yaml` | `config.py:213` |
| Profiles (`burnless profile show/init`, `extends: ../config.yaml`) | `~/.burnless/config.yaml` | `profiles.py:8,13,21,31,47,81,88,115` |

Anyone who edited `~/.burnless/config.yaml` expecting it to change routing/agents
saw **no effect** on `burnless do` — that file only feeds profiles. That is the
actual hazard to fix.

---

## 1. Files read

- `~/.config/burnless/config.yaml` — 106 lines, newer XDG layer (the live global).
- `~/.burnless/config.yaml` — 127 lines, older (live ONLY for profiles).
- `~/.burnless/config.yaml.bak.1777757854` — 1781 bytes, 2026-05-02. **Backup exists, not read deeply.** Stale, safe to archive.
- `src/burnless/config.py` `load()` (lines 206-229) + `DEFAULT_CONFIG` (6-163).
- `src/burnless/profiles.py` (the second reader).

---

## 2. ACTUAL precedence / merge order (from `config.py:206-229`)

```
DEFAULT_CONFIG  (config.py:6-163, baked-in)
   └─ deep_merge ←  ~/.config/burnless/config.yaml      (global_path, line 213)
        └─ deep_merge ←  <project>/.burnless/config.yaml  (the `path` arg)
```

Code flow:
- `global_data = _read(~/.config/burnless/config.yaml)`            (line 213-214)
- `project_data = _read(path)`                                      (line 215)
- `user_data = _deep_merge(global_data, project_data)`  → **project wins over global** (line 216)
- `data = _deep_merge(DEFAULT_CONFIG, user_data)`       → **user wins over defaults** (line 222)
- then `_normalize_legacy_tiers` + compression-mode normalization (223-228)

**Effective precedence, high → low:**
1. project `<cwd>/.burnless/config.yaml`
2. `~/.config/burnless/config.yaml`  ← the only global in the cascade
3. `DEFAULT_CONFIG`

`~/.burnless/config.yaml` is **NOT in this cascade at all.** It is read only by
`profiles.resolve_profile()` (`profiles.py:81` for the no-name case, and via
`extends: "../config.yaml"` resolved relative to `~/.burnless/profiles/`).
Note: `~/.burnless/profiles/` does **not currently exist** → the profile path is
dormant today, but the dangling reference is a live trap the moment a profile is created.

---

## 3. Side-by-side: keys, overlaps, CONFLICTS

Legend: ✅ present · — absent · ⚠️ **CONFLICT (same key, different value)**

| Key | `~/.config/burnless` (live global) | `~/.burnless` (profiles-only) | Verdict |
|---|---|---|---|
| `project_name` | — | `Project` | dup of default; drop |
| `language` | — | `pt-BR` | dup of default; drop |
| `mode` | — | `local_first` | dup of default; drop |
| `agents.gold.name` | `opus` | `claude-opus` | ⚠️ cosmetic |
| `agents.gold.command` | `/opt/homebrew/bin/claude --model opus --dangerously-skip-permissions -p` | `claude -p --model opus --permission-mode bypassPermissions --allowedTools ... --add-dir /Users/roberto/.claude --output-format stream-json --verbose --include-partial-messages` | ⚠️ **CONFLICT** |
| `agents.gold.role` | `strategy_architecture` | `strategy_architecture_code_review` | ⚠️ |
| `agents.silver.name` | `haiku` | `codex-o4mini` (provider: openai) | ⚠️ **MAJOR CONFLICT** |
| `agents.silver.command` | `/opt/homebrew/bin/claude --model haiku --dangerously-skip-permissions -p` | `codex exec -m o4-mini --approval-mode full-auto` | ⚠️ **MAJOR — different vendor** |
| `agents.bronze.name` | `haiku` | `claude-haiku` | ⚠️ cosmetic |
| `agents.bronze.command` | `/opt/homebrew/bin/claude --model haiku --dangerously-skip-permissions -p` | `claude -p --model haiku --permission-mode acceptEdits --allowedTools ...` | ⚠️ **CONFLICT** |
| `agents.diamond` | ✅ codex `gpt-5.5` (`/Users/roberto/.local/bin/codex exec ...`) | — | only in .config |
| `routing.diamond` | ✅ (code/bug/debug/build/etc.) | — | only in .config |
| `routing.silver` | docs/prd/spec only | docs **+ all code keywords + projeto/repositorio/memoria/anotacoes** | ⚠️ **CONFLICT** (code routes to diamond in .config, silver in .burnless) |
| `routing.hardcore_filter` | — (lives at `routing.hardcore_filter` in DEFAULT) | `false` (nested under routing) | matches default |
| `compression.mode` | `light` | `balanced` | ⚠️ **CONFLICT** |
| `compression.friendly` / `voice_match` | — | `true` / `true` | dup of default |
| `metrics.*` | — | token_estimation_ratio 4, show_*, expensive_model_usd_per_million 15.0 | dup of default |
| `cache_policy.*` | — | full block | dup of default |
| `privacy.*` | — | mode cost / plain / memory | dup of default |
| `plan` | — | `free` | user-specific, keep |
| `paywall.*` | — | enabled true, threshold 10, price 10 | user-specific, keep |

**Net:** the two globals encode **two different agent fleets**. `~/.config`
= Roberto's current canonical mapping (gold=opus, silver=haiku, bronze=haiku,
diamond=codex). `~/.burnless` = an **older fleet** (silver=Codex o4-mini, no diamond,
code→silver routing, balanced compression). The `~/.burnless` file is the stale one;
most of its non-conflicting keys merely re-state `DEFAULT_CONFIG`.

---

## 4. RECOMMENDATION — single canonical layout

**Recommend** the following split:

### `~/.config/burnless/config.yaml` — the ONLY global CONFIG
Keep it as the live source of truth (it already is, for the main loader). Content =
**deltas from `DEFAULT_CONFIG` only**. Roberto's canonical fleet
(gold=opus, silver=haiku, bronze=haiku, diamond=codex) + `compression.mode: light` +
any genuinely-wanted user extras (`plan`, `paywall`). Everything that merely repeats
DEFAULT_CONFIG (project_name, language, mode, metrics, cache_policy, privacy,
compression.friendly/voice_match) is **dropped** — defaults already supply it.

### `~/.burnless/` — STATE only (reclassify)
Already-state (leave as-is): `metrics.json`, `global_metrics.jsonl`, `audit.jsonl`,
`capsules/`, `runs/`, `delegations/`, `logs/`, `temp/`, `state/`, `state.json`,
`state.lock`, `warm/`, `warm_session*.json`, `sessions/`, `decisions_cache.json`,
`provider_health.json`, `chats.db`, `iso-cwd/`, `iso-cwd-codex/`, `bin/`, `plugins/`,
`desktop/`, `exec_log/`, `archive/`, `license.json` (symlink), `master.key`,
`cloud_emulator.py`, `chat/`, `bench/`, `test_data/`, `maestro*`.

**Reclassify / remove (these are CONFIG, not state):**
- `~/.burnless/config.yaml`  → fold useful deltas into `~/.config/...`, then **rename to `config.yaml.deprecated`** (don't hard-delete first pass).
- `~/.burnless/config.yaml.bak.1777757854` → move to `~/.burnless/archive/`.

> Migration is PROPOSAL-only here; no file was moved or written.

---

## 5. PROPOSED merged `~/.config/burnless/config.yaml`

> Do NOT write this to the live path. Review first. Keeps `~/.config`'s canonical
> fleet; grafts in `plan`/`paywall` from the old file; drops everything that only
> repeats `DEFAULT_CONFIG`.

```yaml
# ~/.config/burnless/config.yaml — GLOBAL do usuário (ÚNICA camada global de CONFIG)
# Cascata: DEFAULT_CONFIG -> ESTE -> <projeto>/.burnless/config.yaml
# ~/.burnless/ é STATE-only (metrics, capsules, runs, warm, logs). NÃO há config global lá.
# Mapeamento canônico Roberto: gold=opus, silver=haiku, bronze=haiku, diamond=codex.
# Só guardar DELTAS vs DEFAULT_CONFIG; o resto vem dos defaults do código.
agents:
  diamond:
    name: codex
    command: /Users/roberto/.local/bin/codex exec --skip-git-repo-check --sandbox workspace-write -m gpt-5.5
    role: code_debug_execution
    use_for: [code, debug, tests, repo_changes]
  gold:
    name: opus
    command: /opt/homebrew/bin/claude --model opus --dangerously-skip-permissions -p
    role: strategy_architecture
    use_for: [architecture, complex_reasoning, high_level_planning]
  silver:
    name: haiku
    command: /opt/homebrew/bin/claude --model haiku --dangerously-skip-permissions -p
    role: code_implementation
    use_for: [code, implementation, docs, prd, prompts, specs]
  bronze:
    name: haiku
    command: /opt/homebrew/bin/claude --model haiku --dangerously-skip-permissions -p
    role: summaries_classification
    use_for: [summarize, classify, clean_logs]
routing:
  diamond: [código, codigo, code, erro, bug, debug, terminal, arquivo, repo,
            teste, test, build, compilar, compile, stack trace, exception,
            compression, simulator]
  gold: [arquitetura, architecture, estratégia, estrategia, strategy, decisão,
         decisao, decision, risco, risk, conceito, produto, roadmap, trade-off, tradeoff]
  silver: [documentação, documentacao, documentation, briefing, prd, prompt,
           especificação, especificacao, spec, texto, readme]
  bronze: [resumir, resumo, summarize, summary, limpar, clean, classificar,
           classify, extrair, extract, organizar log, tag]
compression:
  mode: light          # anti-phantom; canonical knob (NÃO usar --timeout)
# --- user-specific extras migrated from old ~/.burnless/config.yaml ---
plan: free
paywall:
  enabled: true
  threshold_usd: 10.0
  price_usd_month: 10.0
```

> Dropped vs old `~/.burnless`: `project_name`, `language`, `mode`, `metrics`,
> `cache_policy`, `privacy`, `compression.friendly/voice_match`,
> `routing.hardcore_filter` — all identical to `DEFAULT_CONFIG`, so omitting them
> changes nothing and shrinks the global to true deltas.

---

## 6. Code changes so only ONE global is read

`config.py` already reads only `~/.config` — the leak is entirely in **`profiles.py`**.
Repoint the profiles base at the canonical global and the split-brain closes.

**`src/burnless/profiles.py`:**
- **Line 8** — `_CONFIG_BASE = Path.home() / ".burnless" / "config.yaml"`
  → change to `_CONFIG_BASE = Path.home() / ".config" / "burnless" / "config.yaml"`.
  (This fixes `resolve_profile(None)` at **line 81**, which returns the base directly.)
- **Lines 13, 21, 31, 47, 115** — every template's `"extends": "../config.yaml"`.
  Resolved at **line 88** as `(profile_path.parent / extends).resolve()` =
  `~/.burnless/profiles/../config.yaml` = `~/.burnless/config.yaml`. Two options:
  - (a) Stop using relative `extends` for the base. In `resolve_profile` (lines 86-90),
    when `extends == "../config.yaml"` (or any base sentinel), load `_CONFIG_BASE`
    instead of the relative resolve. Cleanest — templates stay declarative.
  - (b) Change every `extends` literal to an absolute/`~`-expanded path to
    `~/.config/burnless/config.yaml`. Simpler diff, but leaks an absolute path into
    five template literals.
  → Recommend (a): one branch in `resolve_profile`, templates unchanged in intent.

**`src/burnless/config.py` (optional, defensive):**
- In `load()` after line 214, add a **one-time deprecation warning** to stderr if
  `~/.burnless/config.yaml` exists, telling the user it is no longer read and to move
  deltas to `~/.config/burnless/config.yaml`. Pure UX; not required for correctness.

No other source path reads a *config* from `~/.burnless/` — the remaining
`Path.home()/".burnless"` references (`metrics.py:53`, `agents.py:52/59`,
`pipeline_state.py:13`, `warm_session*.py`, `rtk_loader.py`, `glossary_loader.py:26`,
`dispatcher.py`, `cli.py` metrics/desktop, `chat_mode.py:596`) are all **STATE** and
stay put — consistent with the "`~/.burnless/` = state-only" target.

---

## 7. Suggested apply order (future, NOT executed here)

1. Write the merged global to `~/.config/burnless/config.yaml` (back up first).
2. Patch `profiles.py` (line 8 + `resolve_profile` extends-base branch).
3. `burnless route "fix a bug in cli.py"` → expect **diamond** (codex), proving the
   merged routing is live.
4. `burnless profile show` (after creating one) → expect it to inherit the canonical
   fleet, proving profiles now read `~/.config`.
5. Rename `~/.burnless/config.yaml` → `config.yaml.deprecated`; move `.bak.*` to `archive/`.
