# FABLE SENIOR REVIEW — burnless v1 ship-quality

- Reviewer: Claude Fable 5 (diamond, d537) · 2026-06-09/10 · branch `v0.9-agent-arch` (HEAD ef770d2)
- Method: read the live code (cli.py, maestro/*, agents.py, live_runner.py, dispatcher.py, coreconfig/*, cache_modes/*, warm_session*.py, economy.py, pricing.py, codec/encoder.py, config.py, live `.burnless/config.yaml`). 443 tests pass + 1 skip (re-ran locally, 28.8s).
- Every claim cites `file:line`. Specs at the end are dispatch-ready (`burnless do --tier ...`).

## TL;DR (ranked by impact)

| # | Sev | Issue | Where |
|---|-----|-------|-------|
| 1 | P0 | `burnless chat` workers: capsule extraction can't parse stream-json worker output → every delegation comes back `PART missing worker capsule` with the live config | `maestro/dispatcher.py:396-403` vs `.burnless/config.yaml` / `config.py:17,23,46` |
| 2 | P0 | `burnless chat` workers spawn **COLD** — dispatcher bypasses the whole warm-fork machinery (and the economy footer then *fakes* a 22k cache_read) | `maestro/dispatcher.py:225-233`, `cli.py:2653` |
| 3 | P0/P1 | Maestro fork flag drift: base init uses `--setting-sources/--exclude-dynamic-system-prompt-sections/--strict-mcp-config`, the per-turn fork doesn't → `cache_miss_reason: system_changed` risk every turn | `maestro/base.py:83-92` vs `maestro/session_runner.py:34-41` |
| 4 | P1 | Codex warm cache is **dead**: registry promises `fork_args()` on `warm_session_codex`, module only has `warm_flags()/warm_brief()` → AttributeError swallowed → always cold + misleading WARN | `agents.py:619-624`, `warm_session_codex.py:366,382`, `live_runner.py:340-341` |
| 5 | P1 | Dual-config hazard: `coreconfig/` is ~90% dead scaffolding; its `load()` ignores `BURNLESS_GLOBAL_CONFIG`; its CacheMode flags are wrong (`--exclude-dynamic`) | `coreconfig/resolver.py:90` vs `config.py:218`; `coreconfig/schema.py:155` |
| 6 | P1 | `--tools ""` footgun lives on in `maestro_runner` (cache-anchor killer); module should be retired | `maestro_runner.py:93` |
| 7 | P1 | `cache_modes/` package modules are never imported at runtime (registry consumes only `warm_module`) — dead code masquerading as a provider matrix | `cache_modes/__init__.py:4-17`, `coreconfig/resolver.py:201-222` |
| 8 | P2 | 5 maestro entry points, 3 of them legacy; bare `burnless` still boots the "removed in v0.7.4" shell REPL | `cli.py:3204-3206`, see PART 3 |
| 9 | P2 | `cmd_do --mode` patches config.yaml on disk and restores in `finally` — corrupts under parallel `burnless do` | `cli.py:2498-2527` |
| 10 | P2 | Economy rate table has no codex/gemini/gemma families; non-anthropic workers priced as sonnet in the footer | `pricing.py:3-28`, `economy.py:124-131` |

---

# PART 1 — Senior-dev re-review (ranked)

## P0-1 — dispatcher capsule extraction is incompatible with stream-json worker commands

**Where:** `maestro/dispatcher.py:42-45` (CAPSULE_RE), `:396-403` (`_last_capsule` scans raw stdout lines), `:259-265` (fallback → `PART missing worker capsule`).

**The smell:** `run_delegate()` runs the tier command from `config["agents"][tier]["command"]` verbatim (`dispatcher.py:279-290` only appends `-p`/`--allowedTools`, never strips `--output-format`). The default agent commands (`config.py:17,23,46`) AND the live project config (`.burnless/config.yaml` bronze/gold/diamond) all carry `--output-format stream-json --verbose --include-partial-messages`. In stream-json mode every stdout line is a JSON event (`{"type":"system",...}`, `{"type":"result","result":"brz sum x :: OK ..."}`). `CAPSULE_RE` requires lines starting with `gld|slv|brz|...` — no JSON line ever matches. Result: `capsule=None` → status falls to `"ERR" if proc.returncode else "PART"` and the maestro is fed `missing worker capsule [ref:exec/Txxxx]`. The "443 green" suite never catches it because `tests/test_dispatcher.py` exercises fake binaries that print plain text.

**Why P0:** this breaks the *headline* loop of v1 (`chat → delegate → capsule → ingest`) for any user whose tier commands are the shipped defaults. The worker did the work; burnless reports PART.

**Fix (small):** in `_last_capsule()`, first try per-line `json.loads`; if `obj.get("type") == "result"`, scan `obj["result"]` (multi-line) with CAPSULE_RE; keep the raw-line scan as fallback. Or strip `--output-format stream-json`/`--include-partial-messages` in `_worker_command()` and force `--output-format json`, then read `result` from the parsed envelope (more robust; loses live streaming, which dispatcher doesn't render anyway). Spec C below ships this.

## P0-2 — chat/brain workers spawn COLD: dispatcher bypasses the warm-fork machinery entirely

**Where:** `maestro/dispatcher.py:225-233` calls `subprocess.run(run_parts, ...)` directly with `cwd=str(project_root)`.

**The smell:** there are now THREE worker-launch paths and only two of them are cache-correct:
1. `agents._run_once` → `_inject_warm_fork_args` (`agents.py:587-658`): warm fork, registry-driven.
2. `live_runner.run_with_live_panel` (`live_runner.py:328-385`): warm fork + bare-equivalent flags + iso-cwd (`:437-457`).
3. `dispatcher.run_delegate`: **none of it**. No `--resume <warm> --fork-session`, no `--no-session-persistence/--strict-mcp-config/--disable-slash-commands/--exclude-dynamic-system-prompt-sections`, no iso-cwd — the worker runs in the project root, so the project CLAUDE.md is baked into a fresh (uncached) system prompt on every dispatch.

This directly violates the cost thesis the engine was built on: worker O(1) = "forks constant warm base". Via `burnless chat` (the LIVE entry), every worker pays cold W (2.0× cache_creation) instead of R (0.10× read) on its prefix. With k≈6 internal calls per worker turn re-reading that contaminated prefix, the delta is the whole product.

**Aggravator — the footer lies about it:** `_chat_worker_usage_estimate` (`cli.py:2650-2656`) hardcodes `"cache_read_input_tokens": 22000` per worker ("measured warm worker prefix"). Workers dispatched by chat are cold, so `economy_snapshot` (economy.py:161-202) under-counts `actual_usd` (22k tokens billed at 0.10× instead of write at 2.0×) — the `⇣N×` ratio shown to the user is inflated by construction.

**Fix:** make `run_delegate` build the worker process through ONE shared launcher. Smallest-safe: replace the inline `subprocess.run` with `agents_mod.run(agent_cfg, prompt, timeout=..., cwd=project_root, tier=tier_key)` — that path already does warm-fork injection + provider autobalance + ANTHROPIC_API_KEY stripping. (Then delete the duplicated env handling at `dispatcher.py:217-223`.) Spec C below.

## P0/P1-3 — maestro fork flag drift vs base init (cache_miss: system_changed)

**Where:** `maestro/base.py:78-92` (init: `--permission-mode bypassPermissions --disallowedTools ... --strict-mcp-config --disable-slash-commands --setting-sources project,local --exclude-dynamic-system-prompt-sections --append-system-prompt <role>`) vs `maestro/session_runner.py:34-41` (per-turn fork: only `-p msg --model --output-format json --disallowedTools ... --resume`).

**The smell:** `live_runner.py:369-385` documents exactly why those flags must ALSO be present on forks: dynamic per-machine system-prompt sections "drift between warm-init and fork and cause `cache_miss_reason: system_changed`". The new MaestroSession fork omits all four flags plus `--setting-sources`. If the resumed CLI rebuilds the system prompt with dynamic sections (cwd/env/git/memory) the cached base prefix never matches → the maestro pays cache_write on its full history every turn — silently, since `runner_claude_json` discards usage diagnostics beyond the dict.

Mid-cycle continuation (`--resume <fork>` without `--fork-session`, `session_runner.py:39-40`) is the right shape; the missing flags are the risk. Note the design is *correct* about `--tools ""`: tool defs stay present as cache anchor and execution is blocked via `--disallowedTools` (`session_runner.py:6-8`). The footgun survives only in legacy `maestro_runner.py:93` (see P1-6).

**Fix:** append the same flag set used at init to `MaestroSession.build_command()`; add a one-shot assertion in `cmd_chat` that prints `cache_read/cache_creation` from `session.usages[-1]` per turn (data already collected, `session_runner.py:48-49`) so a regressing prefix is visible in the REPL. **Verify empirically before shipping chat** — run two turns, expect turn-2 `cache_read_input_tokens` ≥ base prefix size and `cache_creation` ≈ turn-1 output only.

## P1-4 — codex warm cache is dead code-by-contract-mismatch

**Where:** `coreconfig/schema.py:166-172` declares `codex_subscription.warm_module = "burnless.warm_session_codex"`. `agents._inject_warm_fork_args` (`agents.py:619-620`) does `importlib.import_module(...).fork_args(burnless_root, model)`. `warm_session_codex.py` has **no `fork_args`** (it exposes `warm_flags()` :366 and `warm_brief()` :382 — codex caching is byte-prefix, not session-fork). The AttributeError is swallowed by the broad `except Exception` at `agents.py:632-638` and printed as "warm_session module unavailable (...); worker will spawn COLD". `live_runner.py:340-341` has the same dead call (`_ws.fork_args` on `warm_session_codex`).

Meanwhile the one mechanism that *would* warm codex — `warm_codex_brief`/`warm_codex_flags` threading into `run_with_live_panel` (`live_runner.py:394-407`) — is explicitly stubbed off: `cli.py:819-821` `# Codex warm brief injection is re-wired in phase 3; for now leave empty.`

**Net:** every codex worker, on every path, spawns cold today, with a WARN that misdiagnoses the cause. The keystone claim "cache mode follows from provider" is false for codex.

**Fix:** providers cache differently, so the registry contract must be a *protocol*, not a function name. Give every warm module a uniform adapter surface: `warm_args(burnless_root, model) -> list[str]` (CLI flags) **and** `warm_prefix(burnless_root, model) -> str` (prompt prefix; "" for claude). `warm_session.warm_args` = today's `fork_args`; `warm_session_codex.warm_args` = `warm_flags`, `warm_prefix` = `warm_brief`. Callers (`agents.py:619-658`, `live_runner.py:328-367`) consume both. Spec B below.

## P1-5 — dual config: coreconfig is the keystone on paper, scaffolding in practice

**Verdict on the question "which is source of truth?": `config.py` is the live source of truth.** Every run path resolves tiers/commands/models through `config_mod.load`/`resolve_model` (`cli.py:785,814`, `dispatcher.py:133`, `routing` etc.). `coreconfig` has exactly two real consumers:
- `agents.py:613-616` — `resolve_cache_mode()` to pick the warm module (genuinely live, the only registry win).
- `cached_worker.py:288` — `min_cache_tokens()`.

Everything else in `coreconfig/` (`DEFAULT_TIERS`, `default_config()`, `load()`, `route()`, `resolve_model()`, `resolve_agent()`) duplicates `config.py`/`routing.py` and is exercised only by tests (`test_config_spine.py`, `test_agent_cache_spine.py`). Specific hazards:

1. **Cascade divergence:** `coreconfig/resolver.py:90` reads `~/.config/burnless/config.yaml` unconditionally; `config.py:218-222` honors `BURNLESS_GLOBAL_CONFIG` (the hermetic fix in HEAD commit ef770d2). Any future caller of `coreconfig.load()` resolves a *different* config in tests/CI than the live loader. Fix: mirror the env check (3 lines).
2. **Wrong flag mirrored as data:** `coreconfig/schema.py:155` records `"--exclude-dynamic"`; the real CLI flag is `--exclude-dynamic-system-prompt-sections` (`warm_session.py:209`, `live_runner.py:380`). Harmless today only because `CacheMode.flags` is consumed by nobody — i.e., wrong data sitting in the "single source of truth" waiting for the first consumer.
3. **Aliases duplicated:** `MODEL_ALIASES` in both `config.py:335-340` and `coreconfig/resolver.py:20-25`; tier models in `config.py:322-326` and `schema.py:39-89`. They agree today by manual mirroring (the schema docstring admits it: "Values are mirrored from the legacy duplicated locations").

**Decision to take:** keep `coreconfig` as the *typed registry* (Agent/CacheMode dataclasses + DEFAULT_CACHE_MODES — that part is the keystone and is live), and **delete or quarantine the duplicated config-cascade half** (`default_config`, `load`, `route`, `resolve_model`, `resolve_keywords`, `resolve_priority`) until `config.py` itself is rebuilt on the spine. Do not let two `load()`s coexist with different env semantics.

## P1-6 — `--tools ""` cache footgun still live in maestro_runner

`maestro_runner.py:93` builds the L2 telegram maestro with `--tools ""`. Stripping tool definitions changes the system-prompt prefix vs every other claude invocation in the codebase and disables the cache-anchor strategy the new engine documents (`session_runner.py:6-8` — defs PRESENT, usage blocked via `--disallowedTools`). It's also stateless-per-call (`--no-session-persistence`, no resume), i.e., the exact O(t)-per-call shape v1 was built to kill. `cmd_maestro` (`cli.py:2444-2469`) is its only caller. Retire (PART 3).

## P1-7 — `cache_modes/` package modules are dead

`cache_modes/__init__.py` `get()` is imported by no one (grep: zero call sites). The per-mode modules' `warm()`/`MECHANISM`/`EXTRA_FLAGS` are never read; `resolve_cache_mode()` consumes only `CacheMode.warm_module` strings pointing at `burnless.warm_session*` directly. So `codex_api.py`, `gemini_api.py`, `gemini_subscription.py`, `codex_subscription.py`, `none.py`, `anthropic_*.py` are documentation cosplaying as wiring. Answering the PART 4 question early: **the existing codex_*/gemini_* cache_modes are dead** — what's wired is just `warm_module` (claude → works; codex → broken per P1-4; gemini/ollama → `None`, structurally cold, correct).

**Fix:** either delete the package and keep the dataclass registry, or make the registry point at these modules and have callers consume them (one mechanism, not two). Recommend delete — fewer places to lie.

## P2-8 — error-handling and smaller correctness gaps

- **`cmd_do --mode` config patch race** (`cli.py:2498-2527`): rewrites `.burnless/config.yaml` on disk and restores it in `finally`. Two parallel `burnless do --mode ...` interleave write/restore → user config clobbered. Fix: thread a mode override parameter into `execute_delegation` instead of mutating shared disk state.
- **Bare `burnless` boots the "removed" legacy REPL** (`cli.py:3204-3206` → `shell.main()`), while `burnless shell` aliases to pty (`cli.py:2532-2538`, "legacy REPL removed in v0.7.4"). Inconsistent; the legacy REPL keeps `shell.py` (741 lines) + `natural_planner.py` + `chat_mode.py` alive. Re-point bare invocation at `cmd_chat` once P0-1/2 land.
- **`maestro_layer.py:132`** hardcodes `/opt/homebrew/bin/claude` (breaks any non-homebrew install; everything else uses `shutil.which` via `warm_session._claude_binary`). Also `_maestro_sessions` (`maestro_layer.py:44`) is an in-process dict — MCP server restart silently drops the "persistent" session and pays full re-read.
- **`live_runner.py:391`** injects `--permission-mode bypassPermissions` only when `command[0] in ("claude","claude-cli")` — false for `/opt/homebrew/bin/claude` or the rtk wrapper (compare `_worker_command`'s correct `Path(parts[0]).name` at `dispatcher.py:283`). Live config dodges it by hardcoding the flag; first user without it gets a worker frozen on stdin.
- **First chat delegation gets no runtime context:** `dispatcher.py:169-176` adds `_with_runtime_context` only `if chain:` — the first worker in a session receives the bare delegate line, no project-root/paths block. Compare `execute_delegation` which always adds it (`cli.py:798-804`).
- **`dispatcher.py:170`** imports from `..cli` inside the function — a cli↔dispatcher circular dependency held together by lazy imports. PART 2 step 1 removes it.
- **Silent empty maestro turns:** `runner_claude_json` returns `{"result":""}` on any failure (`maestro/runners.py:40-43`); `cmd_chat` prints nothing and waits for the next prompt — a dead claude binary looks like a mute maestro. Print a stderr notice when `result == "" and usage == {}`.
- **Dead helpers:** `dispatcher._blocked_capsule` (`dispatcher.py:461`) has zero callers; `_load_cloud_emulator_prompt` (`:339`) only test-called; `engine.partner_turn` (`engine.py:161-181`) superseded by `partner_turn_session` and kept alive only by tests — fine, but mark it test-only or fold the tests onto the session variant.
- **`maestro/base.py:105`** `elif proc.returncode != 0:` after `if proc.returncode == 0:` — second condition is always true; collapse to `else`.

## Cache-correctness summary (the direct answers)

- **Does the maestro cache hit turn-over-turn?** Mechanically yes (fork once, then `--resume <fork>` accumulates; `session_runner.py:37-40`), **but** the missing init-flags on forks (P0/P1-3) can defeat it; unverified empirically. The per-turn usage to prove it is already captured in `session.usages` — surface it.
- **Does the worker cache hit?** Via `burnless run`/`do`: yes for claude (warm fork in both `agents.py` and `live_runner.py`). Via `burnless chat`/`brain` (dispatcher): **no** (P0-2). Codex: **never** (P1-4). Gemini/ollama: intentionally cold (correct).
- **Any `--tools ""` footgun?** Yes, exactly one left: `maestro_runner.py:93` (P1-6). The new engine deliberately avoids it.
- **Is the Agent/CacheMode keystone live or scaffolding?** ~10% live: `resolve_cache_mode().warm_module` (agents.py) and `min_cache_tokens` (cached_worker.py). The rest — `Agent` resolution, `CacheMode.flags/headers/module`, the cache_modes package, the tier spine — has **zero run-path consumers** today.

---

# PART 2 — Kill the monoliths

Targets: `cli.py` 3213 lines, `live_runner.py` 1284, `agents.py` 782, `shell.py` 741, `chat_mode.py` 725, `dispatcher.py` 508 lines/17.2KB.

## God-functions (flagged)

| Function | Lines | Smell |
|---|---|---|
| `cli.execute_delegation` | 782-1364 (~580) | backend select + run + provider fallback + BLK lazy-fetch + bronze rescue + verify gate + retry loop + compression + metrics + state, one body |
| `cli.cmd_brain` | 1579-1955 (~376) | REPL + slash handler + keepalive daemon + encoder/decoder turn loop as nested closures |
| `live_runner.run_with_live_panel` | 265-741 (~476) | warm inject + flag policy + Popen + pump threads + liveness + render + stale detection |
| `dispatcher.run_delegate` | 115-277 | parse + route + plugins + subprocess + capsule + exec_log in one body |
| `cli.cmd_chat` | 2659-2758 | acceptable (~100), keep |

## Target module layout

```
src/burnless/
  prompt_context.py      # _with_runtime_context, _build_cacheable_runtime_prefix,
                         # _QTP_F_FIXED_SUFFIX, _TELEGRAPHIC_OUTPUT_HINT   (from cli.py:263-385)
  exec/
    runner.py            # execute_delegation + RunOpts + _apply_verify_gate + retry/rescue/lazy-fallback
    backends.py          # _run_with_maestro, _should_use_maestro_backend, _should_use_cached_worker
  cli/                   # cli.py becomes a package; burnless.cli keeps its import surface
    __init__.py          # build_parser, main, re-exports (back-compat for tests/plugins)
    cmd_core.py          # init, plan, delegate, do, run(thin), route, status
    cmd_chat.py          # cmd_chat + _chat_worker_usage_estimate
    cmd_brain.py         # cmd_brain + _run_basic_maestro_repl   (until retired, PART 3)
    cmd_warm.py          # warm init/status/refresh/daemon
    cmd_info.py          # metrics, economy, providers, decisions, read, log, capsule, watch, trace, debugless
  live_runner/           # split later: stream.py (_translate_stream_json/_detect_phase),
                         # panel.py (_WatchRenderer/_PanelEventFilter/_MinimalSpinner),
                         # overflow.py (is_context_overflow*, truncate, retries), core.py
```

Order (each step independently shippable, test-green):

1. **Extract `prompt_context.py`** — kills the dispatcher→cli circular import. Bronze (mechanical, spec below).
2. **Extract `exec/runner.py`** (execute_delegation + verify gate + backends). Silver (import surgery + one shared constant). Spec below.
3. cli → package with command groups (mechanical once 1-2 land; the remaining cmd_* functions have no cross-deps beyond module-level helpers).
4. live_runner split (stream/panel/overflow/core) — do *after* P0-1 lands so dispatcher can reuse `stream.py` for capsule extraction.
5. Retire `shell.py`+`chat_mode.py`+`natural_planner.py` with the legacy REPL (PART 3) — that's −1,740 lines for free; move `_load_claude_oauth_token` (the one thing `keepalive.py:13` imports from chat_mode) into `claude_integration.py` first.

## Dispatch-ready spec — STEP 1 (bronze)

```
burnless do --tier bronze "
EDIT FILES (repo /Users/roberto/antigravity/burnless, branch v0.9-agent-arch):
1. CREATE src/burnless/prompt_context.py: move from src/burnless/cli.py, byte-identical bodies:
   _QTP_F_FIXED_SUFFIX (cli.py:263-269), _TELEGRAPHIC_OUTPUT_HINT (cli.py:271-284),
   _build_cacheable_runtime_prefix (cli.py:300-329), _with_runtime_context (cli.py:332-385).
   Imports needed: from pathlib import Path; import sys.
2. In src/burnless/cli.py: delete the moved bodies; add
   'from .prompt_context import _with_runtime_context, _build_cacheable_runtime_prefix' near other imports.
3. In src/burnless/maestro/dispatcher.py line ~170: replace
   'from ..cli import _with_runtime_context' with 'from ..prompt_context import _with_runtime_context'.

EXECUTE SHELL:
- python3 -m pytest tests/ -q

HARD PROHIBITIONS: do NOT change any function body or signature; move verbatim. Do NOT touch any
other file. Do NOT reorder existing imports in cli.py beyond adding the one import line.

## Verify
test -f src/burnless/prompt_context.py
grep -q 'from .prompt_context import' src/burnless/cli.py
grep -q 'from ..prompt_context import _with_runtime_context' src/burnless/maestro/dispatcher.py
! grep -q 'from ..cli import _with_runtime_context' src/burnless/maestro/dispatcher.py
python3 -m pytest tests/ -q
"
```

## Dispatch-ready spec — STEP 2 (silver)

```
burnless do --tier silver "
GOAL: extract the execute_delegation god-function from cli.py into src/burnless/exec/runner.py,
keeping burnless.cli's public surface intact (tests import from burnless.cli).

EDIT FILES (repo /Users/roberto/antigravity/burnless):
1. CREATE src/burnless/exec/__init__.py (empty) and src/burnless/exec/runner.py. MOVE verbatim from cli.py:
   RunOpts (cli.py:78-88), execute_delegation (cli.py:782-1364), _apply_verify_gate (cli.py:711-766),
   _run_with_maestro (cli.py:130-221), _should_use_maestro_backend (cli.py:224-231),
   _should_use_cached_worker (cli.py:234-260), _build_retry_prompt (58-61), _build_audit_fix_prompt (64-68),
   MAESTRO_TIER_MODEL + ANTHROPIC_ENV_PATHS + DEFAULT_MAX_TOKENS (cli.py:70-75), _load_anthropic_key (90-104),
   _tier_has_multiple_providers (107-109), _select_provider_cfg (112-116), _record_provider_attempt (119-127),
   plus the private parse helpers execute_delegation calls: _extract_verify_block, _parse_chain_from_delegation,
   _parse_tier_from_delegation, _parse_goal_from_delegation, _parse_created_at_from_delegation,
   _extract_test_status, _infer_kind_hint, _normalize_report_kind, normalize_worker_envelope,
   _record_and_bump (cli.py:2561-2590). Resolve their module imports (paths_mod, config_mod, state_mod,
   metrics_mod, deleg_mod, compression_mod, lifetime_mod, agents_mod, live_runner, dashboard) by copying
   the same import lines cli.py uses.
2. In cli.py: delete moved bodies; add 'from .exec.runner import (execute_delegation, RunOpts,
   _apply_verify_gate, _load_anthropic_key, _record_and_bump, normalize_worker_envelope, _infer_kind_hint,
   _normalize_report_kind, MAESTRO_TIER_MODEL)' (extend the list with any name pytest reports missing —
   re-export rather than rewrite call sites).
3. grep -rn 'from burnless.cli import\|from .cli import\|cli\\.' src/ tests/ for stragglers; fix imports only.

EXECUTE SHELL:
- python3 -m pytest tests/ -q
- python3 -c 'from burnless.cli import execute_delegation, RunOpts'

HARD PROHIBITIONS: NO behavior changes, NO signature changes, NO refactor of the retry/rescue logic
(that's a later step). cli.py keeps re-exports so every existing import path still works. Do not move
cmd_* functions in this step.

## Verify
test -f src/burnless/exec/runner.py
python3 -c 'from burnless.cli import execute_delegation, RunOpts'
python3 -c 'from burnless.exec.runner import execute_delegation'
python3 -m pytest tests/ -q
wc -l src/burnless/cli.py   # expect < 2400
"
```

---

# PART 3 — Maestro fragmentation: canonical engine + migration

## The five entry points, adjudicated

| Entry | Path | Verdict |
|---|---|---|
| `burnless chat` | `cli.cmd_chat` (2659) → `maestro/base.maestro_base_init` → `MaestroSession` + `engine.partner_turn_session` → `dispatcher.run_all` | **CANONICAL.** Warm cached base, fork-per-cycle, tool-less by `--disallowedTools`, economy footer. Fix P0-1/2/3 and this is the product. |
| `burnless brain` | `cli.cmd_brain` (1579) → `maestro/core.run_maestro_turn` (Anthropic SDK streaming + encoder/decoder codec + keepalive) | **RETIRE after porting 2 features.** It's API-key-billed (SDK), maintains its own history jsonl + cache_control plumbing (`maestro/core.py:104-159`) — a parallel implementation of what the claude CLI gives chat for free. Port to chat: (a) the encoder/codec front-end (`codec/encoder.encode` + `police`) as an opt-in pre-processor on the chat input; (b) THINK panel rendering if wanted. Then `brain` becomes an alias of `chat`. |
| `burnless maestro <telegram>` | `cli.cmd_maestro` (2444) → `maestro_runner.run_maestro` | **DELETE.** Stateless-per-call, `--tools ""` footgun (P1-6), no session, superseded by the engine. Keep the CLI verb as a deprecation stub for one release: print pointer to `burnless chat`. |
| bare `burnless` → shell REPL → `natural_planner` | `cli.main:3204-3206` → `shell.main` (741 ln) → `natural_planner.plan_objective` (`shell.py:256`) | **RETIRE.** Re-point bare invocation at `cmd_chat`. Move `chat_mode._load_claude_oauth_token` to `claude_integration.py` (keepalive.py:13 depends on it), then delete `shell.py`, `chat_mode.py`, `natural_planner.py` (~1,740 lines). |
| MCP `maestro` tool | `mcp_server.py:424-434` → `maestro_layer.process_envelope` | **RE-POINT at the engine** (below). The public tool contract (envelope in → `{response_envelope, decoder_hint, usage, compression}` out) must survive. |

## Keeping the MCP `maestro` tool alive through the new engine

`maestro_layer.process_envelope` (maestro_layer.py:112-191) currently: resolves model → keeps a per-project session id in an in-memory dict (`:44`) → runs `claude -p --resume <sid>` with HARD RULES re-sent every message (`:48-55`) → parses stream-json. Replacement, same signature:

1. `root = project_root / ".burnless"`; `model` resolution unchanged.
2. `base_uuid = maestro_base_init(root, model)` (`maestro/base.py:62`) — the warm base replaces the HARD-RULES-every-turn hack: the partner role is already the cached prefix.
3. Persist `PartnerState` + `MaestroSession.fork_session_id` per project at `.burnless/maestro/partner_state.json` (new ~30-line load/save; dataclasses are JSON-trivial). This fixes the restart-amnesia of the in-memory dict.
4. One turn = `partner_turn_session(state, envelope, cfg=cfg, session=session, runner=partial(runner_claude_json, cwd=maestro_iso_cwd(root, model)), compact_fn=..., burnless_root=root)`; capture `session.usages[-1]` for the `usage` field.
5. Run delegate lines through `dispatcher.run_all` exactly as `cmd_chat` does (`cli.py:2735-2755`), feed capsules back, return the final response as `response_envelope` with the existing `decoder_hint`/`compression_telemetry` envelope so MCP clients see no contract change.
6. Delete `maestro_layer`'s subprocess/`_parse_stream_json`/hardcoded-binary code.

## End-to-end flow map (chat) with breaks flagged

```
user line
 → cmd_chat (cli.py:2714)                                     OK
 → partner_turn_session (engine.py:188)                       OK (window accounting + pending_seed)
 → MaestroSession.send → build_command (session_runner.py:22) ⚠ BREAK-3: fork flags drift vs base init (P0/P1-3)
 → runner_claude_json (runners.py:16)                         ⚠ silent "" on failure (P2)
 → _delegate_lines regex scan (cli.py:2705-2711)              OK (matches dispatcher's DELEGATE_RE)
 → dispatcher.run_all (dispatcher.py:90)                      OK (chain only carries last OK did — by design)
 → run_delegate → _worker_command → subprocess.run (:225)     ✗ BREAK-2: cold worker, project-cwd, no iso (P0-2)
                                                              ⚠ first delegation: no runtime context (:169-176)
 → _last_capsule (:396)                                       ✗ BREAK-1: stream-json never matches CAPSULE_RE (P0-1)
 → exec_log.finalize + capsule json (:267-276,:413-433)       OK
 → capsules fed back as next user text (cli.py:2753)          OK (depth cap 3 at :2738)
 → economy_snapshot footer (cli.py:2756)                      ⚠ worker cache_read=22k fiction (P0-2 aggravator)
```

Compaction (`maybe_compact`/rewind/`pending_seed`) is correctly OFF by default (`config.py:110` `rolling_compaction_enabled: False`) and the chat loop honors the contract (seed consumed on next send, `engine.py:210-219`). No break there.

---

# PART 4 — Multiprovider: codex / gemini / gemma-local (ollama) as encoder or any tier

## Ground truth first

- The "ONE config variable" mechanism that actually works today is `agents.<tier>.command` in `.burnless/config.yaml` — proven live: the project config already runs `ollama-bronze`/`gemini-*` tiers via wrapper scripts (`.burnless/config.yaml`: `ollama-bronze` → `.burnless/ollama_bronze.sh`, `gemini-bronze` → `.burnless/gemini_bronze.sh`).
- `resolve_cache_mode` (`coreconfig/resolver.py:201-209`): provider ∉ {anthropic, codex, gemini} → `"none"` → `warm_module=None` → `_inject_warm_fork_args` returns parts unchanged (`agents.py:617-618`). **The gemma/ollama no-cache path already exists and is correct.** What's missing is (i) an explicit `ollama` row in the matrix so it's a decision rather than a fallthrough, (ii) codex warm actually working (P1-4), (iii) the economy table, (iv) encoder selectability.
- `_detect_provider_from_parts` (`agents.py:567-584`) returns `None` for ollama/gemini → warm-fork skip is structural and silent. Correct behavior, keep.
- Encoder today: `codec/encoder.encode` is Anthropic-SDK-only (`encoder.py:7,217-229`) and the `encoder.model` config knob (`config.py:56`, resolved by `resolve_layer_models` `config.py:416-430`) is **never threaded into the call site** — `cli.py:1649` calls `encode(message, project_root=...)` with no model. Encoder selectability requires that thread + a subprocess backend.

## (a) Agent descriptor + CacheMode + warm-module mapping

| Agent | `Agent(...)` (coreconfig/schema.py:92-101) | resolve_cache_mode key | warm module | cache reality |
|---|---|---|---|---|
| codex-worker | `Agent(name="codex-worker", role="execute", provider="codex", auth="subscription", model="gpt-5.2")` | `codex_subscription` (schema.py:166) | `burnless.warm_session_codex` | byte-prefix cache; **dead until Spec B** (needs `warm_args`/`warm_prefix` protocol) |
| gemini-worker | `Agent(name="gemini-worker", role="execute", provider="gemini", auth="api", model="gemini-3-flash")` | `gemini_api` (schema.py:187) | `None` | structurally cold (Gemini context-cache integration is future work; `warm_module=None` is honest) |
| gemma-local | `Agent(name="gemma-local", role="execute", provider="ollama", auth="none", model="gemma-4-12b-it")` | falls through to `"none"` (resolver.py:202-205) | `None` | no prompt cache; $0 marginal; ADD explicit row below |

New schema row (the only schema change needed — makes ollama a first-class decision):

```python
# coreconfig/schema.py — add to DEFAULT_CACHE_MODES:
"ollama_none": CacheMode(
    name="ollama_none",
    module="burnless.cache_modes.none",
    mechanism="local_inference",       # no prompt cache; marginal cost ≈ $0
    warm_module=None,
    keepalive=False,
),
# coreconfig/resolver.py:202 — extend the provider set:
#   if agent.provider in {"anthropic", "codex", "gemini"}: ...
#   elif agent.provider in {"ollama", "ollama-local"}: key = "ollama_none"
```

### gemma-4-12b-it ollama setup — **OPS STEP, do not run from a worker**

The ollama registry may not carry `gemma-4-12b-it`; the source of truth is the GGUF at https://huggingface.co/unsloth/gemma-4-12b-it-GGUF. Exact commands (operator runs once):

```sh
# try registry first:
/opt/homebrew/bin/ollama pull gemma-4-12b-it || true
# if pull fails, build from the GGUF:
mkdir -p ~/models/gemma-4-12b-it
hf download unsloth/gemma-4-12b-it-GGUF gemma-4-12b-it-Q4_K_M.gguf \
   --local-dir ~/models/gemma-4-12b-it          # (or: huggingface-cli download ...)
cat > ~/models/gemma-4-12b-it/Modelfile <<'EOF'
FROM /Users/roberto/models/gemma-4-12b-it/gemma-4-12b-it-Q4_K_M.gguf
PARAMETER num_ctx 8192
EOF
/opt/homebrew/bin/ollama create gemma-4-12b-it -f ~/models/gemma-4-12b-it/Modelfile
# smoke:
echo "ack" | /opt/homebrew/bin/ollama run gemma-4-12b-it
```

## (b) `.burnless/config.yaml` snippets — flip encoder/bronze/any-tier per provider

The proven worker-invocation shape for ollama is the wrapper-script pattern (stdin prompt → JSON envelope on stdout), already battle-tested at `.burnless/ollama_bronze.sh` (it strips ANSI/spinner, salvages a JSON envelope, defaults status from rc). Reuse it parameterized:

```yaml
# bronze on gemma-local (one var: the command line)
agents:
  bronze:
    name: gemma-4-12b-it-local
    provider: ollama-local            # → cache mode none/ollama_none, warm skip is structural
    command: /Users/roberto/antigravity/burnless/.burnless/ollama_bronze.sh
    # model selected inside the wrapper via env:
    # BURNLESS_OLLAMA_BRONZE_MODEL=gemma-4-12b-it (export in ~/.zshrc or wrap in a 2-line sh)

# bronze on codex
agents:
  bronze:
    name: codex-gpt-5.4-mini-low
    provider: codex
    command: /Users/roberto/.local/bin/codex exec --skip-git-repo-check --model gpt-5.4-mini -c model_reasoning_effort=low --sandbox read-only

# bronze on gemini
agents:
  bronze:
    name: gemini-3.1-flash-lite
    provider: gemini
    command: /Users/roberto/antigravity/burnless/.burnless/gemini_bronze.sh

# encoder on gemma-local (requires Spec E threading below)
encoder:
  model: ollama:gemma-4-12b-it       # "ollama:" prefix selects the subprocess backend
```

Note `BURNLESS_OLLAMA_BRONZE_MODEL` default is currently `gemma4:31b-cloud` (`.burnless/ollama_bronze.sh:4`) — flipping to the local 12B is an env var, zero code.

## (c) Exact code touchpoints to make "change one var → provider+cache follow" TRUE

1. **`coreconfig/resolver.py:201-209`** — add the `ollama`/`ollama-local` → `ollama_none` branch + the schema row above. (Today it silently falls to `"none"`; same effect, explicit is better.)
2. **`agents.py:619-624` (`_inject_warm_fork_args`)** — replace the hard `fork_args` call with the warm protocol: `extra = getattr(_ws, "warm_args", getattr(_ws, "fork_args", None))(...)`; if the module exposes `warm_prefix`, return it so `_run_once` (`agents.py:661-725`) can prepend it to the prompt. This is the codex unlock (P1-4).
3. **`warm_session_codex.py`** — add `warm_args = warm_flags` and `warm_prefix = warm_brief` aliases (2 lines) so the protocol is satisfied without renaming.
4. **`live_runner.py:328-367`** — delete the hardcoded `if provider == "claude" ... else warm_session_codex` import pair and resolve the module via `resolve_cache_mode` exactly as `agents.py:610-619` does (one mechanism). Also re-wire `warm_codex_brief/_flags` callers or delete those params (`cli.py:819-821` stub).
5. **`maestro/dispatcher.py:225-233`** — route worker exec through `agents_mod.run(...)` (Spec C) so chat-dispatched workers get the same provider/cache treatment as `burnless run`.
6. **`pricing.py:3-28` + `economy.py:121,124-131`** — add rate families: `"gemma-local": {input:0, output:0, cache_read:0, cache_write:0}`; `"gpt"` and `"gemini"` entries with rates sourced from a new optional `pricing:` block in config.yaml (do NOT hardcode third-party prices in code; default them to sonnet-equivalent with a visible `[rate=assumed]` marker in the footer). Extend `_FAMILIES`/`model_family` to match `"gpt"`, `"gemini"`, `"gemma"` so non-anthropic workers stop being silently priced as sonnet.
7. **`cli.py:1649` + `codec/encoder.py:187-243`** — thread `model=resolve_layer_models(cfg)["encoder"]` into `encode()`; inside `encode()`, branch on `model.startswith("ollama:")` → subprocess `ollama run <model>` with the same cached-prefix text inlined (no cache_control — local inference, cache is irrelevant); keep the regex fallback `_fallback_capsule` (`encoder.py:418-421`) as the safety net. Pattern to copy: `debugless._call_ollama` (`debugless.py:70-88`) already does stdin→stdout ollama subprocess with timeout.
8. **`config.py:328-331` (`DEFAULT_PROVIDER_MODELS`)** — add `"ollama": "gemma-4-12b-it"`, `"gemini": "gemini-3-flash"` so default-model lookups never KeyError when future code paths pass those providers.

**Confirmation requested by the spec:** the existing `codex_*`/`gemini_*` cache_modes are **dead** (P1-7): `CacheMode.module` is never imported, `codex_subscription.warm_module` points at a module that doesn't satisfy the expected interface (P1-4), and `gemini_*`/`codex_api` have `warm_module=None` (cold by design). After touchpoints 2-4, codex becomes live; gemini stays declared-cold (honest) until a Gemini context-cache warm module exists.

## (d) Ready-to-dispatch worker specs

### SPEC A — gemma-local as bronze worker (bronze tier; config+script only, no engine changes)

```
burnless delegate --tier bronze "
GOAL: make gemma-4-12b-it (local ollama) selectable as the bronze worker via one config flip.
PRECONDITION (ops, already done by operator, do NOT run): ollama model 'gemma-4-12b-it' exists
(ollama pull or Modelfile from unsloth/gemma-4-12b-it-GGUF — see FABLE_SENIOR_REVIEW PART 4a).

EDIT FILES:
1. CREATE .burnless/ollama_gemma.sh: copy .burnless/ollama_bronze.sh byte-for-byte, then change
   only line 4 to: MODEL=\"\${BURNLESS_OLLAMA_BRONZE_MODEL:-gemma-4-12b-it}\"
2. chmod +x .burnless/ollama_gemma.sh
3. In .burnless/config.yaml: under agents:, ADD (do not replace bronze):
     gemma-bronze:
       name: gemma-4-12b-it-local
       provider: ollama-local
       command: /Users/roberto/antigravity/burnless/.burnless/ollama_gemma.sh
       role: summaries_classification_readonly
       use_for: [summarize, classify, clean_logs]

EXECUTE SHELL:
- echo 'Summarize in one line: burnless saves tokens by tiering.' | .burnless/ollama_gemma.sh
  (expect a one-line JSON with a \"status\" field; if ollama model missing, report BLK citing the ops step)
- python3 -c \"import yaml; yaml.safe_load(open('.burnless/config.yaml'))\"

HARD PROHIBITIONS: do NOT modify src/**. Do NOT replace the existing bronze agent entry — add
gemma-bronze alongside. Do NOT run ollama pull/create (ops step). Do NOT pipe outputs through tail.

## Verify
test -x .burnless/ollama_gemma.sh
grep -q 'gemma-4-12b-it' .burnless/ollama_gemma.sh
python3 -c \"import yaml; cfg=yaml.safe_load(open('.burnless/config.yaml')); assert 'gemma-bronze' in cfg['agents']\"
echo ack | .burnless/ollama_gemma.sh | python3 -c \"import json,sys; json.loads(sys.stdin.read())\"
"
```

(To make gemma THE bronze: swap `agents.bronze.command` to the script — same one-variable promise, reversible.)

### SPEC B — codex/gemini workers: warm protocol + registry unification (silver)

```
burnless delegate --tier silver "
GOAL: make the codex warm cache actually fire and unify warm-module resolution through
coreconfig.resolve_cache_mode, so provider choice in agents.<tier> drives cache behavior.

CONTEXT: warm_session_codex has warm_flags() (line 366) + warm_brief() (line 382) but agents.py:619-624
and live_runner.py:340-341 call fork_args(), which doesn't exist → AttributeError swallowed → codex
always cold. gemini/ollama must remain a SILENT structural skip (warm_module=None).

EDIT FILES (repo /Users/roberto/antigravity/burnless):
1. src/burnless/warm_session_codex.py: append module-level aliases:
     def warm_args(burnless_root, model): return warm_flags(burnless_root, model)
     def warm_prefix(burnless_root, model): return warm_brief(burnless_root, model)
   src/burnless/warm_session.py: append:
     warm_args = fork_args
     def warm_prefix(burnless_root, model): return ''
2. src/burnless/agents.py _inject_warm_fork_args (587-658): resolve the entry point as
   fn = getattr(_ws, 'warm_args', None) or getattr(_ws, 'fork_args', None); use fn(...).
   Change the function to ALSO return the prefix: new signature
   _inject_warm_fork_args(parts, cwd) -> tuple[list[str], str]  (prefix '' when none).
   In _run_once (661-725): parts, warm_prefix = _inject_warm_fork_args(...); if warm_prefix:
   prompt = warm_prefix + prompt. Update ALL call sites (grep _inject_warm_fork_args in src/ and tests/).
3. src/burnless/coreconfig/schema.py: add the ollama_none CacheMode row (mechanism='local_inference',
   warm_module=None). src/burnless/coreconfig/resolver.py resolve_cache_mode: map providers
   {'ollama','ollama-local'} -> 'ollama_none'.
4. src/burnless/live_runner.py 328-367: replace the claude/codex if/else module import with the same
   resolve_cache_mode lookup agents.py uses (provider detect-vocab 'claude' -> Agent-vocab 'anthropic');
   when cmode.warm_module is None, skip silently (NO cold-warning for gemini/ollama).
   Apply warm_args as today's warm_args injection; apply warm_prefix by prefixing the prompt
   (replaces the dead warm_codex_brief param path — leave the params in place, just stop relying on them).
5. tests: add tests/test_warm_protocol.py covering: (a) codex agent_cfg gets warm_flags injected and
   prompt prefixed (monkeypatch warm_session_codex.warm_flags/warm_brief); (b) ollama-local provider →
   parts unchanged, no stderr warning; (c) claude path unchanged (fork args still injected).

EXECUTE SHELL:
- python3 -m pytest tests/ -q

HARD PROHIBITIONS: do NOT rename warm_flags/warm_brief/fork_args (aliases only — external callers exist).
Do NOT touch dispatcher.py in this spec. Do NOT add a gemini warm module (out of scope; gemini stays
declared-cold). Do NOT print WARN for providers whose cache mode has warm_module=None.

## Verify
python3 -c \"from burnless import warm_session_codex as w; assert callable(w.warm_args) and callable(w.warm_prefix)\"
python3 -c \"from burnless.coreconfig.resolver import resolve_cache_mode; from burnless.coreconfig.schema import Agent; m=resolve_cache_mode(Agent(name='x',role='execute',provider='ollama',auth='none')); assert m.warm_module is None\"
python3 -m pytest tests/ -q
python3 -m pytest tests/test_warm_protocol.py -q
"
```

### SPEC C — chat dispatcher: stream-json capsules + warm workers (silver; fixes P0-1 + P0-2)

```
burnless delegate --tier silver "
GOAL: burnless chat workers must (1) have their capsule extracted from stream-json output and
(2) launch through agents.run so they fork the warm base instead of spawning cold.

EDIT FILES (repo /Users/roberto/antigravity/burnless):
1. src/burnless/maestro/dispatcher.py _last_capsule (396-403): before the raw-line scan, iterate
   stdout lines; for each line that json.loads to a dict with type=='result', scan its 'result'
   string (splitlines) with CAPSULE_RE and remember the last match. Keep the existing raw-line scan
   as fallback for plain-text workers. Pure function — add unit tests with a realistic stream-json
   transcript (system/assistant/result events) in tests/test_dispatcher.py.
2. src/burnless/maestro/dispatcher.py run_delegate (191-276): replace the inline subprocess.run
   block (217-243) with:
     result = agents_mod.run(agent_cfg, stdin_payload, timeout=int(config.get('maestro',{}).get('worker_timeout_s',900)), cwd=project_root, tier=tier_key)
   where stdin_payload is the existing (system_prompt + user_message) composition from
   _inject_system_prompt. Map result: stdout=result['stdout'], stderr=result['stderr'],
   returncode=result['returncode']; on result.get('timed_out') call _finalize_error with ERR
   (preserve current timeout message shape). Keep plugin hooks H1/H7/H2 exactly where they are.
   NOTE: agents.run already strips ANTHROPIC_API_KEY and injects warm-fork args — delete the
   now-duplicated env handling in run_delegate.
3. Always add runtime context: at 169-176, call _with_runtime_context unconditionally (chain may be
   empty list — pass chain=chain or None; the function already handles missing capsules).
4. tests: extend tests/test_dispatcher.py: fake worker script that emits stream-json with the capsule
   inside the result event → run_delegate returns OK capsule; fake plain-text worker still works.

EXECUTE SHELL:
- python3 -m pytest tests/ -q

HARD PROHIBITIONS: do NOT change CAPSULE_RE or the capsule line grammar. Do NOT remove the plain-text
fallback. Do NOT alter exec_log entry shape (status/files/issues/transcript fields stay). Do NOT touch
cli.py except if an import needs updating. Keep H1/H7/H2 plugin hook semantics byte-compatible.

## Verify
python3 -m pytest tests/test_dispatcher.py -q
python3 -m pytest tests/ -q
grep -q 'agents_mod.run(' src/burnless/maestro/dispatcher.py
python3 - <<'PY'
from burnless.maestro.dispatcher import _last_capsule
import json
line = json.dumps({'type':'result','result':'brz sum docs/x.md :: OK summarized [ref:exec/T0001]'})
cap = _last_capsule('{\"type\":\"system\"}\n'+line)
assert cap and cap.startswith('brz sum'), cap
PY
"
```

### SPEC E (optional, bronze) — encoder selectable to gemma-local

```
burnless delegate --tier bronze "
EDIT FILES:
1. src/burnless/cli.py:1649: layers = config_mod.resolve_layer_models(cfg); pass
   model=(layers.get('encoder') or encoder_mod.DEFAULT_ENCODER_MODEL) into encoder_mod.encode
   (skip when value is 'passthrough' — keep current behavior: encode not called... actually
   passthrough today still encodes; preserve existing behavior, only thread the model string).
2. src/burnless/codec/encoder.py encode(): if model.startswith('ollama:'): build prompt =
   _build_cached_prefix(...) + '\n\n' + _build_user_part(compressed); run
   subprocess.run(['/opt/homebrew/bin/ollama','run',model.split(':',1)[1]], input=prompt,
   capture_output=True, text=True, timeout=60); on any failure return
   (_fallback_capsule(raw_message), 0.6); else parse via _extract_capsule_text/_wrap_capsule_lines
   exactly like the anthropic branch. No cache_control (local inference).
3. tests/test_encoder_ollama.py: monkeypatch subprocess.run; assert capsule extraction + fallback path.

EXECUTE SHELL:
- python3 -m pytest tests/ -q

HARD PROHIBITIONS: anthropic SDK branch byte-identical. ollama binary path via shutil.which('ollama')
with /opt/homebrew/bin/ollama fallback (mirror warm_session._claude_binary pattern, agents must not
hardcode-only). Never raise out of encode() — fallback capsule on every error.

## Verify
grep -q \"ollama\" src/burnless/codec/encoder.py
python3 -m pytest tests/test_encoder_ollama.py tests/ -q
"
```

**Smallest path summary:** gemma-bronze = SPEC A alone (zero engine changes — wrapper + config). Codex/gemini as cache-honest workers = SPEC B. Chat using any of them warm+parsed = SPEC C. Encoder on gemma = SPEC E. After A+B+C, "change `agents.<tier>` → provider AND cache behavior follow" is true for anthropic (warm fork), codex (byte-prefix warm), gemini (declared cold), ollama (declared cold, $0).

---

## Final report (5 bullets)

1. **Worst P0:** `burnless chat`'s worker loop is broken-by-config-default twice over — `dispatcher._last_capsule` (dispatcher.py:396) cannot parse the stream-json output the default/live tier commands emit (every delegation → `PART missing worker capsule`), and `run_delegate` (dispatcher.py:225) spawns workers cold in the project cwd, bypassing the warm-fork machinery the entire cost thesis depends on — while the economy footer hardcodes a 22k cache_read (cli.py:2653) that never happened. SPEC C fixes both.
2. **Monolith first step:** extract `prompt_context.py` from cli.py (cli.py:263-385) — 30-minute bronze move that kills the dispatcher→cli circular import; then silver-extract `execute_delegation` (cli.py:782-1364, the ~580-line god-function) into `exec/runner.py` with cli re-exports. Both specs are dispatch-ready in PART 2.
3. **Canonical maestro = `burnless chat` (new engine).** Delete `cmd_maestro`/`maestro_runner` (the `--tools ""` footgun, maestro_runner.py:93); retire `brain` after porting the encoder/codec front-end to chat; retire the bare-`burnless` legacy shell REPL (cli.py:3204) → −1,700 lines; keep the MCP `maestro` tool's public contract by rebuilding `maestro_layer.process_envelope` on `maestro_base_init` + `partner_turn_session` with PartnerState persisted to `.burnless/maestro/partner_state.json`. One urgent engine fix: the per-turn fork omits the cache-stabilizing flags the base init uses (session_runner.py:34-41 vs base.py:83-92) — likely `system_changed` cache miss every turn.
4. **Agent/CacheMode keystone: ~10% live, 90% scaffolding.** Live: `resolve_cache_mode().warm_module` (agents.py:613-616) and `min_cache_tokens` (cached_worker.py:288). Dead: the whole `cache_modes/` package (never imported), `CacheMode.flags/module/headers` (no consumers — and the recorded flag `--exclude-dynamic` is wrong, schema.py:155), the duplicated tier/config/route half of `coreconfig` (only tests use it; its `load()` ignores `BURNLESS_GLOBAL_CONFIG`, resolver.py:90), and the codex warm wiring (registry promises `fork_args`; warm_session_codex only has `warm_flags`/`warm_brief` → always cold, agents.py:619).
5. **Minimal diff for gemma-local as bronze: zero engine code.** Ops step (ollama pull or Modelfile from unsloth/gemma-4-12b-it-GGUF), a 1-line variant of the existing `.burnless/ollama_bronze.sh` (default model → `gemma-4-12b-it`), and an `agents.gemma-bronze` (or `agents.bronze`) entry pointing at it — the `provider: ollama-local` → cache-mode `none` → warm-skip path already behaves correctly end-to-end (resolver.py:202-205, agents.py:617-618). SPEC A ships it.

<!-- grep-anchors: monolith|cli.py · gemma · ollama · CacheMode · PART 1-4 -->
