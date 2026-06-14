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

- `BURNLESS_HARDCORE=1` (env) gates tier-creep: if the router classifies bronze and you force a higher
  tier without `--force`, burnless blocks.
- Config commands use **absolute paths** — subprocess workers don't inherit full `PATH`. `burnless
  init` resolves them via `shutil.which()`.
- Before pushing the public repo: run `./scripts/public_git_check.sh`.

---

## Rolling memory (epochs)

In `burnless chat`, the maestro's own context rolls instead of growing — each turn is summarized to `.burnless/epochs/`, every 10 turns the fork rotates and re-seeds from the rolling summary (Θ(N²)→Θ(N)). Toggle: `burnless epoch on|off`. Default off.

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
Worker output that is empty or has declared fields set to empty arrays/null passes as `OK` even when it should fail. The schema-verify gate (d667, 2026-06-13) detects this false-OK and demotes the delegation to PART.

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
→ Worker returns `{"bugs": []}` or `{"bugs": null}` → JSON is valid, but schema gate catches it → PART.

**Pre-flight check:** Spec must include example JSON with non-empty arrays/objects. Gate asserts declared fields are populated.

---

### Rule 6: Tier health check before dispatch [MENTAL ONLY — not enforced in code]
If a tier is unavailable (e.g., diamond→Fable gated by Anthropic), the dispatcher should block instead of silently falling back to a degraded tier (gemma-local). This was the root cause of d074, d075, d660, d662 timeouts in TEST 1.

✅ Correct (config + pre-flight):
```yaml
tier_model_overrides:
  diamond: anthropic:opus  # interim, until Fable available
```

Then, pre-flight runs `tier_health.py` → 1-token probe on each tier → if tier fails, `[BLOCK]` and suggest alternate tier.

❌ Wrong: Diamond tier unavailable → silently fall back to gemma-local → worker never sees external file paths → OOM or timeout.

**Pre-flight check:** Health probe module; if tier unavailable, return `False` and block dispatch (not silent fallback).

---

## Summary: Pre-Dispatch Validation Gates

When you write a spec for `burnless do`, run this checklist:

1. **Verify fencing**: Is `## Verify` inside a ```sh block?
2. **Line limit**: Does each check inside `## Verify` fit on one line (no if/else)?
3. **Absolute paths**: Does every path start with `/`? (Project root = `/Users/roberto/antigravity/...`)
4. **Tier + file size**: Is a large file edit assigned to silver/gold, not bronze?
5. **Output schema**: Does the spec give an example JSON with non-empty declared fields?
6. **Tier health**: Is the requested tier available (checked via health probe)?

If all six pass → `burnless do --tier T "..."`  
If any fails → re-spec or reword until all six pass.

**Note:** Gates 1-3 are deterministic (exit 6 / ABORT) and block at dispatch time, regardless of model state. Gates 4-6 are NOT implemented in code yet (tier_health unwired, no schema gate) — treat as mental checklist until wired.

**Verify checks must encode the COMPLETE correctness condition, not a proxy.** A proxy check passes
plausible-but-wrong outputs as OK. Example (real, 2026-06-13): a task asked for "the first 3 `def`s";
the Verify only checked "each reported line is a def with that name" — so an answer that returned 3
*real but wrong* defs passed as OK. The check must assert the full condition (the correct three, in
order), not a weaker stand-in. Coverage = quality of the check; the gate protects only as far as the
check is written.
