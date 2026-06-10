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

Choose per session how much Burnless drives your assistant — `/burnless off|partner|on`:

- **off** — raw chat, no Burnless.
- **partner** — the assistant keeps full reasoning and delegates execution to the tiers.
- **on** — the assistant is pinned to the Maestro role: compress intent and only delegate.

Details + the hook: `docs/USING_BURNLESS_FROM_YOUR_LLM.md`. In `partner`/`on` the assistant compresses
your intent into a capsule for the Maestro and expands the Maestro's response back to natural language.

---

## Compression

`--mode {light|balanced|extreme}` controls output compression — it is **NOT** a timeout. `light` is
the default. `extreme` is for read-only / summary work only.

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
