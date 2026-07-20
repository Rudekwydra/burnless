# Burnless — Doctrine (canonical)

The single source of truth for *how burnless works and how to use it*. Architecture lives in
`PROTOCOL.md`; the verified command reference lives in `docs/COMMANDS.md` (checked against `--help`).
**Code wins over memory** — when an instruction file disagrees with the code, the code is right.

Delegate work instead of editing files directly when possible.

---

## Commands (core)

```
burnless route "TASK"          # preview tier/agent, no run
burnless delegate "TASK"       # create dXXX, no run
burnless run dXXX              # execute (exit 0 only if worker OK)
burnless do "TASK" --tier T    # atomic delegate + run
burnless read|capsule|log dXXX # inspect a delegation
burnless status                # project health
burnless metrics               # token savings + counters
```

Full flags + semantics: `docs/COMMANDS.md`.

---

## Tiers are ROLES, not models

A tier is a **role**. Which `provider + model` runs each role is your choice, set in
`.burnless/config.yaml` per level. Point every level at one model (all-local gemma, or all-opus) or
mix freely. Always check the project config before assuming a model.

| tier | role | shipped default |
|---|---|---|
| gold | architecture, structural refactor, decisions | opus |
| silver | implementation with tight spec + HARD PROHIBITIONS | sonnet |
| bronze | reads, summaries, classification, ops shell, local code via tool-calling | haiku *or* ollama-local (gemma) |
| diamond | irreversible / second opinion | opt-in only — never auto-routed |

`diamond` is reachable only via `--tier diamond`; the router never selects it.

**Spec quality picks the tier:** compiles-in-your-head → bronze; needs thinking through → silver;
needs deciding-between-architectures → gold. Wrong tier costs money (over-provision) — don't tier-creep.

**Local models as real workers.** An `ollama-local` agent with `tools: true` runs an agentic
tool-calling loop (read/write/shell over the ollama HTTP API) and edits the filesystem like any other
worker — set `bronze: {provider: ollama-local, model: <gemma>, tools: true}` and `burnless do --tier
bronze` works end to end. Without `tools`, an ollama agent is single-shot text (summaries/classify).

---

## Engagement modes (your assistant)

Two modes only — `/burnless off|on`:

- **off** — raw chat, no Burnless.
- **on** — the assistant is the Maestro: compress intent and delegate via the tiers, **plus** rolling
  memory (epoch hooks keep context O(N), surviving `/clear`). This is the one efficient mode.

Details + the hook: `docs/USING_BURNLESS_FROM_YOUR_LLM.md`. In `on` the assistant compresses your intent
into a capsule for the Maestro and expands the Maestro's response back to natural language. (Legacy
`partner`/`rollover` are gone — both fold into `on`.)

---

## Compression

Capsule compression is fixed and faithful (~150 chars/field, ≤12 list items, full paths, dedupe only).
There is no `--mode` knob; debug a raw capsule via `burnless log` / direct read.

**Compression boundary (load-bearing):** compress memory, transit, and worker *output* — **never the
live worker instruction or contract**. Compressing the instruction degrades fidelity (empirically
re-validated 2026-06-13: a compressed instruction scored 0/3 correct vs 2/3 for the full instruction
on the same task). The spec the worker must follow stays full-fidelity; only what's stored or returned
is compressed.

---

## Timeouts

`do` and `run` accept `--timeout` / `--stale-timeout-s` (forwarded from `do` to the run). `delegate`
does **NOT** accept timeout flags. `--force` is accepted by `delegate` only — `do` does **NOT**.

Never pipe `do`/`run` through `tail`/`head` — it masks the exit code. Capture with `> file 2>&1`.

---

## Workflow

1. **Commit the working tree before delegating** — workers share the tree and may reset files. For
   shared files, isolate with `git worktree add`.
2. **End every code spec with a `## Verify` fenced shell block** asserting the DoD. The runner re-runs
   it after the worker and demotes `OK → PART` on failure — deterministic, written to the delegation
   log. Trust an `OK` that survived the gate; reserve manual audit for what shell can't encode.
   - Footgun: use `! grep -q PATTERN file` for absence checks — never `grep -c` / `diff` (they exit 1
     on the good state).
3. **PART → reject + re-spec smaller. Never merge partial work.**
4. **Gold delegations return everything in one roundtrip:** decision + trade-offs, implementation plan,
   and a tight spec with HARD PROHIBITIONS ready to paste into a bronze/silver delegation.

---

## Ops

- **Tier escalation policy** gates tier-creep: if the router classifies a task at a lower tier and you
  request a higher one without `--force`, burnless blocks and prints the full decision plus the exact
  `--force` command to proceed. Set `routing.escalation_policy: off | explain | block | confirm` in
  config; `BURNLESS_HARDCORE=1` (env) forces `block`. Inspect any decision with `burnless route "<spec>" --tier <t> --explain`.
- Config commands use **absolute paths** — subprocess workers don't inherit full `PATH`. `burnless
  init` resolves them via `shutil.which()`.
- Before pushing the public repo: run `./scripts/public_git_check.sh`.

---

## Rolling memory (epochs)

Rolling memory runs in your **Claude CLI session** via hooks (no `burnless chat` — that was removed). The `Stop` hook summarizes each turn into `.burnless/epochs/<session>/` (consolidating every 10 slots) and the `SessionStart` hook re-injects the rolling summary — **including after `/clear`**, via the project-level `_rolling/seed.md` fallback (since `/clear` rotates the session id, the per-session chain is empty and the fallback carries state forward). Keeps the maestro's context Θ(N), not Θ(N²). Toggle: `burnless epoch on|off`.

**On-disk format:** checkpoints and epoch exports use EN structural markers (`## Current focus`, `Q:`/`A:`, …) with `format_version: 2` by default; readers normalize back to the canonical internal keys, so consumers are format-agnostic. Set `format.en_markers: false` in `config.yaml` to keep PT markers on disk.

### On-disk layout per session

`.burnless/epochs/sessions/<host>/<session_id>/` holds four artifacts, each with a distinct role — the split is deliberate, not incidental:

- **`living.md`** — the semantic layer: compacted narrative in prose/bullets (`## Foco atual`, `## Threads abertas`, `## Decisões`, …), meaning-preserving, not a transcript. Each bullet carries an inline anchor `[chat:<sha256>·t<N>]` pointing back to the exact raw exchange, so a future turn can re-read full fidelity on demand instead of trusting the summary blindly.
- **`state.json`** — the pointer/tiering layer, physically separate from the prose: `contracts` (paths that must be re-read in full, never compressed), `refs` (path + why + line-range, reread on demand rather than inlined), `open_threads`, and `recuperaveis`/`recuperaveis_unparsed` (the recoverable-but-lossy tier — the only layer where compression risk actually concentrates).
- **`checkpoint.json`** — a durable snapshot bundling `living_md` + `harvested_state` (= `state.json`'s content) plus bookkeeping (`generation`, `applied_through`, `journal_head`, `chain_id`, `content_hash`) into one file, so `SessionStart` restore is a single read instead of joining three files live.
- **`journal/NNNNNN-sha256_*.json`** — one file per turn, written synchronously by the `Stop` hook (`burnless epoch journal-append`) before the turn is considered idle. Raw structured exchange record, never summarized at write time. **Scope: the dialogue, not the work** — each record captures the final `user_text`/`assistant_text` verbatim plus touched-file paths and transcript pointers (`transcript_path`, message UUIDs); tool outputs and mid-turn steps are *not* journaled. A load-bearing finding that lives only in a tool result must be restated in the assistant's final message to survive compaction; deep recovery of anything else goes through the transcript pointer.

### Restore = checkpoint + pending delta (an un-folded turn is never lost)

`compact-pending` folds journal records into `checkpoint.living_md` periodically (batched, not necessarily every turn — `applied_through` lagging behind the real `journal_head` on disk is expected and normal). Restore does not read only the folded checkpoint: `render_restore` (`src/burnless/recovery.py:2323`) computes `pending = [r for r in records if r.seq > checkpoint.applied_through]` (~line 2353) and injects `checkpoint.living_md` **plus every pending record** into the next session. A lag in the fold step is a performance detail, not a correctness gap — nothing written to the journal is ever lost on restore, compacted or not.

### Two rollover paths, different guarantees

- **Plain chat/IDE session (no pty)** — a `UserPromptSubmit` hook (`clear_hint_inject.sh`) measures hot tokens against a threshold and, once crossed, only *injects an instruction* telling the model to write `_rolling/live_handoff.md` and then suggest `/clear` to the human. Nothing here can execute `/clear` itself — the human decides. `live_handoff.md` (Camada B) is a voluntary, model-authored reflection written when the model judges the moment safe (never mid-delegation/mid-edit). It is a quality booster layered on top of the mechanical journal, not a required safety net: restore already reconstructs correctly from `journal` + `checkpoint` alone even when `live_handoff.md` is stale or absent.
- **`burnless pilot`/`pty` session** — an outer Python process owns the host CLI as a pty child. A background thread (`monitor_rollover_loop`, `src/burnless/pilot/rollover.py:345`) polls token usage; `should_rollover` (line 186) only fires once `run_state.idle == True` — never mid-tool-call, always between turns. Once armed, `arm_rollover`/`prepare_rollover` write the handoff pointer, then the wrapper sends `SIGTERM` to the whole child process group (`cli.py:2640`) and spawns a fresh `claude` process with a new session id (`cli.py:2888-2920`). The new process's `SessionStart` hook restores via the same checkpoint+pending mechanism above. This is a real kill+respawn, not the CLI's native `/clear` — continuity is reconstructed entirely from disk, not from any in-process state surviving the kill.

---

## Spec Authoring — Pre-Dispatch Checklist (6 Rules)

**When:** Before every `burnless do` or `burnless delegate`. **Why:** These rules prevent systematic errors discovered in TEST 1 audit (2026-06-13). **Who checks:** Pre-flight validator (automatic) + you (mental checklist).

### Rule 1: Verify block MUST be fenced [GATED — cli.py blocks exit 6]
The `## Verify` section must be inside a shell code block (```sh ... ```). If unfenced, the extractor returns an empty list, the gate disables silently, and the delegation passes with `OK` even if it should fail.

✅ Correct:
```
## Verify
```sh
test -f /path/to/file || exit 1
```
```

❌ Wrong:
```
## Verify
test -f /path/to/file || exit 1
```

**Pre-flight check:** Regex `^##\s*Verify` → must be followed by ````sh` within a few lines.

---

### Rule 2: Each Verify check = 1 line [GATED — runner ABORT]
The validator splits the fenced block by newlines, executing each line as a separate shell command. Multi-line commands (if/else/fi, loops, heredocs) break because the lines become incomplete shell fragments.

✅ Correct:
```sh
grep -q 'pattern' /path/to/file || exit 1
```

❌ Wrong:
```sh
if grep -q 'pattern' /path/to/file; then
  echo "found"
else
  exit 1
fi
```
→ Parsed as three commands: `if grep ... ; then` (syntax error: where's the command after `then`?), `echo ...`, `exit 1`, `fi`.

**Pre-flight check:** Count semicolons in each line; collapse long commands with `||`, `&&`, but no `if/fi` or loops inside a single check.

#### Rule 2b: No command-substitution `$(...)` or backticks in a check [GATED — cli.py blocks exit 6]

A check that *looks* like one clean line still aborts if it contains `$(...)` or `` `...` ``. The runner executes each line via `/bin/sh -c`, and command-substitution expands unpredictably (nested quoting, whitespace, exit-code masking). The pre-flight blocks the whole dispatch with:
`[BLOCK] burnless: bloco ## Verify usa backtick ou $(...) (command-substitution).`

The gate is loud and clear — but this is the **single most recurrent authoring footgun** (blocked 4×: a literal backtick fence, then `test -z "$(grep ...)"`, then `test $(wc -l < FILE) -ge 40`). Two rules kill the whole class:

1. **Absence** → `! grep -q 'PATTERN' /abs/file` (never `test -z "$(grep ...)"`, never `grep -c`).
2. **Size / count / "at least N"** → assert *content that only exists when big enough*, not a number. Grep for a required late section/marker instead of counting lines/bytes.

Verify cookbook (wrong → right):

| Intent | ❌ Blocked (`$(...)`) | ✅ Single-line grep |
|---|---|---|
| file exists & non-empty | — | `test -s /abs/out.md` |
| section present | — | `grep -q '## Section' /abs/out.md` |
| absence of pattern | `test -z "$(grep -n X /abs/f)"` | `! grep -q 'X' /abs/f` |
| "at least N lines/items" | `test $(wc -l < /abs/f) -ge 40` | grep the last required marker: `grep -q '## Final Section' /abs/f` |
| count ≥ threshold | `test $(grep -c X /abs/f) -ge 3` | assert the 3 specific expected values each with its own `grep -q` line |

**When you genuinely need computed logic** (real counting, math, JSON field assertions): put it in a `.py` file and call it from a single Verify line — `python3 /abs/check.py` — whose exit code is the boolean. Never inline the computation.

---

### Rule 3: All paths are absolute [GATED — cli.py blocks exit 6]
Workers execute in an isolated cwd (`/private/tmp/claude-502/<uuid>/`). Relative paths like `src/file.py` are relative to the temp dir, not the project root, so file checks silently fail or read the wrong files.

**Scope = the ENTIRE spec body, not just action-target paths or the `## Verify` block.** The validator scans all prose. A path mentioned only *illustratively* or as a *reference* still triggers the block — e.g. a HARD-PROHIBITION line saying "do not edit `src/` or touch `.burnless/config.yaml`" will fail with exit 6, even though those paths are not action targets. If you must name a path anywhere in the spec (prose, prohibitions, examples), write it absolute (`/Users/roberto/antigravity/burnless/.burnless/config.yaml`) or phrase it non-path-like ("the source tree", "the project config"). This footgun bit the author of this very doc on 2026-06-13.

✅ Correct:
```
File: /Users/roberto/antigravity/burnless/src/burnless/doctor.py
## Verify
```sh
test -f /Users/roberto/antigravity/burnless/src/burnless/doctor.py || exit 1
```
```

❌ Wrong:
```
File: src/burnless/doctor.py
## Verify
```sh
test -f src/burnless/doctor.py || exit 1
```

**Pre-flight check:** Regex `-v "^/"` to flag any path not starting with `/` (relative). Prepend project root.

---

### Rule 4: Bronze doesn't edit large files (>200 lines) [MENTAL ONLY — not enforced in code]
Haiku and local Gemma workers emit tool-call markup (`<antml>...</<tool>`) inside file edits when the file is large. This markup leaks into the output, corrupting JSON/code with `[3D[K` ANSI sequences or mismatched brackets.

✅ Correct: Use Bronze for read-only, classification, or small new files.  
✅ Correct: Use Silver or Gold for any mutation of an existing large file.

❌ Wrong: `burnless do --tier bronze "Edit /Users/.../doctor.py (500 lines) and add function X"`  
→ Markup corruption → SyntaxError → PART.

**Pre-flight check:** If spec mentions edit + file >200 lines → warn or auto-tier to silver.

---

### Rule 5: Output schema explicit (JSON with example + declared fields) [MENTAL ONLY — not enforced in code]
Worker output that is empty or has declared fields set to empty arrays/null passes as `OK` even when it should fail. No code gate detects this false-OK — the spec author must encode the schema assertion inside `## Verify`.

✅ Correct:
```
Find all security bugs in /Users/.../ doctor.py.

Output JSON **only**:
{
  "bugs": [
    {"type": "injection", "line": 42, "severity": "high"}
  ]
}
If no bugs found, still return {"bugs": []}, but the `bugs` array MUST be present.

## Verify
```sh
test -f /Users/.../doctor.py || exit 1
python3 -c "import json; d=json.load(open('/tmp/output.json')); assert isinstance(d['bugs'], list) and len(d['bugs']) > 0 or True, 'must include bugs key'" || exit 1
```
```

❌ Wrong:
```
Find all security bugs.
Output: JSON with bugs.
## Verify
test -f file.py
```
→ Worker returns `{"bugs": []}` or `{"bugs": null}` → JSON is valid and passes as OK — nothing catches this unless `## Verify` asserts the fields.

**Author check:** spec must include example JSON with non-empty arrays/objects, and `## Verify` must assert the declared fields are populated.

---

### Rule 6: Tier health check before dispatch [MENTAL ONLY — not enforced in code]
If a tier is unavailable (e.g., diamond→Fable gated by Anthropic), the dispatcher should block instead of silently falling back to a degraded tier (gemma-local). This was the root cause of d074, d075, d660, d662 timeouts in TEST 1.

✅ Correct (config + pre-flight):
```yaml
tier_model_overrides:
  diamond: anthropic:opus  # interim, until Fable available
```

❌ Wrong: Diamond tier unavailable → silently fall back to gemma-local → worker never sees external file paths → OOM or timeout.

**Author check:** confirm the tier's provider/model is reachable (config + `burnless status`/provider stats) before dispatch — there is no automatic health probe; do not rely on silent fallback.

---

## Summary: Pre-Dispatch Validation Gates

When you write a spec for `burnless do`, run this checklist:

1. **Verify fencing**: Is `## Verify` inside a ```sh block?
2. **Line limit**: Does each check inside `## Verify` fit on one line (no if/else)?
3. **Absolute paths**: Does every path start with `/`? (Project root = `/Users/roberto/antigravity/...`)
4. **Tier + file size**: Is a large file edit assigned to silver/gold, not bronze?
5. **Output schema**: Does the spec give an example JSON with non-empty declared fields?
6. **Tier health**: Is the requested tier available? (check manually — config + provider status)

If all six pass → `burnless do --tier T "..."`  
If any fails → re-spec or reword until all six pass.

**Note:** Gates 1-3 are deterministic (exit 6 / ABORT) and block at dispatch time, regardless of model state. Gates 4-6 are the author's manual checklist — validate them yourself before dispatch; the code does not check them.

**Verify checks must encode the COMPLETE correctness condition, not a proxy.** A proxy check passes
plausible-but-wrong outputs as OK. Example (real, 2026-06-13): a task asked for "the first 3 `def`s";
the Verify only checked "each reported line is a def with that name" — so an answer that returned 3
*real but wrong* defs passed as OK. The check must assert the full condition (the correct three, in
order), not a weaker stand-in. Coverage = quality of the check; the gate protects only as far as the
check is written.
