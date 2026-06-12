# Burnless — `doctor` + Setup Hardening Design

> Status: design (d651). No implementation in this doc.
> Goal: after `pip install burnless && burnless setup && burnless doctor`, the user has a **deterministic, machine-verified** answer to "is this correct?" — instead of finding out only when it breaks.

## 0. Problem restatement (grounded in code)

Three gaps verified against `src/burnless/`:

1. **No healthcheck.** 50+ subcommands, none inspects install state (`grep doctor src/` → 0 hits). "Está certo?" has no deterministic answer.
2. **`init` copies agents instead of inheriting.** `cmd_init` (`cli.py:180-181`) builds `agents_override` and writes it into the **project** `config.yaml`. That's the drift source: project `silver` freezes a snapshot and diverges from global (the gemma-vs-anthropic case). Note: `setup_wizard.run` (`setup_wizard.py:375-397`) **already** does the correct thing — seeds global once, then strips `agents` from project config. So **init is the outlier**, not the whole codebase.
3. **`setup` has no version stamp + no MCP guarantee.** The Claude Code wiring (`init_claude_code.wire_settings_hook`) is *already* idempotent (byte-compare on managed files, `already_mode`/`already_seed` guards on hooks). What's missing: (a) a `setup_version` stamp so an upgraded Burnless can tell a stale wire from a current one; (b) MCP server registration — `mcp_server.py` runs only via `python -m burnless.mcp_server`, there is no `burnless mcp` subcommand and nothing runs `claude mcp add`, so post-setup the MCP tools are not guaranteed reachable.

The "every chat works" guarantee lives in **two machine-level (not project-level) layers**, both already present but unverified:

- **SessionStart hook** → `~/.claude/settings.json` runs `burnless_session_seed.sh` regardless of cwd (fail-open, reads `~/.burnless/state/pending_seed.md`).
- **Global config** → `~/.config/burnless/config.yaml` resolves tiers without a local `.burnless/`.

If setup pins those two idempotently+versioned, and doctor proves they're present, every chat opens full. **Doctor is the keystone** — it's the read-only oracle that makes the other two fixes provable.

---

## 1. `burnless doctor` — specification

### 1.1 Contract

- **Read-only by default.** Never mutates unless `--fix` is passed.
- **Deterministic.** Same machine state → same output. No LLM in the path.
- **Sectioned checks**, each emitting one of `PASS` / `WARN` / `FAIL`, plus a one-line `fix:` when not PASS.
- **Exit codes:** `0` = all PASS (WARN allowed), `1` = at least one FAIL, `2` = doctor itself errored (couldn't run a check). This lets `setup && doctor` gate in CI / install scripts.
- **`--json`** emits a machine envelope for the MCP layer and for `burnless status`.
- **`--fix`** runs the safe auto-remediations (re-wire hook, re-seed global agents, register MCP, re-stamp version) then re-runs checks. Anything not safely auto-fixable stays FAIL and prints the manual command.

### 1.2 Check catalog

Grouped into four bands. `[fix]` = auto-remediable via `--fix`; `[manual]` = doctor prints the command but won't run it.

**Band A — Install integrity**
- `A1 binary` — `burnless` resolves on PATH; `__version__` readable. `[manual]`
- `A2 python` — interpreter ≥ required; `import burnless` clean. `[manual]`
- `A3 deps` — `mcp`, `yaml` importable (MCP server imports `mcp.server`). `[manual]`

**Band B — Global machine layer (the "every chat" guarantee)**
- `B1 global-config` — `~/.config/burnless/config.yaml` exists and parses. `[fix: write_default agents from autodetect]`
- `B2 global-agents` — config has a complete `agents` block (gold/silver/bronze each have a non-empty `command`). `[fix]`
- `B3 tier-resolves` — for each tier, the worker binary in `command` is on PATH (e.g. `claude`/`codex`/`gemini`/`ollama`). Missing binary → FAIL with the install hint. `[manual]`
- `B4 provider-creds` — at least one provider referenced by the agents block has a usable credential (env key or CLI session). `WARN` if a configured provider lacks creds but another tier still works; `FAIL` if no tier can run. `[manual]`

**Band C — Claude Code wiring (per-machine, cwd-independent)**
- `C1 settings-json` — `~/.claude/settings.json` exists and parses. `[fix: create empty + wire]`
- `C2 sessionstart-hook` — `SessionStart` contains the `burnless_session_seed.sh` entry. Mirrors `init_claude_code.wire_settings_hook` detection. `[fix]`
- `C3 userprompt-hook` — `UserPromptSubmit` contains `burnless_mode_hook.sh`. `[fix]`
- `C4 managed-scripts` — each file in `init_claude_code._MANAGED` exists in `~/.claude/...` AND byte-matches the packaged template (this is the **stale-wire** detector). Mismatch → `WARN` (works but outdated). `[fix: re-copy]`
- `C5 seed-pointer-sane` — `~/.burnless/state/pending_seed.md` parent dir is writable (hook fail-opens, but doctor surfaces the silent breakage). `[fix: mkdir]`

**Band D — MCP guarantee**
- `D1 mcp-importable` — `python -m burnless.mcp_server --check` exits clean (new lightweight self-check flag, no stdio loop). `[manual]`
- `D2 mcp-registered` — burnless MCP server present in the Claude MCP registry (`claude mcp list` parse, or `~/.claude.json` / project `.mcp.json` contains a `burnless` entry). Repo `.mcp.json` today only has `supabase` → this is the gap doctor catches. `[fix: claude mcp add burnless -- python -m burnless.mcp_server]`
- `D3 setup-version` — `~/.config/burnless/setup_meta.json` exists and its `setup_version` matches the current package `SETUP_VERSION`. Mismatch → `WARN: re-run burnless setup` (the upgrade-staleness signal). `[fix: re-stamp after re-running remediations]`

### 1.3 Output — example PASS

```
burnless doctor                                            v0.9.0

Install
  PASS  binary            burnless on PATH (0.9.0)
  PASS  python            3.14  ·  import burnless OK
  PASS  deps              mcp, yaml importable

Global layer  (~/.config/burnless)
  PASS  global-config     config.yaml parses
  PASS  global-agents     gold·silver·bronze all mapped
  PASS  tier-resolves     claude on PATH for all 3 tiers
  PASS  provider-creds    anthropic key present

Claude Code wiring  (~/.claude)
  PASS  settings-json     parses
  PASS  sessionstart      burnless_session_seed.sh wired
  PASS  userprompt        burnless_mode_hook.sh wired
  PASS  managed-scripts   6/6 match packaged templates
  PASS  seed-pointer      ~/.burnless/state writable

MCP
  PASS  mcp-importable    burnless.mcp_server self-check OK
  PASS  mcp-registered    'burnless' in claude mcp list
  PASS  setup-version     2 (current)

15 PASS · 0 WARN · 0 FAIL                                  exit 0
Every chat opens full. ✓
```

### 1.4 Output — example FAIL (with fixes)

```
burnless doctor                                            v0.9.0

Install
  PASS  binary            burnless on PATH (0.9.0)
  PASS  python            3.14  ·  import burnless OK
  PASS  deps              mcp, yaml importable

Global layer  (~/.config/burnless)
  PASS  global-config     config.yaml parses
  WARN  global-agents     silver overrides global with frozen snapshot
        fix: burnless doctor --fix   (re-seeds project to inherit global)
  FAIL  tier-resolves     silver → 'codex' not on PATH
        fix: install codex, or  burnless models set silver claude:sonnet --default

Claude Code wiring  (~/.claude)
  PASS  settings-json     parses
  FAIL  sessionstart      burnless_session_seed.sh NOT wired
        fix: burnless doctor --fix     (or  burnless init --claude-code)
  PASS  userprompt        burnless_mode_hook.sh wired
  WARN  managed-scripts   1/6 stale: burnless_mode_hook.sh differs from template
        fix: burnless doctor --fix     (re-copies packaged version)
  PASS  seed-pointer      ~/.burnless/state writable

MCP
  PASS  mcp-importable    burnless.mcp_server self-check OK
  FAIL  mcp-registered    'burnless' NOT in claude mcp list
        fix: burnless doctor --fix     (claude mcp add burnless -- python -m burnless.mcp_server)
  WARN  setup-version     stamped 1, current 2 — wire may be outdated
        fix: burnless setup     (or  burnless doctor --fix)

10 PASS · 3 WARN · 3 FAIL                                  exit 1
3 issues block 'every chat opens full'. Run: burnless doctor --fix
```

### 1.5 Internal shape

New module `src/burnless/doctor.py`:

- `@dataclass Check: id, band, status('PASS'|'WARN'|'FAIL'), detail, fix_hint, auto_fixable: bool, fixer: Callable|None`
- `run_checks(*, fix: bool) -> list[Check]` — pure inspection; if `fix`, invokes `check.fixer()` for `auto_fixable` FAIL/WARN then re-evaluates that check once.
- `render_human(checks) -> str` and `render_json(checks) -> dict`.
- Reuse, don't reimplement: detection from `setup_wizard.detect()`, hook detection logic factored out of `init_claude_code.wire_settings_hook` (extract a `_is_wired(home) -> dict[str,bool]` helper so doctor and the wirer share one source of truth), tier resolution from `config.resolve_model` / agents block, paths from `config.global_config_path()`.

`cmd_doctor(args)` in `cli.py` + `sub.add_parser("doctor", ...)` with `--fix`, `--json`, `--quiet`.

---

## 2. Setup — idempotency + versioning

### 2.1 What's already idempotent (keep)

- `wire_settings_hook` guards with `already_mode`/`already_seed` — re-run does **not** duplicate. ✓
- `_MANAGED` copy compares bytes, prints `skipped`/`EXISTS_DIFFERENT`. ✓
- `setup_wizard.run` updates config in place, seeds global only when absent. ✓

### 2.2 What to add

**Version stamp.** Introduce `SETUP_VERSION: int` in `burnless/__init__.py` (bump whenever the wire contract changes: managed-scripts set, hook commands, MCP registration shape). On successful `setup` (and `doctor --fix`), write `~/.config/burnless/setup_meta.json`:

```json
{ "setup_version": 2, "burnless_version": "0.9.0", "wired_at": "2026-06-12T...", "mcp_registered": true }
```

Doctor check `D3` compares stamped vs current. This is the single signal that turns "I upgraded Burnless, is my wire stale?" from unanswerable into a WARN.

**MCP registration in setup.** After Claude Code wiring, setup attempts `claude mcp add burnless -- python -m burnless.mcp_server` (idempotent: check `claude mcp list` first; skip if present). Fail-open with a printed manual command if `claude` CLI absent. Records `mcp_registered` in `setup_meta.json`.

**Self-check flag for the server.** Add `--check` to `mcp_server.py` `main()` that imports the tool table and exits 0 without entering the stdio loop — so doctor `D1` can verify the server boots without hanging.

### 2.3 Idempotency invariant (the contract to test)

> Running `burnless setup` (non-interactive) N times produces byte-identical `~/.claude/settings.json`, `~/.config/burnless/config.yaml`, and `setup_meta.json` after run 1. No duplicated hook entries, no growing arrays, no agents block re-appearing in project config.

This becomes a pytest: run setup twice in a tmp `$HOME`, assert files equal after run 2 == run 1.

---

## 3. Init — inherit instead of copy

### 3.1 The fix

`cmd_init` must stop writing `agents_override` into the **project** config. Adopt the `setup_wizard` pattern verbatim:

- Seed the **global** `~/.config/burnless/config.yaml` `agents` block **only if absent** (never clobber).
- Write the **project** `.burnless/config.yaml` with **no `agents` key** — it cascades via `config.load`'s `_deep_merge(global, project)`.
- Project config keeps only genuinely-local overrides (project_name, plan, per-project routing tweaks) and may override a single tier intentionally — but the default is empty so global wins.

`config.write_default` already supports `agents_override=None` (writes the package default agents). For project files we want the agents key **stripped entirely**, not defaulted — so reuse the `setup_wizard.run` lines 390-397 strip logic, or add `config.write_project_default(path)` that omits `agents`.

### 3.2 Migration for existing projects

A frozen `agents` block already sits in many `.burnless/config.yaml`. Doctor `B2`/`global-agents` detects a project agents block that merely duplicates global and flags WARN with `fix: burnless doctor --fix` → strips the redundant project block. If the project block genuinely differs (intentional override) doctor stays silent. This makes the drift self-healing without a destructive blanket rewrite.

---

## 4. MCP — guarantee it's reachable

Three-part guarantee, all provable by doctor:

1. **Bootable** — `python -m burnless.mcp_server --check` (new flag) imports the tool table cleanly → `D1`.
2. **Registered** — setup runs `claude mcp add burnless -- python -m burnless.mcp_server` idempotently; doctor `D2` parses `claude mcp list` (fallback: scan `~/.claude.json` and project `.mcp.json` for a `burnless` server key).
3. **Stamped** — `setup_meta.json.mcp_registered = true` so a later doctor can distinguish "never registered" from "registered then removed."

Open decision (flag for Roberto, not blocking): register MCP **globally** (user scope, every project) vs **project-scoped** (`.mcp.json` per repo). Recommendation: **user scope** — consistent with the "machine layer, not project layer" guarantee. Project `.mcp.json` stays reserved for project-specific servers (e.g. supabase).

---

## 5. Prioritization — what blocks what

```
P0  doctor (read-only)  ← keystone. Makes 2 & 3 provable. Nothing depends on the others first.
P0  init inherit fix    ← stops new drift. Small, isolated, mirrors existing setup_wizard code.
P1  setup_version stamp ← needed for doctor D3 to be meaningful (D3 degrades to skip without it).
P1  MCP register+check  ← needed for doctor D2/D1 to be meaningful.
P2  doctor --fix        ← remediation; depends on D-checks + remediation helpers existing.
P2  idempotency pytest  ← locks the contract; depends on stamp + register landing.
```

**Order rationale:** Doctor read-only ships first and *immediately* delivers value (you can finally see the state) even before any fix lands — the checks for not-yet-built features (D2/D3) just report `WARN: not configured`. Init-inherit ships in parallel (independent file, no doctor dependency). Then stamp + MCP fill in so D2/D3 go from informational to enforced. Then `--fix` and the contract test.

---

## 6. Tiering — who builds each part

| Part | Tier | Why |
|---|---|---|
| `doctor.py` read-only checks + render | **Silver** | Mechanical: inspect files, compare bytes, format output. Clear spec, deterministic. |
| `cmd_doctor` + parser wiring | **Silver** | Boilerplate following 40 existing `cmd_*`. |
| init-inherit fix (`cmd_init`) | **Silver** | Port existing setup_wizard lines; small surface. |
| `setup_meta.json` stamp + `SETUP_VERSION` | **Silver** | Add constant + write json; trivial. |
| MCP `--check` flag + `claude mcp add` in setup | **Silver** | Subprocess + idempotent guard. |
| `doctor --fix` remediation wiring | **Silver** | Reuses existing wirers; orchestration only. |
| Decision: MCP user-scope vs project-scope | **Gold/Roberto** | Architectural; one call, already recommended above. |
| Idempotency + doctor pytest suite | **Silver** | Standard tmp-HOME fixtures. |

No Bronze work here (everything touches logic/structure, not summarization). No Gold *implementation* — Gold's only role was this design + the one scope decision.

**P0 vs P1 features:**
- P0: read-only doctor (Bands A/B/C), init-inherit fix.
- P1: MCP register + `--check` (Band D1/D2), setup_version stamp (D3).
- P2: `doctor --fix`, idempotency test.

---

## 7. Roadmap — phased, T-shirt sized

### Fase 1 — Doctor read-only (S/M) · P0
`doctor.py` with Bands A/B/C checks (Band D reports "not configured" gracefully), human + `--json` render, `cmd_doctor` + parser, exit codes. Extract `_is_wired()` helper shared with `init_claude_code`. **Deliverable:** `burnless doctor` prints accurate state, exit 0/1/2. **Size: M.**

### Fase 2 — Init inherit (S) · P0 · parallel with Fase 1
`cmd_init` stops writing project agents; seeds global-if-absent; strips project agents. **Deliverable:** fresh `init` produces a project config with no `agents` key; doctor B2 PASS. **Size: S.**

### Fase 3 — Setup versioning (S) · P1
`SETUP_VERSION` constant + `setup_meta.json` write in setup; doctor D3 enforces. **Size: S.**

### Fase 4 — MCP guarantee (M) · P1
`mcp_server --check` flag; `claude mcp add` idempotent in setup; doctor D1/D2 enforce. **Size: M.**

### Fase 5 — Doctor --fix + contract test (M) · P2
Wire `auto_fixable` fixers; `--fix` re-runs checks; pytest for idempotency invariant (§2.3) + doctor-on-broken-HOME. **Size: M.**

**Critical path:** Fase 1 → (3,4) → 5. Fase 2 runs anytime, independent.

---

## 8. Ready-to-dispatch Silver spec — Fase 1 (Doctor read-only)

> This is the one spec to fire at Silver after this design is approved. Scoped to P0 read-only doctor + the shared wiring helper. FaseS 2-5 get their own specs later.

### Goal
Add `burnless doctor` — a read-only, deterministic install healthcheck. No `--fix` yet (Fase 5). Bands A/B/C fully implemented; Band D checks present but report `WARN: not configured` when the underlying feature (MCP register / setup_meta) is absent.

### EDIT FILES
- `src/burnless/init_claude_code.py` — extract a pure helper `is_wired(home: Path) -> dict` returning `{"sessionstart": bool, "userprompt": bool, "managed": list[tuple[str,str,bool]]}` (rel_path, abs_path, byte_matches_template). Refactor `wire_settings_hook` to consume it (no behavior change — existing tests must still pass).
- `src/burnless/cli.py` — add `cmd_doctor(args)` (follow the shape of nearby `cmd_*`); add `sub.add_parser("doctor", help="healthcheck install + wiring; exit 1 if anything broken")` with flags `--json` (action store_true), `--quiet` (action store_true); register `doctor=cmd_doctor` in the dispatch table.
- `src/burnless/mcp_server.py` — add an early `--check` branch in `main()`/`__main__`: if `--check` in argv, build the tool table, print `ok`, exit 0 (do NOT enter stdio loop). Used by doctor D1.

### CREATE FILES
- `src/burnless/doctor.py` — module per §1.5:
  - `@dataclass Check(id, band, status, detail, fix_hint)`.
  - `def run_checks() -> list[Check]` implementing A1-A3, B1-B4, C1-C5, D1-D3. For D2/D3, when MCP isn't registered / no `setup_meta.json`, emit `WARN` with detail "not configured" (NOT FAIL — that feature ships in Fase 3/4).
  - `def render_human(checks) -> str` matching the §1.3/§1.4 layout (banded, aligned, footer with counts + exit code).
  - `def render_json(checks) -> dict` → `{"version": ..., "checks": [...], "summary": {"pass":n,"warn":n,"fail":n}, "exit": code}`.
  - `def exit_code(checks) -> int` → 1 if any FAIL else 0 (doctor-internal errors → caller catches → 2).
  - Reuse `setup_wizard.detect()`, `config.global_config_path()`, `config.load`, `config.resolve_model`, and the new `init_claude_code.is_wired()`. Do NOT reimplement detection.
- `tests/test_doctor.py` — (a) all-green case in a fully-wired tmp `$HOME` (monkeypatch `Path.home`); (b) broken case (no global config, no hooks) asserts the right checks FAIL and `exit_code == 1`; (c) `render_json` keys stable; (d) `--check` on mcp_server exits 0.

### Constraints
- Read-only. `run_checks` MUST NOT write, mkdir, or mutate settings. (mkdir-for-seed-pointer is a Fase 5 `--fix` action; here C5 only *reports* writability.)
- No LLM, no network except `claude mcp list` subprocess (3s timeout, fail-open → WARN "could not query").
- All subprocess calls wrapped like `setup_wizard._try_version` (timeout, catch `FileNotFoundError`/`TimeoutExpired`).
- Exit codes exactly: 0 (PASS/WARN only), 1 (≥1 FAIL), 2 (uncaught doctor error — wrap `cmd_doctor` body in try/except).

### Verify
```bash
cd /Users/roberto/antigravity/burnless
python -m pytest tests/test_doctor.py -q
python -m burnless.cli doctor --json | python -c "import sys,json;d=json.load(sys.stdin);assert 'summary' in d and 'checks' in d;print('json-ok',d['summary'])"
python -m burnless.mcp_server --check
python -m pytest tests/test_init_claude_code.py -q   # refactor must not regress existing wiring tests
echo "exit-check:"; python -m burnless.cli doctor >/dev/null 2>&1; echo "doctor exit=$?"
```

### Done when
All Verify commands pass; `burnless doctor` renders banded PASS/WARN/FAIL with correct exit code; `is_wired()` shared by doctor + wirer; existing `init --claude-code` tests stay green.
