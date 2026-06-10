# Burnless — Doctrine (canonical)

The single source of truth for *how burnless works and how to use it*. This file is **public and
authoritative** — personal operating notes (Roberto's `~/.claude`, the workspace `soul`) should point
here, not re-state these rules. Architecture lives in `PROTOCOL.md`; the verified command reference
lives in `docs/COMMANDS.md` (checked against `--help`). **Code wins over memory** — when an
instruction file disagrees with the code / COMMANDS.md / PROTOCOL.md, the code is right and the doc
is stale.

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

A tier is a **role**. Which `provider + model` runs each role is **the user's choice**, set in
`.burnless/config.yaml` per level. You can point every level at one model (all-local gemma, or
all-opus) or mix freely. Always check the project config before assuming a model — there is no
hardcoded mapping, only a shipped default.

| tier | role | shipped default |
|---|---|---|
| gold | architecture, structural refactor, decisions | opus |
| silver | implementation with tight spec + HARD PROHIBITIONS | sonnet |
| bronze | reads, summaries, classification, ops shell, **local code via tool-calling** | haiku *or* ollama-local (gemma) |
| diamond | irreversible / second opinion | opt-in only — NEVER auto-routed |

`diamond` is reachable only via `--tier diamond`; the router never selects it.

**Spec quality picks the tier:** compiles-in-your-head → bronze; needs thinking through → silver;
needs deciding-between-architectures → gold. Wrong tier costs money (over-provision) — don't tier-creep.

**Local models as real workers.** An `ollama-local` agent with `tools: true` runs an agentic
tool-calling loop (read/write/shell over the ollama HTTP API) and edits the filesystem like any other
worker — set `bronze: {provider: ollama-local, model: <gemma>, tools: true}` and `burnless do --tier
bronze` works end to end. Without `tools`, an ollama agent is single-shot text (summaries/classify).

> **Roadmap (Pro/Synapsis):** pick the per-level config per task in any chat and save it as the default
> for new sessions. Today the default lives in `.burnless/config.yaml`.

---

## Compression

`--mode {light|balanced|extreme}` controls output compression — it is **NOT** a timeout. `light` is
the anti-phantom default (all active projects run on `light`). `extreme` is read-only / summary work only.

> **Deprecated:** the cipher capsule round-trip (`burnless compress`/`decode`, XOR + key custody) is
> retired — key custody was memory-only, so v2 capsules never decoded across processes. The concept
> (encrypted capsules + persistent key store) is reserved for burnless Pro / Synapsis. The **live chat
> decodes semantically** (the IDE LLM expands the capsule via the Maestro `decoder_hint`, no cipher).

---

## Timeouts

`do` and `run` accept `--timeout` / `--stale-timeout-s` (`do` since **v0.9.0**, forwarded to the run).
`delegate` does **NOT** accept timeout flags. `--force` is accepted by `delegate` only — `do` does **NOT**.

Never pipe `do`/`run` through `tail`/`head` — it masks the exit code. Capture with `> file 2>&1`.

---

## Workflow

1. **Commit the working tree before delegating** — workers share the tree and may reset files. For
   shared files, isolate with `git worktree add`.
2. **End every code spec with a `## Verify` fenced shell block** asserting the DoD. The runner RE-RUNS
   it after the worker and demotes `OK → PART` on failure — deterministic, zero-LLM, written to the
   delegation log (`--- VERIFY ---`). Trust an `OK` that survived the gate; reserve manual audit for
   what shell can't encode.
   - Footgun: use `! grep -q PATTERN file` for absence checks — never `grep -c` / `diff` (they exit 1
     on the good state).
3. **PART → reject + re-spec smaller. Never merge partial work.** `PART` today comes from the
   deterministic `## Verify` gate (or the worker self-reporting / a stale worker) — it is the live
   replacement for the retired LLM auditor, not the same thing.
4. **Gold delegations return everything in one roundtrip:** decision + trade-offs, implementation plan,
   and a tight spec with HARD PROHIBITIONS ready to paste into a bronze/silver delegation.

> **The v0.8 LLM auditor (LLM-judges-LLM + snapshot-diff) is RETIRED** and is NOT in the free path. The
> `## Verify` shell gate is the only live post-worker verification. (`_pro/audit.py` is a separate
> Pro/dormant module, not wired into `burnless run`.) Any text describing a live LLM auditor is stale.

<!--
RESERVED RULE (commented, not active) — local/weak workers (bronze haiku/gemma) historically
privileged EDIT over EXECUTE when a spec mixed both. The mitigation was: split the spec into a clear
"EDIT FILES" block and a separate "EXECUTE SHELL" block. Left commented intentionally: if a similar
failure recurs (worker edits files but skips the shell/verify step), re-enable this rule — it is the
likely cause.
-->

---

## Ops

- `BURNLESS_HARDCORE=1` (env) gates tier-creep: if the router classifies bronze and you force a higher
  tier without `--force`, burnless blocks. Repeated `--force` without reason = re-read your spec.
- Config commands must use **absolute paths** — subprocess workers don't inherit full `PATH`. `burnless
  init` resolves them via `shutil.which()`; older configs may need manual fixing.
- Before pushing the public repo: run `./scripts/public_git_check.sh`.
