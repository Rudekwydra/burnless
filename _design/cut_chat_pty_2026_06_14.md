# Cut `chat` / `pty` / maestro-REPL from Burnless v1 — Removal Design

**Date:** 2026-06-14
**Author:** design pass (d689, gold/opus)
**Status:** DESIGN ONLY — no implementation, no git. Grounded in grep of the real tree at `/Users/roberto/antigravity/burnless`.

## Decision (Roberto, 2026-06-14)

`burnless chat`, the PTY mode, and the maestro-REPL were never used. Cut them from v1.
The product is **Burnless invoked from the Claude CLI**: `do / delegate / run / route / status / init / read / capsule / log / metrics / epoch` + the Claude Code hooks (UserPromptSubmit mode, SessionStart seed, Stop epoch) + rolling memory.

This doc maps exactly what is **chat/pty/REPL-only (safe to remove)** vs **shared with the live path (must stay)**, then gives a deterministic `## REMOVAL PLAN` with a `## Verify` block.

---

## 0. CRITICAL DISAMBIGUATION — four different things named "maestro"

The word "maestro" is overloaded across the tree. They are **separate code** with separate fates:

| Thing | Path | Fate | Why |
|---|---|---|---|
| **maestro PACKAGE** (REPL engine) | `src/burnless/maestro/` (engine, base, turn_router, session_runner, dispatcher, runners, display, session, counter, exec_log) | **DELETE** | Only importers are `cmd_chat` + the MCP `maestro` tool (both removed). |
| **maestro_legacy.py** (SDK backend for `do`) | `src/burnless/maestro_legacy.py` | **KEEP** (out of scope) | Imported by the LIVE runner `exec/runner.py:133` as the `do --maestro` backend. NOT the REPL. See HAZARD H1. |
| **maestro_adapters.py** (provider adapters) | `src/burnless/maestro_adapters.py` | **KEEP** | Live infra: imported by `cli.py:27` (top-level) + `keepalive.py:11`. Defines `MaestroAdapter` used across metrics/economy/profiles/state/dashboard. Badly named, but live. |
| **maestro_layer.py** + MCP `maestro` tool (3-layer encoder/decoder pipeline) | `src/burnless/maestro_layer.py`, `mcp_server.py` tool `maestro`, `cmd_pipeline`, `pipeline_state.py` | **DELETE** | The same abandoned 3-layer pipeline concept. Must be severed to delete the package (it imports the package). See §2 Tier-2. |

**The single biggest hazard is "rollover".** There are TWO unrelated rollover features:

- **REPL rollover** — `chat --rollover-turns`, the `/rollover` slash, `maestro.engine.force_compact` / `RollingCapsule`. → **DELETE** (part of the REPL).
- **Claude-Code rollover (rolling memory)** — `/burnless rollover` mode in `templates/scripts/burnless_mode_hook.sh`, `BURNLESS_ROLLOVER_TURNS`, `session-*.rollover.md`, `restart_rollover.sh`, `pending_seed.md` promotion, the SessionStart seed. → **KEEP — this is the non-negotiable rolling-memory path.** Do NOT delete `restart_rollover.sh`, `test_claude_rollover_hook.py`, or `test_restart_rollover_script.py`.

---

## 1. DEPENDENCY MAP

### 1a. Live path does NOT depend on the maestro package (verified)

`burnless do/run` execution path lives in `src/burnless/exec/runner.py`. Its only maestro reference:

- `exec/runner.py:133` → `from .. import maestro_legacy as maestro_mod` (inside `_run_with_maestro`, gated by `_should_use_maestro_backend`, `exec/runner.py:210-217`; default `cfg.maestro.run_backend = False`).

This is **`maestro_legacy.py`, a top-level module — NOT the `maestro/` package.** `do/run` imports nothing from `src/burnless/maestro/`. → Deleting the package does not touch the runner. ✅

`cli.py:62-63` imports `MAESTRO_TIER_MODEL, _run_with_maestro, _should_use_maestro_backend, _should_use_cached_worker` from `claude_integration` — these route into `maestro_legacy` (KEEP), not the package.

### 1b. The `maestro/` package — complete importer list (grep `from .maestro` / `maestro.<mod>`)

Only THREE call sites import the package:

- `cli.py` `cmd_chat` — `cli.py:1419-1424,1469,1471,1605,1630` (dispatcher, engine, base, runners, session_runner, turn_router, display). → removed with `cmd_chat`.
- `cli.py` `_chat_worker_usage_estimate` — `cli.py:1381-1382,1396` (`dispatcher.TIER_ALIASES`, `engine.estimate_tokens`). Called ONLY from `cmd_chat` (`cli.py:1637`). → removed with `cmd_chat`.
- `maestro_layer.py:70-73` — `maestro.base`, `maestro.session_runner`, `maestro.runners`, `maestro.dispatcher`. → `maestro_layer.py` is deleted (§2 Tier-2).

⇒ Once `cmd_chat` and `maestro_layer.py` are gone, **nothing imports `src/burnless/maestro/`** → the whole package is deletable.

### 1c. MCP server — live, but its `maestro` tool is dead

`mcp_server.py` is live CLI usage (exposes `delegate/route/run/capsule/read/status` to the IDE; entrypoint `burnless = burnless.cli:main`, referenced by `setup_wizard.py` + `doctor.py`). Its ONLY package dependency is the dead 3-layer tool:

- `mcp_server.py:20` `from .maestro_layer import process_envelope as _maestro_process_envelope`
- `mcp_server.py:424-434` `async def handle_maestro(...)`
- `mcp_server.py:515-516` `Tool(name="maestro", ...)` in `list_tools()`
- `mcp_server.py:539` dispatch entry `"maestro": handle_maestro` in `call_tool()`

→ Sever these four points; the MCP server keeps all live tools.

### 1d. chat/pty/REPL-only symbols (safe to remove)

| Symbol | Location | Notes |
|---|---|---|
| `cmd_chat` | `cli.py:1407-1657` | the REPL |
| `_chat_worker_usage_estimate` | `cli.py:1372-1406` | chat-only helper |
| `cmd_brain` | `cli.py:714-717` | calls `cmd_chat`; prints "use `burnless chat`" |
| `_run_basic_maestro_repl` | `cli.py:718-759` | **dead — zero callers** (grep confirms) |
| `cmd_maestro` | `cli.py:1153-1165` | already a retired stub ("engine lives in `burnless chat`") |
| `cmd_shell` | `cli.py:1237-1245` | alias → `cmd_pty` |
| `cmd_pty` | `cli.py:1246-1251` | `from . import pty_shell; pty_shell.main(...)` |
| `--chat` flag on `do` + `chat_mode` block | `cli.py:334-365`, subparser `cli.py:1739-1742` | renders conversational template, writes `{did}.chat` marker |
| `render_maestro_chat` + `MAESTRO_CHAT_TEMPLATE` | `delegations.py:95-112` | only caller is `do --chat` (`cli.py:336`) |
| `pty_shell.py` | `src/burnless/pty_shell.py` | only importer is `cmd_pty` |
| `chat_history.py` | `src/burnless/chat_history.py` | only importer is `cmd_chat` (`cli.py:1415,1589,1641`) |
| `maestro/` package | `src/burnless/maestro/*` | see 1b |
| subparsers: `chat`, `brain`, `shell`, `pty`, `maestro` | `cli.py:1877-1897`, `1899-1902`, `1979-1981`, `1983-1985`, `2061-2067` | |

### 1e. Shared / KEEP despite "maestro" name

- `maestro_legacy.py` (do backend, H1), `maestro_adapters.py` (provider adapters), `keepalive.py` (worker warming; imported by metrics/economy/savings_formula/dashboard/profiles/state/coreconfig — **NOT chat-only**), `epochs.py` + all hooks in `templates/scripts/` + `restart_rollover.sh` (rolling memory), `cmd_plan` + `maestro.md` plan file (`cli.py:201-220` — a plan doc, unrelated to the REPL).
- `maestro:` config block (`.burnless/config.yaml:8`) — KEEP: `maestro.run_backend` is read by the `do` backend (`exec/runner.py:217`). `maestro.model` becomes unused after chat removal but is harmless.

---

## 2. REMOVAL INVENTORY

### Tier-1 — chat / pty / REPL (definite, in scope)

**Files to delete:**
- `/Users/roberto/antigravity/burnless/src/burnless/pty_shell.py`
- `/Users/roberto/antigravity/burnless/src/burnless/chat_history.py`
- `/Users/roberto/antigravity/burnless/src/burnless/maestro/` (entire dir: `__init__.py`, `base.py`, `counter.py`, `dispatcher.py`, `display.py`, `engine.py`, `exec_log.py`, `runners.py`, `session.py`, `session_runner.py`, `turn_router.py`)

**Tests to delete (chat/pty/maestro-package-only):**
- `/Users/roberto/antigravity/burnless/tests/test_chat_display.py` (imports `maestro.display`, `chat_history`)
- `/Users/roberto/antigravity/burnless/tests/test_chat_rollover.py` (imports `maestro.engine`)
- `/Users/roberto/antigravity/burnless/tests/test_chat_slash.py` (imports `maestro.turn_router`)
- `/Users/roberto/antigravity/burnless/tests/test_chat_turn_router.py` (imports `maestro.turn_router`, `maestro.engine`)
- `/Users/roberto/antigravity/burnless/tests/test_maestro_engine.py` (imports `maestro.engine`)
- `/Users/roberto/antigravity/burnless/tests/test_pty_shell_scroll.py`
- `/Users/roberto/antigravity/burnless/tests/test_pty_shell_tips.py`

**`cli.py` blocks to excise** (descending line order when applied):
- `cmd_chat` `1407-1657`; `_chat_worker_usage_estimate` `1372-1406`; `cmd_pty` `1246-1251`; `cmd_shell` `1237-1245`; `cmd_maestro` `1153-1165`; `_run_basic_maestro_repl` `718-759`; `cmd_brain` `714-717`.
- `do --chat`: the `chat_mode` branch `334-343` + the `{did}.chat` marker block `361-365`; subparser `--chat` arg `1739-1742`.
- Subparsers: `chat` `1877-1897`, `brain` `1899-1902`, `shell` `1979-1981`, `pty` `1983-1985`, `maestro` `2061-2067`.
- `cli.py:27` top import `from . import maestro_adapters` — **KEEP** (used elsewhere; confirm not orphaned after edits). Do NOT remove.
- `delegations.py:95-112` `render_maestro_chat` + its `MAESTRO_CHAT_TEMPLATE` constant.

**Must-fix (or `import burnless.cli` / CLI breaks):**
- `cli.py:2191` `main()` no-arg path `return cmd_chat(...)` → repoint to `parser.print_help(); return 0` (or `cmd_menu`). **Non-negotiable.**
- Help/doc strings mentioning chat: `cli.py:894,905` (capsule help "live chat uses semantic…"), `cli.py:522` ("per-chat: /burnless in chat") — update wording (non-blocking, cosmetic).

**Config keys to drop:**
- `cache_policy.rolling_compaction_enabled` — gated ONLY by `maestro/engine.py:96` (verified; not read by `cache_policy.py` or `maestro_legacy.py`). Remove from `config.py:116` default and `.burnless/config.yaml:130`. Cosmetic/safe (default already `False`).
- Leave the `maestro:` block (run_backend live). Leave `chat.*` (router_enabled/expand_display) — they are NOT in `DEFAULT_CONFIG` (read via inline `.get()` in `cmd_chat`, so they vanish with `cmd_chat`).

### Tier-2 — 3-layer MCP pipeline (REQUIRED to delete the package; coupled)

Deleting `maestro/` forces severing `maestro_layer.py`'s import, which forces removing the MCP `maestro` tool. Do all of:
- Sever MCP: remove `mcp_server.py:20`, `424-434`, `515-516`, `539` (see §1c).
- Delete `/Users/roberto/antigravity/burnless/src/burnless/maestro_layer.py`.
- Delete `/Users/roberto/antigravity/burnless/tests/test_maestro_layer.py` and `/Users/roberto/antigravity/burnless/tests/test_maestro_rotation.py` (both import `maestro_layer` / `maestro.*`; rolling-memory epoch coverage is preserved by `test_epochs.py`, `test_epoch_*.py`, `test_session_seed*.py`).
- **Recommended (same dead feature, self-contained):** remove `cmd_pipeline` (`cli.py:2151-2177`) + its subparser (`cli.py:2069-2079`) + delete `pipeline_state.py` (only importer is `cli.py`). This is the user-facing toggle for the same dead encoder/decoder pipeline (`mcp__burnless__maestro`). If preferring minimal blast radius, the `pipeline` command can be left as a no-op stub — but then it dangles. Decision: remove.

### Docs to mark historical (no code impact)
- `_design/CODEX_CHAT_ROLLOVER_2026-06-10.md`, `_design/rolling_memory_pure_chat_2026_06_13.md`, `_design/maestro_v1/`, `_design/MCP_SERVER_DESIGN_2026-05-20.md` (maestro tool section), `_design/layer_*`, `_design/brecha6_brain_adapters_*`. Prepend a `> SUPERSEDED 2026-06-14: chat/pty/maestro-REPL cut from v1 (see cut_chat_pty_2026_06_14.md)` banner. Do not delete history.

---

## 3. HAZARDS

- **H1 — `maestro_legacy.py` is NOT the REPL.** It is the `do --maestro` SDK backend (`exec/runner.py:116-217`). Deleting it would break `do --maestro` / `cfg.maestro.run_backend`. → **KEEP.** Out of scope; flag for a separate "is the SDK backend still wanted?" review.
- **H2 — `main()` no-arg → `cmd_chat`** (`cli.py:2191`). Removing `cmd_chat` without repointing this makes `import burnless.cli` fine but `burnless` (no args) crash with `NameError`. → repoint in the same change.
- **H3 — rollover ambiguity.** `burnless_mode_hook.sh` rollover mode, `BURNLESS_ROLLOVER_TURNS`, `restart_rollover.sh`, `session-*.rollover.md`, `pending_seed.md`, `test_claude_rollover_hook.py`, `test_restart_rollover_script.py` are **rolling memory (KEEP)**, NOT the REPL `--rollover-turns`. Do not touch.
- **H4 — `maestro_adapters.py` / `keepalive.py` look maestro/chat-ish but are live.** `keepalive` is worker-warming used by metrics/economy/dashboard/profiles/state — NOT chat-only. KEEP both.
- **H5 — breaking CLI surface.** Removing `chat`, `pty`, `shell`, `brain`, `maestro` (subcommands) and `do --chat` (flag) is a **breaking change** to those public surfaces. They were never used (per decision) → remove outright, no alias. `brain`/`maestro` are already retirement stubs, so their removal is low-risk. If any external script calls them, they will now error with argparse "invalid choice" (acceptable).
- **H6 — `maestro.md` plan file & `p["chat"]`/`p["history"]` path keys.** `cmd_plan` writes `maestro.md` (a plan doc, unrelated). Leave `paths.py` keys `chat`/`history`/`maestro` as-is to avoid breaking other `paths_for()` readers; the dirs simply go unused. Pruning them is optional/cosmetic and out of this cut.
- **H7 — `pipeline_state.py`** only imported by `cli.py` → safe to delete with `cmd_pipeline`. Confirm grep before delete (done: single importer).

---

## REMOVAL PLAN

Ordered, deterministic. Apply top-to-bottom. All paths absolute. (`do/delegate/run/route/status/epoch/init` and the rolling-memory hooks remain untouched — this plan never edits `exec/runner.py`, `epochs.py`, `templates/scripts/*`, `restart_rollover.sh`, `maestro_legacy.py`, `maestro_adapters.py`, or `keepalive.py`.)

**Step 1 — Delete REPL/pty source files.**
- rm `/Users/roberto/antigravity/burnless/src/burnless/pty_shell.py`
- rm `/Users/roberto/antigravity/burnless/src/burnless/chat_history.py`
- rm -r `/Users/roberto/antigravity/burnless/src/burnless/maestro/`

**Step 2 — Delete 3-layer-pipeline source files.**
- rm `/Users/roberto/antigravity/burnless/src/burnless/maestro_layer.py`
- rm `/Users/roberto/antigravity/burnless/src/burnless/pipeline_state.py`

**Step 3 — Sever MCP `maestro` tool** in `/Users/roberto/antigravity/burnless/src/burnless/mcp_server.py`: remove line 20 import, `handle_maestro` (424-434), the `Tool(name="maestro", …)` entry (515-516), and the `"maestro": handle_maestro` dispatch (539). Leave all other tools.

**Step 4 — Excise `cli.py` functions** (`/Users/roberto/antigravity/burnless/src/burnless/cli.py`), highest line number first to keep offsets stable: `cmd_pipeline` (2151-2177) → `cmd_chat` (1407-1657) → `_chat_worker_usage_estimate` (1372-1406) → `cmd_pty` (1246-1251) → `cmd_shell` (1237-1245) → `cmd_maestro` (1153-1165) → `_run_basic_maestro_repl` (718-759) → `cmd_brain` (714-717).

**Step 5 — Excise `cli.py` subparsers** (`build_parser`): `pipeline` (2069-2079), `maestro` (2061-2067), `pty` (1983-1985), `shell` (1979-1981), `brain` (1899-1902), `chat` (1877-1897), and the `do --chat` arg (1739-1742).

**Step 6 — Excise `do --chat` logic** in `cmd_do`: the `chat_mode` branch (334-343) and the `{did}.chat` marker block (361-365). Keep the `else` (`render_delegation`) path as the only path.

**Step 7 — Repoint `main()` no-arg** (`cli.py:2191`): `return cmd_chat(argparse.Namespace(model=None))` → `parser = build_parser(); parser.print_help(); return 0`.

**Step 8 — Remove `render_maestro_chat`** + `MAESTRO_CHAT_TEMPLATE` from `/Users/roberto/antigravity/burnless/src/burnless/delegations.py` (95-112 + the template constant).

**Step 9 — Drop config key `rolling_compaction_enabled`** from `/Users/roberto/antigravity/burnless/src/burnless/config.py:116` and `/Users/roberto/antigravity/burnless/.burnless/config.yaml:130`.

**Step 10 — Delete dead tests:**
- rm `/Users/roberto/antigravity/burnless/tests/test_chat_display.py`
- rm `/Users/roberto/antigravity/burnless/tests/test_chat_rollover.py`
- rm `/Users/roberto/antigravity/burnless/tests/test_chat_slash.py`
- rm `/Users/roberto/antigravity/burnless/tests/test_chat_turn_router.py`
- rm `/Users/roberto/antigravity/burnless/tests/test_maestro_engine.py`
- rm `/Users/roberto/antigravity/burnless/tests/test_maestro_layer.py`
- rm `/Users/roberto/antigravity/burnless/tests/test_maestro_rotation.py`
- rm `/Users/roberto/antigravity/burnless/tests/test_pty_shell_scroll.py`
- rm `/Users/roberto/antigravity/burnless/tests/test_pty_shell_tips.py`
- KEEP: `test_maestro_adapters.py`, `test_claude_rollover_hook.py`, `test_restart_rollover_script.py` (live infra + rolling memory).

**Step 11 — Docs:** prepend SUPERSEDED banners (§2 Docs). No code impact.

**Step 12 — Verify** (below).

---

## Verify

```sh
test -f /Users/roberto/antigravity/burnless/_design/cut_chat_pty_2026_06_14.md || exit 1
test ! -e /Users/roberto/antigravity/burnless/src/burnless/maestro || exit 1
test ! -e /Users/roberto/antigravity/burnless/src/burnless/pty_shell.py || exit 1
test ! -e /Users/roberto/antigravity/burnless/src/burnless/chat_history.py || exit 1
test ! -e /Users/roberto/antigravity/burnless/src/burnless/maestro_layer.py || exit 1
test -f /Users/roberto/antigravity/burnless/src/burnless/maestro_legacy.py || exit 1
test -f /Users/roberto/antigravity/burnless/src/burnless/maestro_adapters.py || exit 1
test -f /Users/roberto/antigravity/burnless/restart_rollover.sh || exit 1
test -f /Users/roberto/antigravity/burnless/templates/scripts/burnless_mode_hook.sh || exit 1
cd /Users/roberto/antigravity/burnless && python3 -c "import burnless.cli" || exit 1
cd /Users/roberto/antigravity/burnless && python3 -c "import burnless.mcp_server" || exit 1
cd /Users/roberto/antigravity/burnless && python3 -m burnless --help 2>&1 | grep -Eq "(^| )do( |,)" || exit 1
cd /Users/roberto/antigravity/burnless && python3 -m burnless --help 2>&1 | grep -Eq "delegate" || exit 1
cd /Users/roberto/antigravity/burnless && python3 -m burnless --help 2>&1 | grep -Eq "epoch" || exit 1
cd /Users/roberto/antigravity/burnless && python3 -m burnless --help 2>&1 | grep -Eqv "" ; python3 -m burnless --help 2>&1 | grep -q " chat " && exit 1 || true
cd /Users/roberto/antigravity/burnless && python3 -m burnless --help 2>&1 | grep -qw pty && exit 1 || true
cd /Users/roberto/antigravity/burnless && python3 -m pytest tests/ --collect-only -q >/dev/null 2>&1 || exit 1
grep -rq "from .maestro import\|from \.\.maestro\|import maestro_layer\|from .maestro_layer" /Users/roberto/antigravity/burnless/src/burnless && exit 1 || true
```
