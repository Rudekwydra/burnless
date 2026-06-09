# Burnless — Doctrine (canonical; v0.9.0)

The single source of truth for *how burnless works and how to use it*.
Architecture lives in `PROTOCOL.md`; the verified command reference lives in
`docs/COMMANDS.md` (checked against `--help`). **Code wins over memory** — when
an instruction file disagrees with the code / COMMANDS.md / PROTOCOL.md, the
code is right and the doc is stale.

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

## Tiers (DEFAULT model map)

Per-project `.burnless/config.yaml` may **REMAP** these — always check the
project config before assuming a model.

| tier    | default model | use for                                              |
|---------|---------------|------------------------------------------------------|
| gold    | opus          | architecture, structural refactor, decisions         |
| silver  | sonnet        | implementation with tight spec + HARD PROHIBITIONS    |
| bronze  | haiku         | reads, summaries, classification, ops shell           |
| diamond | (opt-in only) | NEVER auto-routed; reachable only via `--tier diamond` |

`diamond` is an explicit escalation tier — it is never selected by the router.

**Spec quality picks the tier:** compiles-in-your-head → bronze; needs thinking
through → silver; needs deciding-between-architectures → gold. The wrong tier
costs money (over-provision) — don't tier-creep.

---

## Compression

`--mode {light|balanced|extreme}` controls output compression — it is **NOT** a
timeout. `light` is the anti-phantom default (all active projects run on
`light`). `extreme` is for read-only / summary work only.

## Timeouts

`do` and `run` accept `--timeout` / `--stale-timeout-s` (`do` accepts them since
**v0.9.0**, forwarded to the run). `delegate` does **NOT** accept timeout flags.
`--force` is accepted by `delegate` only — `do` does **NOT** accept `--force`.

Never pipe `do`/`run` through `tail`/`head` — it masks the exit code. Capture
with `> file 2>&1` instead.

---

## Workflow

1. **Commit the working tree before delegating** — workers share the tree and
   may reset files. For shared files, isolate with `git worktree add`.
2. **End every code spec with a `## Verify` fenced shell block** asserting the
   DoD. The runner RE-RUNS it after the worker and demotes `OK → PART` on
   failure — deterministic, zero-LLM. Trust an `OK` that survived the gate;
   reserve manual audit for what shell can't encode.
   - Footgun: use `! grep -q PATTERN file` for absence checks — never
     `grep -c` / `diff` (they exit 1 on the good state).
3. **PART output → reject + re-spec smaller.** Never merge partial work.

> **The v0.8 LLM auditor (LLM-judges-LLM) is RETIRED.** The `## Verify` shell
> gate is the only live post-worker verification. Any text describing a live LLM
> auditor is stale.
