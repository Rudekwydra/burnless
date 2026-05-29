# Footer Tier/Model Visibility (real-time) — d504 gold design

Date: 2026-05-29
Files in scope:
- `/Users/roberto/antigravity/burnless/src/burnless/pty_shell.py` (footer renderer)
- `/Users/roberto/antigravity/burnless/src/burnless/usage_meter.py` (helper)
- `/Users/roberto/antigravity/burnless/src/burnless/config.py` (tier→model SSO)
- `/Users/roberto/antigravity/burnless/src/burnless/liveness.py` (run-state source)

---

## 1. Architecture / decision + trade-offs

### Problem
The PTY footer (`_draw_status` in `pty_shell.py`) shows static maestro `bin_name`
(+ splash `model`) + tokens/delegations/cache/quota. When a delegation runs it does
**not** show which **tier** (bronze/silver/gold/diamond) or **underlying model**
(Haiku/Sonnet/Opus/Codex) is executing. The footer process is a **separate process**
from the worker (`burnless do` runs the worker in another subprocess/terminal), so it
can only learn run state via shared filesystem under `.burnless/`.

### Where run state already lives (ground truth)
- `cli.py:830-843` writes a per-run snapshot **before** handing to the worker:
  `.burnless/runs/<did>.plan.json` = `{id, tier, agent, provider, started_at, delegation}`.
  This is the authoritative "what tier/agent is running" record.
- `liveness.py:init_run_dir` truncates `.burnless/runs/<did>/liveness.jsonl` at start;
  `live_runner.py:723` emits a `finish` event at run end (subprocess backend).
- `state.json` `active_tier` is **sticky routing config (None=auto)**, NOT live state —
  do NOT use it for "currently executing" (confirmed `state.py:13`).

### Decision: poll the filesystem (NOT events), reuse the existing poll loop
The footer already polls 5×/s in `_status_updater` and TTL-caches expensive hints
(usage 2s, quota 15s). Active-run detection is the same shape: a cheap `glob`+`stat`+
small JSON read, TTL-cached ~1s. An event/IPC bus is rejected — the footer and worker
are distinct processes with no channel; filesystem polling is already the established
pattern and is crash-safe (no stale subscriptions).

### Active-run detection (robust across backends)
Backends differ: only the subprocess backend emits the liveness `finish` event;
`cached_worker`/`maestro` may not. So detect "live" by **freshness**, not solely by
absence of `finish`:

1. Glob `.burnless/runs/*.plan.json`; pick the one with the newest `started_at`
   (tie-break: newest file mtime).
2. Consider it **active** when EITHER:
   - its `runs/<did>/liveness.jsonl` exists, its last event is **not** `finish`,
     and the file mtime is within `stale_s` (default 8s); OR
   - no liveness file exists yet but the `plan.json` mtime is within `stale_s`
     (covers cached_worker/maestro + the gap before first liveness write).
3. Otherwise → no active run → footer falls back to current static/quota hint.

`stale_s=8s` chosen: > the 5×/s footer tick and > liveness 0.5s cadence, < human
"is it stuck?" patience. A finished/crashed run drops off the footer within ~8s.

### tier → model single source (Phase 5 model-sso)
`config.resolve_model(tier, cfg)` is the single source: reads
`cfg["agents"][tier].command`/`name`, else falls back to `DEFAULT_TIER_MODELS`
(`gold→claude-opus-4-8`, `silver→claude-sonnet-4-6`, `bronze→claude-haiku-4-5-*`).
Add a thin `config.tier_model_label(tier, cfg)` that calls `resolve_model` then maps
the resolved model-id to a short human label (Opus/Sonnet/Haiku/Codex/Ollama/raw id).
No new model table — labels are derived from the resolved id, keeping SSO intact.
(`diamond` is not in `DEFAULT_TIER_MODELS`; `resolve_model` falls back to silver's
default unless cfg defines a `diamond` agent — label still derives correctly from
whatever id resolves, so no special-casing needed.)

### Trade-offs
- Poll vs event: poll wins on simplicity + crash-safety; cost ~1 glob+stat+small read
  per second (negligible vs existing usage scan over JSONL).
- Freshness vs finish-marker: freshness covers all backends and crash cases; the only
  downside is a live badge lingers ≤`stale_s` after a silent kill — acceptable.
- Label derivation vs explicit map: deriving from resolved id avoids a second source of
  truth that could drift from config (the exact bug Phase 5 model-sso fixed).

---

## 2. Implementation plan (ordered, files + functions, absolute paths)

1. **`/Users/roberto/antigravity/burnless/src/burnless/liveness.py`** — add
   `def active_run(burnless_root: Path, *, stale_s: float = 8.0) -> dict | None`.
   - Glob `burnless_root/"runs"/"*.plan.json"`; parse each, keep max by `started_at`
     (fallback file mtime). Return `None` on empty/all-stale.
   - For the candidate `did`, resolve liveness via existing `liveness_path()`; apply the
     freshness rule above. Return the parsed plan dict (`{id,tier,agent,provider,...}`)
     when active, else `None`. Best-effort: swallow all IO/JSON errors → `None`.

2. **`/Users/roberto/antigravity/burnless/src/burnless/config.py`** — add
   `def tier_model_label(tier: str, cfg: dict | None = None) -> str`.
   - `mid = resolve_model(tier, cfg)`; lowercase-substring map:
     `opus→Opus`, `sonnet→Sonnet`, `haiku→Haiku`, `codex|gpt→Codex`,
     `ollama|mistral|llama→Ollama`; else first token of `mid`. Never raises.

3. **`/Users/roberto/antigravity/burnless/src/burnless/pty_shell.py`** — in
   `_run_pty`, thread `cfg` + `burnless_root` into `_draw_status` (closure already has
   `metrics_path`; pass `cfg` via a captured var from `main`). Add to `_session`:
   `last_run_ts`, `last_run_hint` (TTL 1.0s, new `_RUN_TTL = 1.0`).
   - In `_draw_status`, before composing `active_hint`: every `_RUN_TTL`s call
     `liveness.active_run(burnless_root)`; if a dict returned, build
     `run_hint = f"▶ {tier}·{config.tier_model_label(tier, cfg)}"` (e.g. `▶ gold·Opus`),
     else `""`. Cache in `_session["last_run_hint"]`.
   - Priority: when `last_run_hint` is non-empty it is shown **first** (most salient),
     prepended ahead of quota/usage/pro-tip: `active_hint = run_hint + " · " + <rest>`.
     When no run is live, behavior is byte-for-byte unchanged.

4. **`main()`** (`pty_shell.py`) — pass `cfg` and `burnless_root = root` (the `.burnless`
   dir, already `paths_mod.find_root()`) into `_run_pty(...)` (new params, default
   `None` so non-root / test paths stay safe).

5. Tests: add `tests/test_footer_tier_visibility.py` — unit-test `active_run` (fresh vs
   stale vs finished vs missing) + `tier_model_label` (all tiers + codex cfg override).

No edits to `cli.py`/`live_runner.py` needed — they already produce plan.json + liveness.

---

## 3. Bronze-ready spec (copy into `burnless do --tier bronze`)

```
TASK: Add real-time worker tier/model badge to the burnless PTY footer.

CONTEXT (read first, do not modify behavior elsewhere):
- Run snapshot per delegation: .burnless/runs/<did>.plan.json = {id,tier,agent,provider,started_at,delegation} (written by cli.py).
- Liveness stream: .burnless/runs/<did>/liveness.jsonl; last event "finish" => done. Helper: liveness.liveness_path(root, did).
- tier->model single source: config.resolve_model(tier, cfg). DEFAULT_TIER_MODELS = gold:opus, silver:sonnet, bronze:haiku.
- Footer renderer: pty_shell.py _draw_status (inside _run_pty); polls via _status_updater; TTL-caches hints in _session.

EDIT FILES (exactly these 3, nothing else):

1. /Users/roberto/antigravity/burnless/src/burnless/liveness.py
   ADD function (append after tail_events):
     def active_run(burnless_root: Path, *, stale_s: float = 8.0) -> dict | None
   Behavior:
     - runs_dir = burnless_root / "runs"; if not dir -> return None.
     - plans = list(runs_dir.glob("*.plan.json")); if empty -> None.
     - For each, json-load; keep the one with max started_at string (fallback: file st_mtime). Wrap each load in try/except -> skip bad files.
     - did = plan["id"]. lv = liveness_path(burnless_root, did).
     - active if:
         (lv.exists() and (time.time() - lv.stat().st_mtime) <= stale_s and last non-empty json line's "event" != "finish")
         OR
         (not lv.exists() and (time.time() - <plan file>.stat().st_mtime) <= stale_s)
     - return plan dict if active else None. Swallow ALL exceptions -> return None.

2. /Users/roberto/antigravity/burnless/src/burnless/config.py
   ADD function (after resolve_model):
     def tier_model_label(tier: str, cfg: dict | None = None) -> str
   Behavior:
     - mid = (resolve_model(tier, cfg) or "").lower()
     - if "opus" in mid: "Opus"; elif "sonnet" in mid: "Sonnet"; elif "haiku" in mid: "Haiku";
       elif "codex" in mid or "gpt" in mid: "Codex"; elif any(k in mid for k in ("ollama","mistral","llama")): "Ollama";
       else: (mid.split("-")[0] or tier).capitalize(). Never raises.

3. /Users/roberto/antigravity/burnless/src/burnless/pty_shell.py
   a. import: add `from . import liveness as liveness_mod` near the other `from . import` lines.
   b. _run_pty signature: add params `cfg: dict | None = None, burnless_root: Path | None = None` (keep existing params/order; new ones last).
   c. In _session dict add: "last_run_ts": 0.0, "last_run_hint": "". Add constant `_RUN_TTL = 1.0` near _USAGE_TTL.
   d. In _draw_status, BEFORE the final `active_hint = ...` quota/usage block resolves into `line`:
        if burnless_root is not None:
            now3 = time.time()
            if now3 - float(_session.get("last_run_ts", 0.0) or 0.0) >= _RUN_TTL:
                try:
                    pr = liveness_mod.active_run(burnless_root)
                    if pr:
                        t = pr.get("tier", "?")
                        _session["last_run_hint"] = f"▶ {t}·{config_mod.tier_model_label(t, cfg)}"
                    else:
                        _session["last_run_hint"] = ""
                except Exception:
                    _session["last_run_hint"] = ""
                _session["last_run_ts"] = now3
      Then AFTER active_hint is finalized by the quota/usage logic, prepend run badge if present:
        if _session.get("last_run_hint"):
            active_hint = f"{_session['last_run_hint']} · {active_hint}" if active_hint else _session["last_run_hint"]
   e. In main(), update the final call to: return _run_pty(full_argv, metrics_path, bin_name, hint, cfg=cfg, burnless_root=root)
      (root is the .burnless dir from paths_mod.find_root(); already in scope as `root`).

HARD PROHIBITIONS:
- DO NOT edit cli.py, live_runner.py, state.py, usage_meter.py, or any file outside the 3 listed.
- DO NOT read or use state.json "active_tier" (it is sticky routing config, NOT live run state).
- DO NOT change footer output when NO run is active (no-run path must be byte-identical to before).
- DO NOT add new model tables/dicts mapping tier->model. tier_model_label MUST derive from config.resolve_model only.
- DO NOT introduce threads, events, watchers, or IPC. Filesystem polling inside the existing _status_updater loop ONLY.
- DO NOT change _RUN_TTL below 0.5s. DO NOT block in _draw_status (no sleeps, no network).
- DO NOT use Date.now-style nondeterminism beyond time.time()/stat already used.

DoD:
- liveness.active_run + config.tier_model_label exist with the signatures above.
- Footer shows `▶ <tier>·<Model>` first in the hint when a fresh run exists; nothing when none.
- Existing tests still pass.
```

## Verify

```bash
cd /Users/roberto/antigravity/burnless

# 1. New functions exist with correct signatures
python -c "import inspect, burnless.liveness as L; s=str(inspect.signature(L.active_run)); print('active_run', s); assert 'burnless_root' in s and 'stale_s' in s"
python -c "import inspect, burnless.config as C; s=str(inspect.signature(C.tier_model_label)); print('tier_model_label', s); assert 'tier' in s and 'cfg' in s"

# 2. tier_model_label derives from resolve_model (SSO), all default tiers
python -c "import burnless.config as C; m={t:C.tier_model_label(t) for t in ('gold','silver','bronze')}; print(m); assert m=={'gold':'Opus','silver':'Sonnet','bronze':'Haiku'}, m"

# 3. active_run: returns None on empty/missing runs dir; dict when fresh plan exists
python - <<'PY'
import json, tempfile, time, pathlib, burnless.liveness as L
d=pathlib.Path(tempfile.mkdtemp())
assert L.active_run(d) is None, "empty -> None"
runs=d/"runs"; runs.mkdir()
(runs/"d999.plan.json").write_text(json.dumps({"id":"d999","tier":"gold","agent":"claude-opus"}))
pr=L.active_run(d); assert pr and pr["tier"]=="gold", pr   # fresh, no liveness yet -> active
# finished run -> None
(runs/"d999").mkdir()
(runs/"d999"/"liveness.jsonl").write_text(json.dumps({"event":"finish","did":"d999"})+"\n")
assert L.active_run(d) is None, "finished -> None"
print("active_run OK")
PY

# 4. pty_shell wires cfg+burnless_root and run badge, no edits outside 3 files
python -c "import ast,inspect,burnless.pty_shell as P; src=inspect.getsource(P._run_pty); assert 'burnless_root' in src and 'last_run_hint' in src and 'active_run' in src, 'footer wiring missing'"
grep -q "tier_model_label" src/burnless/pty_shell.py && echo "label-used OK"

# 5. No forbidden coupling introduced
! grep -q "active_tier" src/burnless/pty_shell.py && echo "no active_tier coupling OK"

# 6. Module imports clean + existing suite green
python -c "import burnless.pty_shell, burnless.liveness, burnless.config; print('imports OK')"
python -m pytest -q 2>&1 | tail -5
```
