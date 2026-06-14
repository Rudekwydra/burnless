# Collapse 3-level compression → single fixed faithful behavior

**Date:** 2026-06-14
**Author:** d691 (design only — NOT implemented)
**Decision (Roberto, 2026-06-14):** The 3 capsule compression levels (`light`/`balanced`/`extreme`)
were a debug aid (inspect each layer independently). Debugging now happens via `burnless log`/raw
reads, so the levels are obsolete. Collapse to ONE fixed behavior = the former **`light`** params
(`per_field=150`, `list_items=12`: preserve everything, dedupe only). Drop the level *name* — there
is no level concept anymore. Remove the user-facing `--mode` knob, the `compression.mode` config key,
and tier-modulation-by-compression. Keep the compression **mechanism** (per_field truncate / dedupe)
fixed at the faithful values.

**Boundary doctrine (unchanged):** this is OUTPUT-capsule compression (safe to compress). The worker
INSTRUCTION path / boundary is NOT touched.

---

## Scope boundary — what stays out

- **`compress_transcript()` (`compression.py:407`) is a SEPARATE function**, not a capsule level. Its
  `mode` is a *run-the-LLM-or-not* axis (`light` = skip the semantic LLM layer and return minified;
  `balanced`/`extreme` = run the LLM encoder — see `compression.py:453`). It is **orphaned** (no `src`
  caller; `_design/REWRITE_CONCEPT_2026-06-09.md:82` flags it orphaned; only `tests/test_encoder_provider.py`
  exercises it). We do NOT collapse its `mode` axis — we only sever its dependency on the deleted
  `MODES`/`normalize_mode` (strip the validation at `compression.py:427-429`). Keeping its `mode`
  param + `stats["mode"]` leaves all 5 encoder-provider tests green with zero edits.
- **`friendly` / `voice_match` / `local_codec` / `local_codec_model`** under `compression:` stay. Only
  `mode` is removed.
- **`_design/*`, `bench/*`, `CHANGELOG.md`** are historical records — NOT edited.

---

## 1. Per-file change list (ordered, file:line — old → new intent)

### A. `src/burnless/compression.py` (the mechanism — fix at faithful values)

| line(s) | old | new intent |
|---|---|---|
| `8-12` (module docstring) | "Three modes: light / balanced / extreme …" | Replace with one line: capsule compression is fixed & faithful (~150 chars/field, ≤12 list items, dedupe only). |
| `44` | `MODES = ("light", "balanced", "extreme")` | **DELETE.** |
| `45` | `DEFAULT_MODE = "balanced"` | **DELETE.** |
| `46` | `MODE_ALIASES = {"safe": "light", "aggressive": "extreme"}` | **DELETE.** |
| `49-51` | `def normalize_mode(mode)` | **DELETE.** (only callers: `compress` 119, `_normalize_files` 241, `compress_transcript` 427, `routing.modulate_by_compression` 68, `cli` 755, `config` 251, `runner` 885 — all removed/edited below). |
| `54-58` | `_FIELD_LIMITS = {"light":{150,12},"balanced":{80,8},"extreme":{40,5}}` | Replace with two module constants: `_PER_FIELD = 150` and `_LIST_ITEMS = 12` (the former `light` values). |
| `87` | `mode: str = DEFAULT_MODE` (Capsule field) | Change to `mode: str = "faithful"` — keep the field so capsule JSON schema shape is stable (nothing READS `capsule["mode"]`; grep shows only writes at `to_dict` 94). Cosmetic marker, no concept. |
| `108-115` | `def compress(*, delegation_id, goal, summary, raw_log, mode=DEFAULT_MODE)` | **Remove the `mode` param.** Only 2 callers (`cli.py:775`, `runner.py:894`), both edited below. Signature becomes `def compress(*, delegation_id, goal, summary, raw_log)`. |
| `116` | docstring "...under the given mode." | "Build a faithful capsule from goal + summary + raw_log." |
| `119-121` | `mode = normalize_mode(mode)` + `if mode not in MODES: raise ...` | **DELETE** (no mode, no validation). |
| `128-130` | `limits=_FIELD_LIMITS[mode]; per_field=limits["per_field"]; max_items=limits["list_items"]` | `per_field = _PER_FIELD` / `max_items = _LIST_ITEMS`. |
| `135` | `_normalize_files(..., mode=mode)` | `_normalize_files(...)` (param dropped — see below). |
| `160-164` | `if mode == "extreme": objective=_slugify_phrase(...); next_step=...; decisions=...; risks=...` | **DELETE the whole block** — slugify was the `extreme`-only path, dead under faithful. (`_slugify_phrase` 394-404 + `_SLUG_KEEP` 392 become unused; leave as harmless dead code OR delete — mark optional.) |
| `176` | `mode=mode,` (in `Capsule(...)` ctor call) | `mode="faithful",` or omit (use the field default). |
| `240-250` `_normalize_files` | `def _normalize_files(items, *, mode)` + `mode=normalize_mode(mode)` (241) + `if mode in ("balanced","extreme"): s=Path(s).name or s` (247-248) | **Remove `mode` param.** Drop the basename branch entirely → always preserve the FULL path, dedupe only (former `light` behavior). New body: iterate, skip falsy, `out.append(str(f))`, `return _dedupe(out)`. Only caller is `compress():135`, edited above. |
| `427-429` (`compress_transcript`) | `mode = normalize_mode(mode)` + `if mode not in MODES: raise ...` | **DELETE these 3 lines only.** Keep the `mode` param (410), the `if mode == "light"` branch (453), and `stats["mode"]` (461,524). Severs the dep on deleted `MODES`/`normalize_mode`; preserves the separate run-LLM axis + green tests. |

### B. `src/burnless/routing.py` (tier-modulation-by-compression — gone)

| line(s) | old | new intent |
|---|---|---|
| `53-57` | comment "Compression dial → tier modulation." + `_DEMOTE_ONE` (56) + `_PROMOTE_ONE` (57) | **DELETE** — both dicts are used ONLY by `modulate_by_compression`. |
| `60-81` | `def modulate_by_compression(tier, matched_kw, compression_mode)` | **DELETE the whole function.** Sole caller is `cli.py:294` (removed below). No test references it (grep `tests/*.py`: zero hits). |

### C. `src/burnless/cli.py` (user-facing surface removal)

| line(s) | old | new intent |
|---|---|---|
| `293-294` | `comp_mode = cfg.get("compression",{}).get("mode","balanced")` + `tier, modulation_reason = routing_mod.modulate_by_compression(tier, kw, comp_mode)` | **DELETE both lines.** Replace with `modulation_reason = ""` so the downstream reference stays defined (the `tier_override` branch at 288-290 already sets `modulation_reason=""`; mirror it here). `tier, kw` come straight from `routing_mod.route(...)` at 292. |
| `748-753` (`cmd_capsule`) | `if args.mode is None: …print raw capsule…; return 0` | Becomes the ONLY path — `cmd_capsule` always prints the raw capsule (de-indent the body, drop the `if args.mode is None` guard). Regen-by-mode is gone; raw debugging is `burnless log`/`read` per the decision. |
| `755-791` | `args.mode=normalize_mode(...)` + MODES validation + regen branch (`compress(... mode=args.mode)` 775-781 + print 785-789) | **DELETE the entire regen block** (the `--mode` feature for `capsule`). |
| `1107-1118` | `do --mode` temp-override block (`_mode_override = getattr(args,"mode_override",None)` … `cfg.setdefault("compression",{})["mode"]=_mode_override; config_mod.save(...)`) | **DELETE the whole block.** NOTE the ripple: `_config_patched` / `_orig_config_text` are also touched by the worker-override block at 1122-1124 (`if _orig_config_text is None`). Initialize `_orig_config_text = None` and `_config_patched = False` *before* the worker-override block (keep those two inits, drop only the mode-override logic) so the existing `finally` restore still works. |
| `1496-1501` | `capsule` parser: `sp.add_argument("--mode", choices=list(compression_mod.MODES), default=None, help="...light\|balanced\|extreme")` | **DELETE the `--mode` argument** from the `capsule` subparser. |
| `1588-1594` | `do` parser: `sp.add_argument("--mode", choices=["balanced","extreme","light"], default=None, dest="mode_override", help="...")` | **DELETE the `--mode` argument** from the `do` subparser. |

### D. `src/burnless/exec/runner.py` (worker OUTPUT-capsule build — fixed)

| line(s) | old | new intent |
|---|---|---|
| `884-891` | `raw_mode=cfg.get("compression",{}).get("mode",DEFAULT_MODE)` + `mode=normalize_mode(raw_mode)` + `if mode not in MODES: print(...); mode=DEFAULT_MODE` | **DELETE all 8 lines** (no config read, no validation, no fallback branch). |
| `894-900` | `compress(delegation_id=did, goal=goal, summary=summary, raw_log=raw_log_text, mode=mode)` | Drop `mode=mode` → `compress(delegation_id=did, goal=goal, summary=summary, raw_log=raw_log_text)`. |
| `913` | f-string `f"capsule mode={mode}: raw …"` | Drop `mode=…` from the log string (or hardcode `mode=faithful`). |
| `917` | `extra={"mode": mode, "ratio": …}` | `extra={"ratio": …}` (drop `mode`) or `{"mode":"faithful", …}`. |
| `929` | `state["last_capsule_mode"] = mode` | Drop the line, or set `= "faithful"`. Grep shows this is the only write and nothing reads `last_capsule_mode` → safe to drop; keep as `"faithful"` if you want state schema stability. |

> `runner.py:426` (`legacy_mode = opts.mode`) and `live_runner.py:928` (`mode = kwargs["mode"]`) are
> EXECUTION-mode (run path), unrelated to compression — **do not touch.**

### E. `src/burnless/config.py` (default + normalize-on-load)

| line(s) | old | new intent |
|---|---|---|
| `99-104` (`DEFAULT_CONFIG["compression"]`) | includes `"mode": "balanced", # canonical: light\|balanced\|extreme …` (100) | **DELETE line 100.** Keep `friendly`, `voice_match`, `local_codec`, `local_codec_model`. |
| `249-251` | `from . import compression as _comp` + `comp = data.setdefault("compression",{})` + `comp["mode"]=_comp.normalize_mode(comp.get("mode","balanced"))` | **DELETE** the `_comp` import (249) and the `comp["mode"]=…normalize_mode(…)` line (251). Keep `comp = data.setdefault("compression", {})` (250) — still needed for the friendly default below. |
| `252-253` | `if "friendly" not in user_comp: comp["friendly"] = comp["mode"] != "extreme"` | Replace the mode-derived default with a constant: `if "friendly" not in user_comp: comp["friendly"] = True` (former non-`extreme` default). |

### F. `.burnless/config.yaml`

| line(s) | old | new intent |
|---|---|---|
| `114-119` | `compression:` block with `mode: balanced` (115) | **DELETE line 115 (`mode: balanced`).** Keep `friendly`, `voice_match`, `local_codec`, `local_codec_model`. |

### G. Docs (remove level tables + `--mode`; one fixed-faithful line; keep layer *architecture* narrative)

| file:line | old | new intent |
|---|---|---|
| `README.md:200` | `mode: balanced   # light \| balanced \| extreme` | DELETE the config line. |
| `README.md:205-207` | 3-row light/balanced/extreme table | DELETE table; one line: "Capsule compression is fixed and faithful — preserve everything (~150 chars/field, ≤12 list items), dedupe only. No mode knob." |
| `README.md:213` | "Per-invocation override: `burnless --mode light …`" | DELETE. |
| `README.md:220` | table cell "...balanced + extreme" | Drop the mode reference (keep the L2-encoder row as architecture). |
| `MATH.md:328-340` | "The three compression modes…" + 3-row table + per-mode prose | Replace the *mode-selection* framing with: capsule compression is fixed/faithful. KEEP the L1/L2/L3 *layered-architecture* narrative (the cost × fidelity plane) but stop tying it to a user-chosen mode. |
| `MATH.md:347` | `mode: light  # light \| balanced \| extreme` | DELETE config line. |
| `MATH.md:350` | "Or per-invocation: `burnless --mode light …`" | DELETE. |
| `llms.txt:88-90` | 3 bullets light/balanced/extreme | Replace with one bullet: capsule compression fixed & faithful, dedupe-only, no `--mode`. |
| `docs/COMMANDS.md:28` | `--mode {balanced,extreme,light}` knob doc | DELETE the flag entry. |
| `docs/COMMANDS.md:49-51` | 3 bullets light/balanced/extreme | Replace with one line: fixed faithful capsule compression, no mode. |
| `docs/USING_BURNLESS_FROM_YOUR_LLM.md:84` | "Set it per run with `--mode <light\|balanced\|extreme>`…" | Replace: capsule compression is fixed/faithful, no per-run knob. |
| `docs/USING_BURNLESS_FROM_YOUR_LLM.md:88-92` | 3-row table + "When to use `--mode light`" | DELETE the table + the `--mode light` guidance paragraph. |
| `docs/DOCTRINE.md:68-69` | "`--mode {light\|balanced\|extreme}` controls output compression…" | Replace with one line: capsule compression is fixed/faithful; debug raw via `burnless log`. |

> `bench/COMPRESSION_FINDINGS.md`, `bench/VS_SONNET_SOLO.md`, `CHANGELOG.md:65`, and every `_design/*`
> hit (`TARGET_ARCHITECTURE`, `v04_landing_briefing`, `hd_hygiene_2026-06-09/*`, `layer_audit`) are
> historical/empirical records — **leave untouched.**

---

## 2. Ripple analysis — callers of `compress()` / encoder funcs

### `compress()` (`compression.py:108`) — `mode` param REMOVED
Exhaustive caller set (grep `\.compress\(` over `src` + `tests`):
1. `cli.py:775` — inside the `cmd_capsule` regen block that is being **deleted** wholesale → no ripple.
2. `runner.py:894` — edited to drop `mode=mode` → handled.
3. **Tests:** zero. No test calls `compression.compress(...)` or imports `MODES`/`_FIELD_LIMITS`
   (grep `tests/*.py`). → capsule `compress()` has no test ripple.

### `_normalize_files()` (`compression.py:240`) — `mode` param REMOVED
- Sole caller: `compress():135` (edited). No external/test caller. → safe full removal.

### `compress_transcript()` (`compression.py:407`) — `mode` param KEPT
- `src` callers: **none** (orphaned). 
- Test callers: `tests/test_encoder_provider.py:64,78,95,116,134` all pass `mode="balanced"` and assert
  `stats["mode"]=="balanced"` (lines 68,123). Because we keep the param + the branch + `stats["mode"]`
  and only delete the `MODES` validation (427-429), **all 5 tests stay green with zero edits.**

### `normalize_mode()` (`compression.py:49`) — REMOVED
Callers and their fate: `compress` 119 (deleted), `_normalize_files` 241 (deleted),
`compress_transcript` 427 (deleted), `routing.modulate_by_compression` 68 (function deleted),
`cli` 755 (block deleted), `config` 251 (deleted), `runner` 885 (deleted). → zero dangling refs.

### `modulate_by_compression()` (`routing.py:60`) — REMOVED
- Sole caller: `cli.py:294` (deleted). No test references it. → zero dangling refs.

### `MODES` / `DEFAULT_MODE` / `MODE_ALIASES` — REMOVED
Reference sites (grep `src`): `cli.py:756,758,1113,1498`, `runner.py:884,886,888,891`, `config.py:251`,
`routing.py:68`, `compression.py` self. Every one is in a block being deleted or edited above. No
`tests/*.py` import of `MODES`/`DEFAULT_MODE` (verified). → clean removal.

### `Capsule.mode` field (`compression.py:87`, serialized at 94) — KEPT (value fixed)
- No reader of `capsule["mode"]` anywhere in `src` (grep shows only the `to_dict` write). Keeping the
  field at a fixed `"faithful"` value preserves capsule JSON shape for any external/older reader; it is
  cosmetic. (Dropping it is also safe but changes the on-disk schema — keep it to be conservative.)

---

## COLLAPSE PLAN

Deterministic order (compression-core first so deletions of `MODES`/`normalize_mode` land before their
references are removed — but since this is design-only, the order is the recommended implementation
sequence):

1. **`compression.py`** — replace `_FIELD_LIMITS` with `_PER_FIELD=150`/`_LIST_ITEMS=12`; delete
   `MODES`/`DEFAULT_MODE`/`MODE_ALIASES`/`normalize_mode`; strip `mode` from `compress()` (params,
   normalize/validate, `_FIELD_LIMITS[mode]`, the `extreme` slugify block, the `Capsule(mode=…)` arg →
   `"faithful"`); strip `mode` from `_normalize_files()` (always full-path + dedupe); delete only the
   `MODES` validation in `compress_transcript()` (427-429), keep its `mode` param + branch; update the
   module docstring + `Capsule.mode` default.
2. **`routing.py`** — delete `_DEMOTE_ONE`/`_PROMOTE_ONE` + the modulation comment + `modulate_by_compression()`.
3. **`config.py`** — drop `"mode"` from `DEFAULT_CONFIG["compression"]`; delete the `_comp` import +
   `comp["mode"]=normalize_mode(...)`; set `comp["friendly"]=True` default (keep the `user_comp` guard).
4. **`exec/runner.py`** — delete the `raw_mode`/`normalize`/validation block (884-891); drop `mode=mode`
   from the `compress(...)` call; drop/neutralize `mode` in the log string (913), `extra` (917), and
   `last_capsule_mode` (929).
5. **`cli.py`** — remove `comp_mode`+`modulate_by_compression` call (set `modulation_reason=""`);
   simplify `cmd_capsule` to always print raw (delete the regen-by-mode block); delete the `do --mode`
   override block (preserve the `_orig_config_text=None`/`_config_patched=False` inits for the
   worker-override path); delete both `--mode` argparse args (capsule + do).
6. **`.burnless/config.yaml`** — delete `mode: balanced` under `compression:`.
7. **Docs** — README, MATH, llms.txt, COMMANDS, USING_BURNLESS_FROM_YOUR_LLM, DOCTRINE: remove level
   tables + `--mode` mentions; add the single fixed-faithful line; keep the layered-architecture
   narrative in MATH.
8. **Tests** — see TEST IMPACT (only `test_stale.py:288` fixture is cosmetic; encoder-provider tests
   stay green).

## Verify

```sh
test -f /Users/roberto/antigravity/burnless/_design/collapse_compression_2026_06_14.md || exit 1
grep -q "COLLAPSE PLAN" /Users/roberto/antigravity/burnless/_design/collapse_compression_2026_06_14.md || exit 1
grep -q "TEST IMPACT" /Users/roberto/antigravity/burnless/_design/collapse_compression_2026_06_14.md || exit 1
grep -qi "modulate_by_compression" /Users/roberto/antigravity/burnless/_design/collapse_compression_2026_06_14.md || exit 1
test -z "$(cd /Users/roberto/antigravity/burnless && python3 -c 'import burnless.cli' 2>&1 | grep -i error)" || exit 1
cd /Users/roberto/antigravity/burnless && python3 -c "import burnless.cli, burnless.exec.runner, burnless.compression, burnless.routing" || exit 1
cd /Users/roberto/antigravity/burnless && python3 -m pytest tests/ --collect-only -q >/dev/null || exit 1
cd /Users/roberto/antigravity/burnless && ! python3 -m burnless.cli do --help 2>&1 | grep -q -- "--mode" || exit 1
cd /Users/roberto/antigravity/burnless && ! python3 -m burnless.cli capsule --help 2>&1 | grep -q -- "--mode" || exit 1
! grep -q "modulate_by_compression" /Users/roberto/antigravity/burnless/src/burnless/routing.py || exit 1
! grep -q "modulate_by_compression" /Users/roberto/antigravity/burnless/src/burnless/cli.py || exit 1
! grep -qE "^\s*mode:\s*(light|balanced|extreme)" /Users/roberto/antigravity/burnless/.burnless/config.yaml || exit 1
! grep -qE "MODES\s*=\s*\(.*light.*balanced.*extreme.*\)" /Users/roberto/antigravity/burnless/src/burnless/compression.py || exit 1
grep -q "_PER_FIELD" /Users/roberto/antigravity/burnless/src/burnless/compression.py || exit 1
```

> Verify intent: doc exists + required headers; `burnless` imports cleanly; pytest still collects; no
> `--mode` in `do`/`capsule` help; `modulate_by_compression` gone from routing + cli; no
> `compression.mode` level in config.yaml; the 3-entry `MODES` tuple is gone and the fixed
> `_PER_FIELD` constant is present.

---

## TEST IMPACT

Grounded in grep over `tests/*.py`:

1. **`tests/test_encoder_provider.py:64,68,78,95,116,123,134`** — call `compress_transcript(mode="balanced")`
   and assert `stats["mode"]=="balanced"`. **NO CHANGE REQUIRED.** We intentionally preserve
   `compress_transcript`'s `mode` param, its `if mode=="light"` branch, and `stats["mode"]`; only the
   `MODES` validation (427-429) is removed. These 5 tests stay green. (If a future cleanup also collapses
   `compress_transcript`, revisit them — out of scope here.)
2. **`tests/test_stale.py:288`** — config fixture string `"compression:\n  mode: balanced\n"`. **Cosmetic
   update recommended** (drop the `mode: balanced` line) but NOT required for green: after removal,
   `config.load` no longer normalizes/reads `mode`, and an unknown `mode` key merges harmlessly. The test
   asserts stale behavior, not compression mode. Leave or trim.
3. **`modulate_by_compression`** — **no test asserts it** (zero hits in `tests/*.py`). Removing it breaks
   no test.
4. **`compress()` capsule builder / `MODES` / `_FIELD_LIMITS` / `capsule --mode`** — **no direct test
   coverage** (zero hits). Removal is test-safe at the unit level; `pytest --collect-only` guards against
   import breakage.
5. **No new tests authored** (design-only). If desired post-implementation: a unit test asserting
   `compress(...)` always yields `per_field≈150 / ≤12 items` and full (non-basenamed) paths would lock
   the faithful contract — recommended but not part of this collapse.
