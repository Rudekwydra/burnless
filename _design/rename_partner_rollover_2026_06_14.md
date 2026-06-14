# Rename inventory + spec — purge internal "partner" / "rollover" code naming (2026-06-14)

**Status:** DESIGN ONLY. No code edited. Grounded entirely in grep output of
`/Users/roberto/antigravity/burnless` (full tree).

**Goal:** Stop the internal code from confusing the reading LLM. The engagement
MODES `partner` and `rollover` were removed (only `off`/`on` remain, with legacy
mode strings coerced to `on`). But the engine CODE still names:

- the maestro's tool-less role as **`partner`** (`PartnerState`, `partner_turn`,
  `partner_turn_session`, `_FALLBACK_PARTNER_ROLE`, `_load_partner_role`,
  `partner_role.md`) — a dead-mode ghost reused for a live concept → **RENAME → `maestro`**.
- the rolling rewind-recompact cadence as **`rollover`** (`--rollover-turns`,
  `/rollover`, `rollover_turns`, `BURNLESS_ROLLOVER_TURNS`, `.rollover.md/.json`)
  → **LEAVE** (live, accurate concept; multiple public/tested surfaces). See §3.

---

## 1. Exhaustive inventory

### 1.A `partner` — RENAME (maestro-role internal code concept)

**`/Users/roberto/antigravity/burnless/src/burnless/maestro/engine.py`**
- `:1` module docstring `"...partner loop + rolling rewind-recompact."`
- `:6` docstring `"The maestro is a tool-less partner."`
- `:53` `class PartnerState:`
- `:71` `def assemble_prompt(state: PartnerState, ...)`
- `:83` `def window_tokens(state: PartnerState)`
- `:88` `def maybe_compact(state: PartnerState, ...)`
- `:114` `def build_pending_seed(state: PartnerState)`
- `:121` `_apply_compaction_result(state: PartnerState, ...)`
- `:165` `def force_compact(state: PartnerState, ...)`
- `:195` `def partner_turn(`
- `:196` `state: PartnerState,`
- `:204` docstring `"One partner turn: assemble..."`
- `:218` `def _render_tail(state: PartnerState)` — annotation only; helper name stays (see §1.D)
- `:222` `def partner_turn_session(`
- `:223` `state: PartnerState,`
- `:232` docstring `"One partner turn over a conversation-native session."`

**`/Users/roberto/antigravity/burnless/src/burnless/maestro/base.py`**
- `:4` docstring `"...the slim partner role (_design/maestro_v1/partner_role.md)..."`
- `:28` comment `"...when _design/maestro_v1/partner_role.md is absent"`
- `:30` `_FALLBACK_PARTNER_ROLE = (`
- `:31` role-text body `"You are the Burnless MAESTRO — a tool-less partner..."` → **prompt content, LEAVE** (see §1.C / §3)
- `:45` `def _load_partner_role(project_root: Path) -> str:`
- `:46` `role_path = project_root / "_design" / "maestro_v1" / "partner_role.md"`
- `:50` `return _FALLBACK_PARTNER_ROLE`
- `:75` `role = _load_partner_role(project_root)`

**`/Users/roberto/antigravity/burnless/src/burnless/cli.py`**
- `:1408` docstring `"Partner-maestro REPL on the new core..."`
- `:1409` docstring `"MaestroSession/partner_turn_session → dispatcher.run_all..."`
- `:1422` `from .maestro.engine import PartnerState, Turn, estimate_tokens, partner_turn_session`
- `:1452` `state = PartnerState()`
- `:1595` `response = partner_turn_session(`
- `:1877` `sp = sub.add_parser("chat", help="partner-maestro REPL on the new core ...")`

**`/Users/roberto/antigravity/burnless/_design/maestro_v1/partner_role.md`** (file to rename → `maestro_role.md`)
- `:1` `# Partner Role (maestro v1)` — H1; **prompt content** (loaded verbatim as role), LEAVE body (see §3)
- `:3` `"...a tool-less partner orchestrating ephemeral workers."` — prompt content, LEAVE

**Tests (must be renamed in lockstep or imports break):**
- `/Users/roberto/antigravity/burnless/tests/test_chat_rollover.py:4` `from burnless.maestro.engine import PartnerState, RollingCapsule, Turn, force_compact`
- `…/tests/test_chat_rollover.py:15` `state = PartnerState(`
- `…/tests/test_chat_turn_router.py:7` `from burnless.maestro.engine import PartnerState`
- `…/tests/test_chat_turn_router.py:80` `return PartnerState()`
- `…/tests/test_maestro_engine.py:1` docstring `"...maestro partner engine (M1 prototype)."`
- `…/tests/test_maestro_engine.py:12` import `PartnerState,`
- `…/tests/test_maestro_engine.py:17` import `partner_turn,`
- `…/tests/test_maestro_engine.py:48,70,85,91,105,123,141,148,171,179,196,226,228,247,261,294,352,370,390,413` `PartnerState(...)`
- `…/tests/test_maestro_engine.py:57,94,157` `partner_turn(`
- `…/tests/test_maestro_engine.py:311` comment `"# partner_turn_session: integrated session backend ..."`
- `…/tests/test_maestro_engine.py:314` `from burnless.maestro.engine import partner_turn_session`
- `…/tests/test_maestro_engine.py:353,372,392,399,415` `partner_turn_session(`

### 1.B `partner` — LEAVE (legitimate MODE string / already-handled back-compat coercion)

These are the **public mode** `partner` (removed engagement mode, coerced to `on`).
NOT the maestro-role concept. Touching them changes user-facing behavior.

- `/Users/roberto/antigravity/burnless/templates/scripts/burnless_mode_hook.sh:5,163,181,197`
  — accept-set `{"on","partner","off","rollover"}`, menu string, `if mode == "partner"` coercion.
- `/Users/roberto/antigravity/burnless/docs/USING_BURNLESS_FROM_YOUR_LLM.md:137,168,171,179`
  — `case "$a" in partner|rollover) a=on` coercion snippets.
- `/Users/roberto/antigravity/burnless/docs/DOCTRINE.md:62` — `"partner/rollover are gone — both fold into on."`
- `/Users/roberto/antigravity/burnless/README.md:272` — legacy-note paragraph.
- `/Users/roberto/antigravity/burnless/.claude/commands/burnless.md:12` — legacy coercion note.

### 1.C `partner` — LEAVE (prompt CONTENT seen by the maestro model, not a code symbol)

Editing these changes the role prompt = behavior change, out of scope for a pure rename.
Flagged for an explicit Roberto decision in §3.
- `…/src/burnless/maestro/base.py:31` `"...a tool-less partner..."` (inside `_FALLBACK_*_ROLE`)
- `…/_design/maestro_v1/partner_role.md:1,3` H1 + body prose.

### 1.D `partner` — LEAVE (historical design records / unrelated)

Design docs are dated records; rewriting them falsifies history. No code depends on them.
- `…/_design/TARGET_ARCHITECTURE_2026-06-09.md:10,17,38,40,42,51`
- `…/_design/FABLE_SENIOR_REVIEW_2026-06-09.md:104,239,250,251,252,261`
- `…/_design/FABLE_READINESS_AND_VERBOSE_2026-06-09.md:18,46,48,49,55,61,72,73,80,89,100,101,104,106,107,123,302`
- `…/_design/FABLE_REVIEW_2026-06-09.md:15,82,110,153,248,250,271`
- `…/_design/CODEX_CHAT_ROLLOVER_2026-06-10.md:565,583,695,715,742,796,815,825,831,837,841,863,869,881,887,911,917,929,941,994`
- `…/_design/CONSOLIDATION_PLAN_2026-06-10.md:58,67`
- `…/_design/REWRITE_CONCEPT_2026-06-09.md:149`
- `…/_design/BURNLESS_V1.md:10,21,35,40`
- `…/_design/rolling_memory_pure_chat_2026_06_13.md:96,109,130,134,136,175,176,178,181,183,186,206`

Also LEAVE: `engine.py:31`/`:213`/`:248` `role: str # "user" | "maestro"` and
`Turn("maestro", ...)` — already use `maestro`, nothing to do.
`engine.py:218 _render_tail` — name is not "partner"; only its parameter annotation
(`PartnerState`) changes via the type rename. Function name stays.

### 1.E `rollover` — full inventory (decision: LEAVE; see §2 / §3)

**Public CLI flag + REPL slash command (tested):**
- `…/src/burnless/cli.py:1880` `"--rollover-turns"` (argparse → `args.rollover_turns`)
- `…/src/burnless/cli.py:1480,1482,1526,1527,1541,1546,1579,1643` `rollover_turns` (local)
- `…/src/burnless/cli.py:1494,1578,1587,1642,1651` `turns_since_rollover` (local)
- `…/src/burnless/cli.py:1410` docstring `"...opt-in rollover mode can force a cycle"`
- `…/src/burnless/cli.py:1525,1541,1546,1552,1588,1652` `/rollover` REPL strings & `↻ rollover capsule` output
- `…/src/burnless/maestro/turn_router.py:46,49,67,68,72,74,75` `("rollover", int)` parser for `/rollover N`
- `…/src/burnless/maestro/engine.py:173` docstring `"...opt-in chat rollover flows..."`
- `…/tests/test_chat_rollover.py:7,9,10` `--rollover-turns` parse assertion
- `…/tests/test_chat_slash.py:27,28,31,32,33,36,37,44,45` `/rollover` parse assertions

**Public mode + env var + on-disk artifacts (separate surface, handled by rolling-memory design):**
- `…/templates/scripts/burnless_mode_hook.sh:7,163,181,213,215,221,222,304` mode `rollover`,
  `BURNLESS_ROLLOVER_TURNS`, `session-{sid}.rollover.md/.json`, `[BURNLESS ROLLOVER]`
- `…/restart_rollover.sh:5,42,51,66,76` script + `BURNLESS_ROLLOVER_DRYRUN` + `.rollover.md`
- `…/tests/test_claude_rollover_hook.py:12,15,29,…` `BURNLESS_ROLLOVER_TURNS`, `.rollover.md/.json`
- `…/tests/test_restart_rollover_script.py:8,11,28,37` `restart_rollover.sh`, `.rollover.md`, `BURNLESS_ROLLOVER_DRYRUN`
- docs/design records: `README.md:272`, `docs/USING…:137,168,171,179`, `docs/DOCTRINE.md:62`,
  `.claude/commands/burnless.md:12`, `_design/BENCHMARK_*`, `_design/bench_v2_honest.py`,
  `_design/CODEX_CHAT_ROLLOVER_2026-06-10.md`, `_design/rolling_memory_pure_chat_2026_06_13.md`.

---

## 2. Rename MAPPING with rationale

| Old token | New token | Why |
|---|---|---|
| `PartnerState` | `MaestroState` | It's the maestro's working state (capsule + window + cycle). "Partner" is a removed-mode ghost. |
| `partner_turn` | `maestro_turn` | One turn of the maestro loop. (`partner_turn` is a substring of `partner_turn_session`, so this single token rule renames BOTH — see §4.) |
| `partner_turn_session` | `maestro_turn_session` | Session-backed maestro turn. Covered by the `partner_turn`→`maestro_turn` rule. |
| `_FALLBACK_PARTNER_ROLE` | `_FALLBACK_MAESTRO_ROLE` | Embedded fallback for the maestro role prompt. |
| `_load_partner_role` | `_load_maestro_role` | Loader for the maestro role file. |
| `partner_role.md` | `maestro_role.md` | The maestro role file (string literal + the file itself). |

**Docstring/comment prose** (code files only — safe, no behavior): `partner loop` →
`maestro loop`; `tool-less partner` (in docstrings, NOT in role-prompt strings) →
`tool-less maestro`; `One partner turn` → `One maestro turn`; `Partner-maestro REPL` /
`partner-maestro REPL` → `maestro REPL`; the `partner_role.md` path inside `base.py`
docstring/comment → `maestro_role.md`.

**`rollover` → DECISION: KEEP (no rename).** Rationale:
1. `rollover` describes a **live, accurate** concept (force a rewind-recompact cycle
   every N turns). Unlike `partner`, it is not a dead-mode ghost — there is nothing
   misleading about it for the reading LLM.
2. Three public/tested surfaces would break: `burnless chat --rollover-turns N`
   (`cli.py:1880`, asserted `test_chat_rollover.py:10`), the `/rollover N` REPL command
   (`turn_router.py:67`, asserted `test_chat_slash.py:27-45`), and `BURNLESS_ROLLOVER_TURNS`
   (`burnless_mode_hook.sh:215`, asserted `test_claude_rollover_hook.py:15`). A rename
   buys near-zero clarity at the cost of public-surface breakage or alias debt.
3. The purely-internal locals (`rollover_turns`, `turns_since_rollover`) are bound by
   name to the public flag (`dest=rollover_turns`); renaming only them is churn that
   desyncs code from the user-facing flag. Leave them too.

Net: this refactor renames **`partner` (role concept) only**.

---

## 3. BACK-COMPAT hazards — MUST NOT break

1. **Public mode strings `partner` / `rollover`** (`burnless_mode_hook.sh:163,181,197`,
   docs). The hook accepts `partner` on read and coerces `partner|rollover → on`.
   This is intentional legacy support — **do NOT touch.** (§1.B)
2. **CLI flag `--rollover-turns`** (`cli.py:1880`; test `test_chat_rollover.py:10`).
   Public. KEEP. Renaming requires an argparse alias.
3. **REPL slash command `/rollover N`** (`turn_router.py:67`; tests `test_chat_slash.py:27-45`).
   KEEP.
4. **Env var `BURNLESS_ROLLOVER_TURNS`** (`burnless_mode_hook.sh:215`,
   test `test_claude_rollover_hook.py:15`) and `BURNLESS_ROLLOVER_DRYRUN`
   (`restart_rollover.sh:66`, test `test_restart_rollover_script.py:37`). KEEP.
5. **On-disk artifacts `session-<sid>.rollover.md` / `.rollover.json`**
   (`burnless_mode_hook.sh:221,222`, `restart_rollover.sh:51`). Persisted state of live
   sessions — renaming orphans them. KEEP.
6. **Role-prompt CONTENT** (`base.py:31` `_FALLBACK_*_ROLE` body, `partner_role.md` H1/body).
   These strings are sent to the maestro model. Editing them = prompt/behavior change,
   which violates "pure rename." **DEFAULT: LEAVE.** If Roberto wants "partner" purged
   from what the model reads too, that is a separate, explicit prompt edit (recommend
   wording the role as "a tool-less orchestrator/maestro") — flagged here, not done.
7. **No on-disk `partner` artifact** to migrate: real code persists capsules as
   `maestro/rolling/capsule_<cycle>.json` (`engine.py:143`), not `partner_state.json`
   (that name appears only as a *proposal* in `_design/FABLE_SENIOR_REVIEW…:251`).
   So the `PartnerState` rename has **no serialization/back-compat impact** — `PartnerState`
   is never written to disk by class name.
8. **Alias need:** NONE for the `partner`→`maestro` rename. All `partner` code symbols are
   private/internal (no public API exports `PartnerState`/`partner_turn*`; consumers are
   `cli.py` + tests in-tree). No alias required.

---

## RENAME PLAN

Pure rename, no behavior change. Apply as scripted whole-word token replacement across
the code+test files below (NOT design docs §1.D, NOT mode-hook/templates §1.B, NOT
prompt content §1.C). Order matters where noted.

**Scope (files to edit):**
- `/Users/roberto/antigravity/burnless/src/burnless/maestro/engine.py`
- `/Users/roberto/antigravity/burnless/src/burnless/maestro/base.py`
- `/Users/roberto/antigravity/burnless/src/burnless/cli.py`
- `/Users/roberto/antigravity/burnless/tests/test_maestro_engine.py`
- `/Users/roberto/antigravity/burnless/tests/test_chat_rollover.py`
- `/Users/roberto/antigravity/burnless/tests/test_chat_turn_router.py`

**Ordered token replacements (whole word):**
1. `partner_turn` → `maestro_turn`  *(run FIRST; also converts `partner_turn_session` → `maestro_turn_session`, since the former is a substring of the latter)*
2. `PartnerState` → `MaestroState`
3. `_FALLBACK_PARTNER_ROLE` → `_FALLBACK_MAESTRO_ROLE`
4. `_load_partner_role` → `_load_maestro_role`
5. `partner_role.md` → `maestro_role.md`  *(string literal in `base.py:46` + docstring/comment refs `base.py:4,28`)*

**Prose-only edits (code files, safe — do NOT alter the `_FALLBACK_MAESTRO_ROLE` string body):**
6. `engine.py:1` `partner loop` → `maestro loop`
7. `engine.py:6` `tool-less partner` → `tool-less maestro`  *(module docstring only)*
8. `engine.py:204` `One partner turn` → `One maestro turn`
9. `engine.py:232` `One partner turn over` → `One maestro turn over`
10. `cli.py:1408` `Partner-maestro REPL` → `Maestro REPL`
11. `cli.py:1877` help `partner-maestro REPL on the new core` → `maestro REPL on the new core`
12. `test_maestro_engine.py:1` `maestro partner engine` → `maestro engine`

**File rename:**
13. `git mv` (or `mv`) `/Users/roberto/antigravity/burnless/_design/maestro_v1/partner_role.md`
    → `/Users/roberto/antigravity/burnless/_design/maestro_v1/maestro_role.md`
    *(content unchanged — it is loaded verbatim as prompt; see §3 item 6).*

**References-to-update** are all already covered by token rules 4–5 and the file rename:
the only on-disk reference to the filename is the literal in `base.py:46`
(`"_design" / "maestro_v1" / "partner_role.md"`), handled by rule 5.

**Leave untouched:** everything in §1.B, §1.C, §1.D, §1.E (all `rollover`), and the
`_FALLBACK_MAESTRO_ROLE` string body + the renamed `maestro_role.md` content.

## Verify
```sh
! grep -rq "PartnerState" /Users/roberto/antigravity/burnless/src /Users/roberto/antigravity/burnless/tests || exit 1
! grep -rq "partner_turn" /Users/roberto/antigravity/burnless/src /Users/roberto/antigravity/burnless/tests || exit 1
! grep -rq "partner_role" /Users/roberto/antigravity/burnless/src || exit 1
! grep -rq "_FALLBACK_PARTNER_ROLE" /Users/roberto/antigravity/burnless/src || exit 1
grep -rq "class MaestroState" /Users/roberto/antigravity/burnless/src/burnless/maestro/engine.py || exit 1
grep -rq "def maestro_turn" /Users/roberto/antigravity/burnless/src/burnless/maestro/engine.py || exit 1
grep -rq "def maestro_turn_session" /Users/roberto/antigravity/burnless/src/burnless/maestro/engine.py || exit 1
grep -rq "_FALLBACK_MAESTRO_ROLE" /Users/roberto/antigravity/burnless/src/burnless/maestro/base.py || exit 1
grep -rq "def _load_maestro_role" /Users/roberto/antigravity/burnless/src/burnless/maestro/base.py || exit 1
test -f /Users/roberto/antigravity/burnless/_design/maestro_v1/maestro_role.md || exit 1
test ! -f /Users/roberto/antigravity/burnless/_design/maestro_v1/partner_role.md || exit 1
grep -rq "maestro_role.md" /Users/roberto/antigravity/burnless/src/burnless/maestro/base.py || exit 1
cd /Users/roberto/antigravity/burnless && python3 -c "import burnless.cli, burnless.maestro.engine, burnless.maestro.base" || exit 1
```
