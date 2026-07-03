# Burnless — canonical command reference

**Single source of truth for subcommand flags. Verified against `--help` on v0.9.0 (2026-05-28).**
When docs and memory disagree, this file (and `burnless <cmd> --help`) wins. Do not document flags from memory.

The three layers: **Encoder/Decoder** (compaction) → **Maestro** (routing) → **Workers** (tiered execution: bronze/silver/gold/diamond).

---

## Core lifecycle

| Command | What it does |
|---|---|
| `burnless init` | Seed `.burnless/` in the current project. |
| `burnless route "TASK"` | Preview which tier/agent would handle a task (no execution). |
| `burnless delegate "TASK"` | Create a numbered delegation `dXXX` (does NOT run it). |
| `burnless run dXXX` | Execute an existing delegation through its agent. |
| `burnless do "TASK"` | **Atomic** delegate + run in one step. Sugar for `delegate` then `run`. |
| `burnless read dXXX` | 3-path fallback read: capsule → temp → log. |
| `burnless capsule dXXX` | Finalized capsule contents. |
| `burnless log dXXX` | Raw worker stdout. |
| `burnless status` / `metrics` | Project health / token savings counters. |

## Flags by subcommand (verified vs `--help`)

### `burnless do` — delegate + run atomically
- `--tier {diamond,gold,silver,bronze}` — force a tier (diamond = explicit escalation only). Shortcuts: `--gold` / `--silver` / `--bronze` / `--diamond`.
- `--force` — override the tier escalation policy (forwarded to delegate).
- `--cold-cache` — inject a nonce to force a cache miss (cold-cache benchmarks).
- `--timeout TIMEOUT` — worker timeout in seconds, forwarded to the embedded run *(added v0.9.0; before that `do` rejected it)*.
- `--stale-timeout-s STALE_TIMEOUT_S` — abort if no worker output for N seconds, forwarded to the embedded run *(added v0.9.0)*.
- `--allow-relative-paths` — skip the absolute-path guard (workers run in isolated cwd; relative paths may fail).
- `--allow-unfenced-verify` — accept a `## Verify` block that is not fenced in ```` ```sh ````.

### `burnless delegate` — create a delegation (does not run)
- `--goal`, `--success`, `--tier`, `--chain CSV`, `--force`, `--allow-relative-paths`, `--allow-unfenced-verify`.
- **No `--timeout`/`--stale-timeout-s`** — `delegate` does not execute, so they have nowhere to go. Use `do` (forwards them) or `run dXXX --timeout N`.

### `burnless run dXXX` — execute a delegation
- `--timeout`, `--stale-timeout-s`, `--dry-run`, `--maestro` / `--no-cache-worker`, `--cold-cache`, `--watch` / `--quiet` / `--full` / `--verbose`, `--progress {minimal,brief,full}`.

### `burnless route "TASK"` — preview routing (no execution)
- (default) — prints the natural tier, agent, and matched keyword (3-line summary).
- `--explain` — full scored route decision: natural/requested/effective tier, confidence, the signals that drove it, the active escalation-policy source, the action (`allowed`/`downgraded`/`blocked`/`confirmed`), and an executable next command.
- `--tier {diamond,gold,silver,bronze}` — test a requested-tier upgrade against the natural route (pairs with `--explain` to see whether the escalation policy would block it and the `--force` command to override).

## Behavior contracts (verified)

- **Atomic id allocation:** delegation ids are allocated under an exclusive file lock (`alloc_delegation_id`). Parallel `burnless do` never collide. *(Proven: 30 concurrent processes → 0 collisions.)*
- **Exit codes:** `run` returns `0` only when worker status is `OK`, else `1` (`cli.py: return 0 if status_str == "OK" else 1`). `do` propagates that code. So `&&`-chains and task-notifications reflect real failure.
- **Silent default:** `do`/`run` print the one-line result, not the full delegation `.md`. Still: **never pipe `burnless do/run` through `tail`/`head`** — it masks the exit code and truncates errors. Capture with `> file 2>&1` or `set -o pipefail`, and audit deliverables by filesystem (`ls`/`grep`) before trusting an OK.
- **Worker read scope:** workers run with `--permission-mode bypassPermissions`, so they CAN read paths outside the project root (e.g. `/tmp`, `~/Downloads`). *(Proven 2026-05-28.)* Inlining small sources in the spec is still good practice, but not required for reads.

## Compression
- Capsule compression is fixed and faithful (~150 chars/field, ≤12 list items, full paths, dedupe only). No mode knob.
- The compaction encoder is configured in `.burnless/config.yaml` under `encoder.endpoint`, `encoder.timeout_s`, and `encoder.local_api`; `BURNLESS_LOCAL_API` remains a compatibility override, not the primary switch.

## Versioning
Single source of truth: `src/burnless/__init__.py` → `__version__`. `pyproject.toml` reads it dynamically (`[tool.hatch.version]`). Never hardcode a version elsewhere.
- Rolling-memory format is selected by `.burnless/config.yaml` → `epochs.version` (new projects default to `3`). `BURNLESS_EPOCH_V2` is a compatibility override only.

## `burnless epoch` — rolling-memory toggle + epoch engine

| Command | What it does |
|---|---|
| `burnless epoch capture --chat-id ID` | Read STDIN, summarize, append to epoch chain, consolidate if at 10. |
| `burnless epoch read --chat-id ID` | Print active chain to stdout. |
| `burnless epoch cleanup --chat-id ID` | Remove originais directory. |
| `burnless epoch on` | Enable rolling memory (remove `.burnless/epochs.off`, opt-out marker). |
| `burnless epoch off` | Disable rolling memory (create `.burnless/epochs.off`). |
| `burnless epoch status` | Show ON/OFF state + count of chats and summary files. |
