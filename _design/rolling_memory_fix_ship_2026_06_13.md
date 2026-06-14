# Rolling Memory — Fix + Ship Design

**Date:** 2026-06-13
**Author:** d685 (gold/opus investigation)
**Status:** design + silver spec (NOT implemented)
**Scope:** Burnless epoch "rolling memory" subsystem — the 0-byte seed bug, chat-id semantics, and the ship gap that keeps it from installing on a clean machine.

Grounded entirely in code read during this investigation:
- `/Users/roberto/antigravity/burnless/src/burnless/epochs.py`
- `/Users/roberto/antigravity/burnless/src/burnless/cli.py` (`cmd_epoch`, lines 1264–1327; parser 2106–2122)
- `/Users/roberto/antigravity/burnless/src/burnless/init_claude_code.py`
- Live hooks `/Users/roberto/.claude/scripts/burnless_epoch_stop.sh`, `/Users/roberto/.claude/scripts/burnless_epoch_session.sh`, `/Users/roberto/.claude/scripts/burnless_mode.sh`
- Templates `/Users/roberto/antigravity/burnless/templates/scripts/`

---

## 0. How the subsystem is wired (ground truth)

**Stop hook** (`/Users/roberto/.claude/scripts/burnless_epoch_stop.sh`):
1. Reads stdin JSON → `session_id` (SID), `cwd`, `transcript_path`.
2. Walks up from `cwd` to find a dir with `.burnless/config.yaml` → `ROOT`.
3. Early-exits unless `ROOT/.burnless/epochs.on` exists (line 21).
4. Extracts the last user + last assistant turn from the transcript via inline python.
5. In a backgrounded subshell (`{ …; } &`):
   - `burnless epoch capture --chat-id "$SID" --root "$ROOT"` (stdout+stderr discarded)
   - `burnless epoch read --chat-id "$SID" --root "$ROOT" > "$ROOT/.burnless/epochs/_rolling/seed.md"`

**SessionStart hook** (`/Users/roberto/.claude/scripts/burnless_epoch_session.sh`):
1. Same ROOT discovery + `epochs.on` gate.
2. `CHAIN=$(burnless epoch read --chat-id "$SID" --root "$ROOT")`.
3. If `CHAIN` empty AND `_rolling/seed.md` exists → `CHAIN=$(cat _rolling/seed.md)` (the fallback).
4. Emits `{hookSpecificOutput:{hookEventName:"SessionStart", additionalContext: "## Rolling memory…" + CHAIN}}`.

**CLI** (`cmd_epoch`, cli.py:1264):
- `capture` (1297): reads stdin → `s = epoch_summarizer(root)(text)`. **If `s is None` → prints a stderr warning and `return 0` WITHOUT writing anything** (1301–1303). Else `append_epoch` + consolidation, prints the slot name.
- `read` (1314): `chain = active_chain(root, chat_id)`; prints each slot file. Empty chain → prints nothing.

**Storage** (`epochs.py`):
- `epoch_dir(root, chat_id) = root/.burnless/epochs/<chat_id>` (line 15) — **chat-id is a hard path component**.
- `active_chain(root, chat_id)` (100) reads strictly that dir; nonexistent dir → `[]` (103–104).
- `epoch_summarizer` (141) calls a live LLM: ollama `/api/generate`, else `claude -p --model <bronze> … --timeout 8s` (185–192). **Any exception → `return None`** (202–203).

---

## 1. Root cause of the 0-byte seed

### The destructive line
`burnless_epoch_stop.sh:56`:
```
burnless epoch read --chat-id "$SID" --root "$ROOT" > "$ROOT/.burnless/epochs/_rolling/seed.md"
```
The `>` redirection **truncates `seed.md` to 0 bytes before `epoch read` produces a single byte**. If `read` emits nothing, the file is left empty — and the previous good seed is destroyed.

### Why `read` emits nothing right after `capture`
`epoch read` prints `active_chain(root, SID)`. The chain is empty whenever the chat-dir `root/.burnless/epochs/<SID>/` has no slot files. That dir is created **only** by `append_epoch`, which `capture` calls **only if the summarizer returned a non-None string**. The summarizer is fail-open: on any failure it returns `None`, and `cmd_epoch` then does `return 0` without `append_epoch` (cli.py:1301–1303). No slot file → empty chain → empty `read` → truncated 0-byte seed.

The hook discards capture's stderr (`>/dev/null 2>&1`, line 55), so the `warning: summarizer failed (fail-open, no mutation)` message is invisible — the failure is silent.

### Why the summarizer fails in the hook (reproduced)

**REPRO 1 — real config, real PATH, capture writes nothing:**
```
$ printf 'PERGUNTA:\nfix the 0-byte seed bug...\n\nRESPOSTA:\nroot cause...\n' \
    | burnless epoch capture --chat-id REPRO685 --root /Users/roberto/antigravity/burnless
warning: summarizer failed (fail-open, no mutation)
capture rc=0
$ burnless epoch read --chat-id REPRO685 --root /Users/roberto/antigravity/burnless | wc -c
0
$ ls .burnless/epochs/REPRO685/
ls: No such file or directory
```
On the live encoder config (`encoder.model: null` → `DEFAULT_TIER_MODELS["bronze"]`, provider defaults to `anthropic` → the `claude -p` path), the summarizer returned `None`, capture created **no dir**, and `read` produced **0 bytes**. This is the exact launch-audit symptom, reproduced on the real config.

**REPRO 2 — `read` of a nonexistent chat-id is 0 bytes (no race needed):**
```
$ burnless epoch read --chat-id NONEXISTENT_$$ --root /Users/roberto/antigravity/burnless | wc -c
0
```

**REPRO 3 — `claude` is not guaranteed on a hook's PATH:**
```
$ env -i PATH=/usr/bin:/bin sh -c 'command -v claude || echo "claude NOT FOUND"'
claude NOT FOUND in minimal PATH
```
Claude Code hooks run with a reduced environment. `epoch_summarizer` shells out to `claude` (falling back to the literal string `"claude"` when `_claude_binary()` returns None, epochs.py:181–184). With `claude` off PATH → `FileNotFoundError` → `None` → no capture.

**The current live seed** (`.burnless/epochs/_rolling/seed.md`, 318 bytes) is not 0 bytes today; it holds an LLM refusal ("Sem conversa válida para resumir — PROBE2 q/a são placeholders"). That is a *second* failure mode (garbage seed from probe input, §1.1) and confirms the path is flaky: identical inputs produce either a None (0-byte), a refusal (garbage), or a real summary depending on the live LLM call.

### Contributing factors, ranked
1. **Summarizer is a live LLM call with an 8s timeout** (epochs.py:191) executed inside the Stop hook while the machine is still finishing a turn → intermittent `TimeoutExpired` → `None`. Explains the *intermittent* 0-byte.
2. **`claude` not on the hook PATH** → `FileNotFoundError` → `None` (REPRO 3).
3. **`>` truncates the seed before content is known** → a failed/empty read is destructive, not a no-op.
4. **capture's stderr is discarded** → silent failure.
5. **capture and read are two separate processes** → read depends on capture having mutated disk; any capture no-op desyncs them.

### 1.1 Secondary failure: garbage seed
When the summarizer *does* answer but the input is a probe/empty turn, the LLM emits a meta-reply ("give me real content") that `append_epoch` stores verbatim as the epoch summary, and that propagates into the seed. The fix below (capture emits chain only on a real append; guarded seed write) does not fully cure this — it should be backed by a minimum-input guard (skip capture when extracted text is below a threshold), noted in the spec as a soft check.

### The fix (design — three layers)
**Layer A (must-have): non-destructive seed write.** Stop hook writes to a temp file and atomically promotes it **only if non-empty**:
```
tmp="$ROOT/.burnless/epochs/_rolling/.seed.md.tmp.$$"
burnless epoch capture --chat-id "$SID" --root "$ROOT" > "$tmp" 2>/dev/null
[[ -s "$tmp" ]] && mv -f "$tmp" "$ROOT/.burnless/epochs/_rolling/seed.md" || rm -f "$tmp"
```
A summarizer failure now **preserves the last good seed** instead of zeroing it.

**Layer B (structural): `capture` emits the chain to stdout.** Change `cmd_epoch` `capture` so that on a successful append it prints the *active chain* (not just the slot name) to stdout. Then the Stop hook makes **one** call (`capture > tmp`) and drops the second `read` entirely — eliminating the capture→read desync and the empty-dir race. On summarizer-None, capture prints nothing → tmp empty → Layer A keeps the old seed. (Slot-name stdout is preserved on stderr or behind a flag to avoid breaking other callers; the spec uses an opt-in `--emit-chain` flag so existing `capture` output contract is unchanged.)

**Layer C (hardening): make the summarizer survive the hook.** In the shipped Stop hook export a PATH that includes `$HOME/.local/bin` and resolve the binary with `command -v` (so nested `claude`/`burnless` resolve), and raise the summarizer timeout for the epoch path from 8s → 20s. This reduces the None rate at the source.

Recommended ship = **A + B + C**. A alone stops data loss; B removes the architectural race; C reduces failures.

---

## 2. chat-id vs latest-chain semantics — and the fallback decision

**Finding:** `epoch read --chat-id X` is **strictly isolated by chat-id**. `active_chain(root, X)` reads only `root/.burnless/epochs/X/` (`epoch_dir`, epochs.py:15). It does **not** return the project's latest active chain regardless of X. A nonexistent chat-dir returns `[]` (REPRO 2: `read --chat-id NONEXISTENT → 0 bytes`).

**Consequence for the SessionStart hook:** a **fresh session has a new `session_id`** that has no epoch dir yet (the prior session wrote under the *prior* SID). So `epoch read --chat-id <new SID>` is **always empty on a fresh session**. The only thing that carries memory across sessions is the project-level `_rolling/seed.md`.

**Decision: KEEP the `_rolling/seed.md` fallback. It is load-bearing.** Without it there is no cross-session carry-forward at all. The chat-id `read` in the SessionStart hook only ever hits on a *resumed* session (same SID), where it's a strict superset is not guaranteed — so keep `read` as the primary (same-session resume) and `seed.md` as the fallback (cross-session). Both paths are needed; neither is redundant.

(If a future design wants true per-project latest-chain reads, that requires a new mode in `active_chain`/`cmd_epoch` keyed on mtime across chat-dirs — out of scope here. The seed-file fallback is the simpler, already-correct mechanism.)

---

## 3. Ship plan

### 3.1 What does NOT ship today
- `templates/scripts/` holds only `burnless_mode_hook.sh`, `burnless_offload_hook.sh`, `burnless_session_seed.sh`.
- `init_claude_code.py` `_MANAGED` installs only those three (plus two agents + a compact hook). It wires `UserPromptSubmit` (mode) + `SessionStart` (session_seed) but **registers no `Stop` hook and no epoch SessionStart hook**.
- The two epoch hooks (`burnless_epoch_stop.sh`, `burnless_epoch_session.sh`) exist only in the live `~/.claude/scripts/` and **hardcode `/Users/roberto/.local/bin/burnless`** — not portable.
- The per-project `.burnless/epochs.on` marker that gates both hooks (stop:21, session:20) is never created by the installer.

### 3.2 Files to add to shipped templates
Create, genericized (no personal absolute paths):
- `/Users/roberto/antigravity/burnless/templates/scripts/burnless_epoch_stop.sh`
- `/Users/roberto/antigravity/burnless/templates/scripts/burnless_epoch_session.sh`

Each must, at the top:
```
export PATH="$HOME/.local/bin:$PATH"
BURNLESS_BIN="$(command -v burnless || echo "$HOME/.local/bin/burnless")"
```
and call `"$BURNLESS_BIN" epoch …` instead of the hardcoded path. (`init_claude_code.py` resolves *install-time* paths in python; hook *runtime* resolution must be `command -v` in bash because the session PATH differs from install PATH.)

**`burnless_mode.sh` is out of scope for rolling memory.** It is referenced only by `burnless_encoder_inject.sh` (and itself), and by **no** epoch hook. It is a separate mode-resolver shipping gap; do not bundle it into this fix.

### 3.3 Installer changes (`init_claude_code.py`)
1. **`_MANAGED`** — append:
   ```
   ("scripts/burnless_epoch_stop.sh",    ".claude/scripts/burnless_epoch_stop.sh"),
   ("scripts/burnless_epoch_session.sh", ".claude/scripts/burnless_epoch_session.sh"),
   ```
2. **`is_wired`** — add detection booleans:
   - `stop`: any hook in `hooks.Stop` whose command contains `burnless_epoch_stop.sh`.
   - `epoch_session`: any hook in `hooks.SessionStart` whose command contains `burnless_epoch_session.sh` (distinct from the existing `sessionstart`/session_seed bool).
   Return them in the dict.
3. **`wire_settings_hook`** — register, idempotently:
   - `hooks.Stop` ← `{"type":"command","command":"bash ~/.claude/scripts/burnless_epoch_stop.sh","async": true}` if not already present.
   - `hooks.SessionStart` ← `{"type":"command","command":"bash ~/.claude/scripts/burnless_epoch_session.sh","timeout":10}` if not already present.
   - Guard each append on the new `is_wired` booleans so re-runs don't duplicate. Update the `already_*` short-circuit so it only returns `already-wired` when ALL four (mode, seed, stop, epoch_session) are present.
4. **`unwire_settings_hook`** — also strip `burnless_epoch_stop.sh` from `hooks.Stop` and `burnless_epoch_session.sh` from `hooks.SessionStart`, mirroring the existing prune loops (preserve other hooks in the same groups; drop now-empty groups).
5. **Uninstall** — `_MANAGED` drives file removal, so the two new files are removed automatically; the `unwire` change handles settings.

### 3.4 Enabling rolling memory for the mode (`epochs.on`)
Both hooks early-exit unless `ROOT/.burnless/epochs.on` exists. The installer is **global** (`~/.claude`); the marker is **per-project**. So enabling must happen at project scope:
- Recommended: have `burnless on` / the project setup path call `epochs.set_enabled(root, True)` (epochs.py:240) so that turning Burnless on in a project also turns on rolling memory, creating `.burnless/epochs.on`.
- Already available as the explicit `burnless epoch on` command (cli.py:1275). Document it as the manual switch.
- `is_enabled` (epochs.py:230) is also true when `config.epochs.enabled` is set, so a project `config.yaml` with `epochs: {enabled: true}` is an alternative that needs no marker file.

Spec wires the marker via `burnless epoch on` at minimum and notes the `burnless on` integration as a follow-up (kept out of the hard spec to avoid touching mode-toggle logic blind).

---

## SILVER SPEC

**Tier:** silver (Sonnet). **Goal:** ship rolling memory — add portable epoch hooks to templates, fix the 0-byte seed (non-destructive write + capture emits chain), wire the installer. **Do NOT** redesign storage, touch `burnless_mode.sh`, or change `active_chain`'s chat-id isolation.

### Files + exact edits

**1. CREATE `/Users/roberto/antigravity/burnless/templates/scripts/burnless_epoch_stop.sh`**
Port the live `/Users/roberto/.claude/scripts/burnless_epoch_stop.sh` with these changes:
- After `set`/shebang, add:
  ```
  export PATH="$HOME/.local/bin:$PATH"
  BURNLESS_BIN="$(command -v burnless || echo "$HOME/.local/bin/burnless")"
  ```
- Replace BOTH `/Users/roberto/.local/bin/burnless` occurrences with `"$BURNLESS_BIN"`.
- Replace the capture-then-read block (live lines 54–57) with a single guarded, non-destructive write:
  ```
  mkdir -p "$ROOT/.burnless/epochs/_rolling"
  tmp="$ROOT/.burnless/epochs/_rolling/.seed.md.tmp.$$"
  echo "$extracted" | "$BURNLESS_BIN" epoch capture --chat-id "$SID" --root "$ROOT" --emit-chain > "$tmp" 2>/dev/null
  if [[ -s "$tmp" ]]; then mv -f "$tmp" "$ROOT/.burnless/epochs/_rolling/seed.md"; else rm -f "$tmp"; fi
  ```
  (Keep it inside the existing `{ …; } &` background subshell.)

**2. CREATE `/Users/roberto/antigravity/burnless/templates/scripts/burnless_epoch_session.sh`**
Port the live `/Users/roberto/.claude/scripts/burnless_epoch_session.sh` with:
- Same `export PATH` + `BURNLESS_BIN` header.
- Replace the `/Users/roberto/.local/bin/burnless` occurrence with `"$BURNLESS_BIN"`.
- Keep the `_rolling/seed.md` fallback exactly as-is (it is load-bearing per §2).

**3. EDIT `/Users/roberto/antigravity/burnless/src/burnless/cli.py`** — `cmd_epoch`, `capture` branch (lines 1297–1312):
- Add `--emit-chain` to the `capture` subparser (cli.py:2111, via `epoch_common` or a per-subparser `add_argument("--emit-chain", action="store_true", dest="emit_chain", default=False)`).
- In the `capture` branch: after a successful `append_epoch` + consolidation, if `getattr(args,"emit_chain",False)` is true, print the active chain (reuse the `read` rendering: for each `f` in `active_chain(root_path, chat_id)`, print `f"# {f.name}\n"`, file text, blank line) **instead of** just `path.name`. When `s is None`, keep the existing fail-open `return 0` (emits nothing → empty stdout → Stop hook preserves old seed).

**4. EDIT `/Users/roberto/antigravity/burnless/src/burnless/init_claude_code.py`:**
- `_MANAGED` (after line 18): append the two epoch-hook tuples (see §3.3.1).
- `is_wired`: after computing `sessionstart`, compute `stop` (scan `hooks.get("Stop",[])` for `burnless_epoch_stop.sh`) and `epoch_session` (scan `hooks.get("SessionStart",[])` for `burnless_epoch_session.sh`); add both to the returned dict.
- `wire_settings_hook`: extend the `already_*` short-circuit to require all four; add idempotent appends to `hooks["Stop"]` (`burnless_epoch_stop.sh`, `"async": true`) and `hooks["SessionStart"]` (`burnless_epoch_session.sh`, `timeout 10`).
- `unwire_settings_hook`: add prune loops that strip `burnless_epoch_stop.sh` from `hooks["Stop"]` and `burnless_epoch_session.sh` from `hooks["SessionStart"]`, dropping now-empty groups, setting `changed=True` when removed.

### HARD PROHIBITIONS
- Do NOT modify `epochs.py` `active_chain` / `epoch_dir` (chat-id isolation stays).
- Do NOT remove the `_rolling/seed.md` fallback in the SessionStart hook.
- Do NOT ship any path containing `/Users/roberto/` inside the two template hook files.
- Do NOT add, edit, or wire `burnless_mode.sh` (out of scope).
- Do NOT change the default `capture` stdout contract (slot-name) when `--emit-chain` is absent.
- Do NOT run git.
- Bronze must not touch files >200 lines — `cli.py` and `init_claude_code.py` exceed that; this is a **silver** job.

### Verify
```sh
test -f /Users/roberto/antigravity/burnless/templates/scripts/burnless_epoch_stop.sh || exit 1
test -f /Users/roberto/antigravity/burnless/templates/scripts/burnless_epoch_session.sh || exit 1
grep -q "BURNLESS_BIN" /Users/roberto/antigravity/burnless/templates/scripts/burnless_epoch_stop.sh || exit 1
grep -q "BURNLESS_BIN" /Users/roberto/antigravity/burnless/templates/scripts/burnless_epoch_session.sh || exit 1
grep -L "/Users/roberto/.local/bin/burnless" /Users/roberto/antigravity/burnless/templates/scripts/burnless_epoch_stop.sh | grep -q . || exit 1
grep -L "/Users/roberto/.local/bin/burnless" /Users/roberto/antigravity/burnless/templates/scripts/burnless_epoch_session.sh | grep -q . || exit 1
grep -q "seed.md.tmp" /Users/roberto/antigravity/burnless/templates/scripts/burnless_epoch_stop.sh || exit 1
grep -q "_rolling/seed.md" /Users/roberto/antigravity/burnless/templates/scripts/burnless_epoch_session.sh || exit 1
grep -q "burnless_epoch_stop.sh" /Users/roberto/antigravity/burnless/src/burnless/init_claude_code.py || exit 1
grep -q "burnless_epoch_session.sh" /Users/roberto/antigravity/burnless/src/burnless/init_claude_code.py || exit 1
grep -q "emit_chain\|emit-chain" /Users/roberto/antigravity/burnless/src/burnless/cli.py || exit 1
python3 -c "import ast,sys; ast.parse(open('/Users/roberto/antigravity/burnless/src/burnless/init_claude_code.py').read())" || exit 1
python3 -c "import ast,sys; ast.parse(open('/Users/roberto/antigravity/burnless/src/burnless/cli.py').read())" || exit 1
bash -n /Users/roberto/antigravity/burnless/templates/scripts/burnless_epoch_stop.sh || exit 1
bash -n /Users/roberto/antigravity/burnless/templates/scripts/burnless_epoch_session.sh || exit 1
```
