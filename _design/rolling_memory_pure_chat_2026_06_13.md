# Rolling memory for a PURE Claude Code session + simplified mode surface

**Date:** 2026-06-13
**Author:** gold design pass (d684)
**Status:** design + silver spec — NOT implemented
**Project root:** `/Users/roberto/antigravity/burnless`

---

## 0. Ground truth (verified against code 2026-06-13, NOT assumed)

The launch-audit premise ("nothing writes epochs from a pure Claude Code session")
is **partially wrong**. Verified facts:

| Claim in audit | Reality in code | Evidence |
|---|---|---|
| Nothing writes epochs from pure session | **FALSE.** `burnless_epoch_stop.sh` is a `Stop` hook (registered in `~/.claude/settings.json`) that extracts last user+assistant from `transcript_path` and pipes to `burnless epoch capture`. | `/Users/roberto/.claude/scripts/burnless_epoch_stop.sh`; settings.json `Stop[].hooks[2]` |
| `epochs.on` absent | **FALSE.** Marker exists since Jun 10. | `/Users/roberto/antigravity/burnless/.burnless/epochs.on` (0 bytes, Jun 10 18:38) |
| Epochs never get written | **FALSE.** 6 chat dirs already populated. | `/Users/roberto/antigravity/burnless/.burnless/epochs/` (latest `febbd0d3…` Jun 13 10:59) |
| `SessionStart` reads epochs | TRUE. | `/Users/roberto/.claude/scripts/burnless_epoch_session.sh` → `burnless epoch read --chat-id $SID` |
| `rolling_compaction_enabled: false` | TRUE, but **independent** of the epoch engine. | `.burnless/config.yaml` `cache_policy.rolling_compaction_enabled` |

So the write→read loop **already exists and runs**. The actual defects are:

1. **TWO parallel, non-composing rolling-memory systems.**
   - **System A — Epoch engine** (the correct shape): `Stop` writes (`burnless_epoch_stop.sh` → `burnless epoch capture` → `epochs.py append_epoch`/`consolidate_level`), `SessionStart` reads (`burnless_epoch_session.sh` → `burnless epoch read`). Injection is **1× per session** — matches the frozen-seed/fork-duplo doctrine.
   - **System B — `rollover` mode** (the wrong shape): `burnless_mode_hook.sh` on **`UserPromptSubmit`** rebuilds a capsule from the transcript **every turn**, writes `session-<sid>.seed.md`, runs a gemma compaction at rotation, and nudges the user to `/rewind`. Per-turn injection is exactly the ~20k-tok/session waste documented in `[[fork-duplo-rolling-memory-sessionstart-vs-userprompt-2026-06-12]]`, and the `/rewind` nudge contradicts `[[paper-opportunity-clear-reseed-window-shrink-2026-06-12]]` (which proved `/clear`+reseed is the cheaper primitive).

2. **Cross-`/clear` keying gap.** Epoch chains are keyed by `--chat-id $SID` (session_id). `SessionStart` reads `epoch read --chat-id $SID`. If a `/clear`/new launch rotates the session_id, the new session's `SessionStart` queries an **empty chain** for the new id → no reseed. There is no project-level "latest rolling seed" pointer that survives id rotation.

3. **Capture granularity.** `burnless_epoch_stop.sh`'s python loop overwrites `u`/`a` each line, so it captures only the **final** user+assistant exchange of the transcript per `Stop`, not the whole turn delta. Acceptable for v1 (one slot per turn, consolidate at 10), but noted.

**The design below is therefore a UNIFICATION + a small cross-`/clear` fix, not a greenfield build.** Decision: **keep System A, retire System B's per-turn injection, add a project-level seed pointer.**

---

## 1a. Which hook writes — decision: `Stop` (keep `burnless_epoch_stop.sh`)

**Decision: the `Stop` hook writes the rolling epoch. Reject `UserPromptSubmit` and `PreCompact`.**

Justification, grounded in the four capsules and the code:

- **`Stop` (CHOSEN).** Fires once when the assistant finishes a turn — exactly one write per turn, after the full exchange exists in `transcript_path`. The write is **off the hot path** (`async: true` in settings.json) so it never blocks the user. The cost model in `[[fork-duplo-rolling-memory-sessionstart-vs-userprompt-2026-06-12]]` shows the write must happen once per turn-delta regardless; `Stop` is the natural once-per-turn boundary. Already wired and working — minimal change.
- **`UserPromptSubmit` (REJECTED for writing).** This is System B. It re-injects the rolling capsule into context **every turn**, landing at a new array offset each time → prefix-cache never dedupes → write every turn (`[[prefix-exact-not-volume-discount-frozen-seed-2026-06-12]]`: cache is exact-prefix, not volume discount). `UserPromptSubmit` is correct **only** for query-specific, intentionally-volatile injection (`forgetless_auto_rank.sh`), never for the durable seed.
- **`PreCompact` (REJECTED).** Fires only on native `/compact` (auto or manual). Roberto's flow uses `/clear`+reseed, not `/compact` (`[[paper-opportunity-clear-reseed-window-shrink-2026-06-12]]`). A pure session driven by `/clear` may **never** fire `PreCompact`, so the seed would never be written. Wrong trigger for this workflow.

**What it captures:** the last user prompt + last assistant text of the turn (already implemented), summarized into a dense markdown slot by `epoch_summarizer` (config `encoder`, fail-open → no mutation on summarizer failure).

**Where it writes:**
- Per-session chain: `/Users/roberto/antigravity/burnless/.burnless/epochs/<session_id>/NNN.md`, hierarchically consolidated at 10 slots/level by `epochs.py` (`append_epoch` → `consolidate_level`). Unchanged.
- **NEW — project-level rolling pointer:** `/Users/roberto/antigravity/burnless/.burnless/epochs/_rolling/seed.md`. After each successful `capture`, the Stop hook overwrites this file with the **current active chain of the most-recently-written session** (i.e. the just-captured session's `burnless epoch read`). This is the cross-`/clear` durable seed (see §1c).

**How `SessionStart` re-injects (next turn / after `/clear`):** `burnless_epoch_session.sh` already injects `burnless epoch read --chat-id $SID` as `additionalContext` under a `## Rolling memory (carry-forward)` header — once, at session start. SessionStart fires on **every** start source (`startup`, `resume`, `clear`, `compact`), so it re-seeds after `/clear` automatically. The only change is a **fallback**: if the per-session chain is empty (new id after `/clear`), read `_rolling/seed.md` instead (see §1c).

---

## 1b. Why O(N) not O(N²), and how it composes with prompt-cache

**O(N) not O(N²):**

- Naïve "carry the whole transcript forward" is **O(N²)**: at turn k the window holds all k turns, summed over N turns = N(N+1)/2 tokens processed.
- The epoch engine bounds the carried state. `append_epoch` adds one bounded slot per turn; `consolidate_level` folds every 10 slots of level L into **one** slot of level L+1 and moves originals to `originais/`. So the **active chain** length grows logarithmically in turns, and each slot is a fixed-size dense summary. Carried context per session start is **O(log N · slot_size) ≈ O(1)** in practice, and total work across the session is **O(N)** (one bounded summarize per turn) — never re-processing the whole history.
- `/clear`+reseed (`[[paper-opportunity-clear-reseed-window-shrink-2026-06-12]]`) caps the live window: measured **90k→42k (−53%)**, reaqueceu em 1 turn. The seed carries minimal state (next step + objective + refs), not the dead transcript. This is the window-level realization of the same bound.

**Prompt-cache composition (frozen-seed doctrine, `[[prefix-exact-not-volume-discount-frozen-seed-2026-06-12]]`):**

- Cache read (0.1×) happens **only** when the identical byte-sequence was previously written at the **same prefix offset**. Cache is exact-prefix, **not** a volume discount.
- Therefore the seed must be injected **once, at a stable prefix position** (SessionStart, right after the byte-stable `system+tools+CLAUDE.md` block), and must be **byte-identical** on reseed. The Stop→SessionStart path satisfies this; the System-B UserPromptSubmit path violates it (new offset every turn = write every turn).
- **HARD invariant for byte-stability:** the injected seed block must contain **no volatile bytes** — no timestamps, no turn counters, no run-ids, fixed slot order. `burnless epoch read` emits slots in deterministic `active_chain` order with `# <name>` headers (stable). The Stop hook must **not** stamp time into slot bodies. (The current `epoch_summarizer` prompt does not inject timestamps — keep it that way.) If the reseed block is byte-identical to the prior session's hot prefix and still within TTL, it reads at 0.1× instead of writing at 1.25× — the ~12.5× saving on that slice. Keepalive ping (already in burnless, <TTL) keeps it hot.

---

## 1c. Cross-`/clear` survival (the real new mechanism)

Problem: epochs are keyed by `session_id`; `/clear`/new-launch can rotate it; the new `SessionStart` would read an empty chain.

**Fix — project-level rolling seed pointer:**

1. On every successful `Stop` capture, write `/Users/roberto/antigravity/burnless/.burnless/epochs/_rolling/seed.md` = the active chain of the session that was just captured (overwrite, byte-stable content, no timestamp).
2. `SessionStart` (`burnless_epoch_session.sh`) logic becomes:
   - `CHAIN = burnless epoch read --chat-id $SID` (per-session, unchanged primary path).
   - **If `CHAIN` empty AND `_rolling/seed.md` exists → `CHAIN = cat _rolling/seed.md`** (fallback for rotated id after `/clear`).
   - Inject `CHAIN` under the existing `## Rolling memory (carry-forward)` header.
3. Result: a fresh session id after `/clear` still gets the last rolling state from disk, byte-stable → cache-friendly, O(1) carried.

This is the minimal change that makes rolling memory **survive `/clear` and re-seed from disk**, which is the stated goal.

---

## 1d. Mode ↔ engine: what actually toggles rolling memory

Two independent switches exist today; the simplified surface (§2) collapses them:

- **`.burnless/epochs.on` marker** (project-scoped) — gates BOTH epoch hooks (`epoch_stop` and `epoch_session` early-exit if absent). This is the real engine switch (`epochs.is_enabled`).
- **`cache_policy.rolling_compaction_enabled: false`** (config) — gates the **maestro/`burnless chat`** in-loop compaction (`cli.py` ~L1404 "Compaction OFF by default. An opt-in rollover mode can force a cycle"). It does **NOT** gate the pure-session epoch hooks. Leave it as-is for v1 (it governs `burnless chat`, a different surface).
- **Session mode** (`~/.burnless/state/session-<sid>.mode`, values today `off|partner|on|rollover`) — per-session UserPromptSubmit behavior in `burnless_mode_hook.sh`.

**Unification rule (v1):** the user-facing `rollover` mode becomes the single switch that means "epochs on for this project." Selecting `rollover` ensures `epochs.on` exists; the epoch hooks (already keyed per `chat-id`) do the rest. `rolling_compaction_enabled` stays scoped to `burnless chat` and is out of scope for the pure-session surface.

---

## 2. Simplified mode surface — `off` / `on` / `rollover`

### Current set (in `burnless_mode_hook.sh` + `burnless_mode.sh`)

| Mode | Today's behavior |
|---|---|
| `off` | no-op; pure Claude |
| `partner` | **no-op** ("assistant keeps reasoning, Burnless stays as execution boundary") — behaviorally identical to off in the hook |
| `on` | inject `[BURNLESS ON]` Maestro/delegate-only context (UserPromptSubmit, byte-stable, every turn — small) |
| `rollover` | `on` semantics + System-B per-turn capsule rebuild + seed.md + `/rewind` nudge |
| `menu`/`models` | not modes — show config table |

`native` is **not** a session mode in the hook; "native" appears only in capsule prose to mean "Claude Code's own loop." Nothing to migrate for a literal `native` mode.

### New set (exactly three)

| New mode | Meaning | Mechanism |
|---|---|---|
| `off` | Burnless disabled; pure Claude. | hook no-op; epoch hooks still gated by `epochs.on` marker but mode-off suppresses Maestro injection |
| `on` | Burnless engaged: Maestro / delegate-boundary. **No** rolling memory. | inject `[BURNLESS ON]` (unchanged) |
| `rollover` | `on` **+ rolling memory** (epoch write on `Stop`, reseed on `SessionStart`). | `[BURNLESS ON]` injection **+** ensure `epochs.on`; **remove** System-B per-turn capsule/seed/`/rewind` code path |

### Mapping / migration

| Old | New | Action |
|---|---|---|
| `off` | `off` | none |
| `on` | `on` | none |
| `partner` | **`on`** | `partner` was a no-op execution-boundary stance; fold into `on` (the delegate-boundary mode). Drop the "keep reasoning" nuance. |
| `rollover` | `rollover` | keep the name; **replace** the implementation (drop UserPromptSubmit seed rebuild + `/rewind`; rely on epoch Stop/SessionStart) |
| `menu`/`models` | unchanged | still surfaces config table, not a mode |

**Removed:** the `partner` mode string; System-B's per-turn capsule injection, `session-<sid>.seed.md` / `.rollover.md` / `.rollover.json` writes, gemma rotation compaction, and the `/rewind` nudge inside `burnless_mode_hook.sh`.

**Migration of existing on-disk state:** any `~/.burnless/state/session-*.mode` file whose content is `partner` is rewritten to `on` (one-shot sweep). `rollover`/`on`/`off` files are left as-is. The `/burnless` command parser accepts only `on|off|rollover|menu|models`; `partner` is silently coerced to `on` if typed.

---

## 3. Config: defaults for v1

- `.burnless/epochs.on` — **present** (engine enabled). `rollover` mode guarantees it.
- `cache_policy.rolling_compaction_enabled` — **stays `false`** (governs `burnless chat`, not pure-session epochs; no change in scope).
- Default session mode (no file, no `global.on`) — **`off`** (unchanged precedence in `burnless_mode.sh`: `BURNLESS_OFF=1` > session file > `global.on` > `off`). Note: `global.on` resolves to `on`, never `rollover`; rollover is always an explicit per-session choice in v1.
- `BURNLESS_ROLLOVER_TURNS` — **retired** for the pure-session path (was System-B's per-turn window). Epoch consolidation cadence is fixed at 10 slots/level by `epochs.py`; no env knob in v1.

---

## SILVER SPEC

> Paste-ready for a silver delegation. Design-faithful, surgical. Burnless project root: `/Users/roberto/antigravity/burnless`. Hooks live in `/Users/roberto/.claude/scripts/`.

### Goal
Unify rolling memory onto the epoch engine (`Stop` write, `SessionStart` read), add a cross-`/clear` project-level seed fallback, and collapse the session modes to exactly `off|on|rollover`.

### EDIT FILES
1. `/Users/roberto/.claude/scripts/burnless_epoch_stop.sh`
   - After the existing `burnless epoch capture …` call succeeds, append the active chain to a project-level pointer. Add, immediately after the capture line (keep it async-safe, fail-open):
     ```
     mkdir -p "$ROOT/.burnless/epochs/_rolling"
     /Users/roberto/.local/bin/burnless epoch read --chat-id "$SID" --root "$ROOT" > "$ROOT/.burnless/epochs/_rolling/seed.md" 2>/dev/null || true
     ```
   - Do NOT add any timestamp/turn-counter to written content (byte-stability invariant).

2. `/Users/roberto/.claude/scripts/burnless_epoch_session.sh`
   - After computing `CHAIN`, add a fallback BEFORE the `[[ -z "$CHAIN" ]] && exit 0` guard:
     ```
     if [[ -z "$CHAIN" && -f "$ROOT/.burnless/epochs/_rolling/seed.md" ]]; then
       CHAIN=$(cat "$ROOT/.burnless/epochs/_rolling/seed.md")
     fi
     ```
   - Leave the rest (jq additionalContext emission) unchanged.

3. `/Users/roberto/.claude/scripts/burnless_mode_hook.sh`
   - Mode set becomes exactly `off|on|rollover`. In the `/burnless` command parser, change the accepted set from `{"on","partner","off","rollover"}` to `{"on","off","rollover"}`; if the typed token is `partner`, coerce to `on` before `write_mode`.
   - Update the menu help strings to `/burnless on|rollover|off|menu` (drop `partner`).
   - REMOVE the entire System-B `rollover` block (the `build_capsule`/gemma/`seed.md`/`.rollover.*`/`/rewind` machinery). Replace the `if mode == "rollover":` body with: emit the SAME `[BURNLESS ON]` Maestro context as `mode == "on"`, then `raise SystemExit(0)`. (Rolling memory now comes entirely from the epoch hooks; this hook only injects the Maestro stance.)
   - Keep `mode == "partner"` accepted on READ for back-compat by treating it as `on` (coerce when reading the mode file: `if mode == "partner": mode = "on"`).

4. `/Users/roberto/.claude/scripts/burnless_mode.sh`
   - Accept-list for session file currently `on|partner|off`. Change to `on|partner|off|rollover`, and emit `on` when the file says `partner` (coerce). i.e. `case "$m" in on) echo on;; partner) echo on;; rollover) echo rollover;; off) echo off;; esac`.

5. **One-shot migration (run once, not a hook):** rewrite any `partner` mode file to `on`:
   ```
   for f in /Users/roberto/.burnless/state/session-*.mode; do
     [[ -f "$f" ]] && [[ "$(tr -d '[:space:]' < "$f")" == "partner" ]] && printf 'on' > "$f"
   done
   ```

### DO NOT (HARD PROHIBITIONS)
- Do NOT touch `/Users/roberto/antigravity/burnless/src/burnless/epochs.py` or `cli.py` — the engine is correct as-is.
- Do NOT flip `cache_policy.rolling_compaction_enabled` (it governs `burnless chat`, out of scope).
- Do NOT add timestamps, turn counters, run-ids, or any volatile bytes to the injected seed / `_rolling/seed.md` (breaks prefix-cache, breaks the −53% window win).
- Do NOT re-introduce per-turn (`UserPromptSubmit`) injection of the durable seed. Durable seed = `SessionStart` only.
- Do NOT remove `forgetless_auto_rank.sh` from `UserPromptSubmit` (query-specific volatile injection is intended to stay there).
- Do NOT run `git`.
- Do NOT delete existing epoch chain dirs under `.burnless/epochs/`.

### Verify
```sh
test -f /Users/roberto/.claude/scripts/burnless_epoch_stop.sh || exit 1
grep -q "_rolling/seed.md" /Users/roberto/.claude/scripts/burnless_epoch_stop.sh || exit 1
grep -q "_rolling/seed.md" /Users/roberto/.claude/scripts/burnless_epoch_session.sh || exit 1
grep -qE '"on", *"off", *"rollover"|"on","off","rollover"' /Users/roberto/.claude/scripts/burnless_mode_hook.sh || exit 1
grep -q "rollover" /Users/roberto/.claude/scripts/burnless_mode.sh || exit 1
test -z "$(grep -rl 'partner' /Users/roberto/.burnless/state/session-*.mode 2>/dev/null)" || exit 1
bash -n /Users/roberto/.claude/scripts/burnless_epoch_stop.sh || exit 1
bash -n /Users/roberto/.claude/scripts/burnless_epoch_session.sh || exit 1
bash -n /Users/roberto/.claude/scripts/burnless_mode_hook.sh || exit 1
bash -n /Users/roberto/.claude/scripts/burnless_mode.sh || exit 1
```

---

## Appendix — capsules consulted
- `[[fork-duplo-rolling-memory-sessionstart-vs-userprompt-2026-06-12]]` — SessionStart (1×) vs UserPromptSubmit (per-turn) cost fork; ~20k/session waste of per-turn seed.
- `[[paper-opportunity-clear-reseed-window-shrink-2026-06-12]]` — `/clear`+reseed 90k→42k (−53%), reaquece em 1 turn; replaces `/rewind`.
- `[[rolling-memory-handoff-next-chat-2026-06-12]]` — handoff; next step = move durable seed off UserPromptSubmit; byte-identical reseed guard.
- `[[prefix-exact-not-volume-discount-frozen-seed-2026-06-12]]` — cache = exact-prefix match; frozen seed ≥4096 tok + keepalive → 0.1× read.
