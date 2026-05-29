# Model Identity — Single Source of Truth

**Date:** 2026-05-29
**Author:** gold (d494, READ-ONLY design)
**Status:** design — ready to delegate to silver (AFTER d493 lands)
**Directive (Roberto):** model identity must live in ONE place. config decides who is gold / gold-fallback / silver / bronze, per tier, per provider. Every other site RESOLVES via a function/constant — never hardcodes. Bump current opus to `claude-opus-4-8`.

---

## 0. Two bugs this also fixes

- **`[no-warm]` (warm-pool miss).** config command is `claude --model opus` (alias). `agents._extract_model_from_parts` returns the literal `"opus"` (`agents.py:601`,`:554-562`), and the warm pool is keyed by that string (`warm_session.warm_file_path` → `~/.burnless/warm/claude/opus.json`, `warm_session.py:47-57`). But every *seed* / footer / keepalive default uses the **full id** `"claude-sonnet-4-6"` (`warm_session.py:184,:344`; `cli.py:2123`). Alias-keyed pool ≠ full-id-keyed pool → lookup misses → worker spawns COLD → footer prints `[no-warm]`. Root cause = **no model-name normalization** between command-build and warm lookup.
- **Footer shows wrong/missing model.** `pty_shell.py:364` reads `cfg.get("brain", {}).get("model", "")`. There is no `brain` key in `DEFAULT_CONFIG` (keys are `agents` / `maestro` / `encoder`, `config.py:9-58`) → footer model is empty string. Footer, warm key, and cache key must all pull from the SAME resolved value.

Both collapse into one fix: a `normalize_model()` that both the command path and the warm/footer/cache path go through, plus a `resolve_model(tier, cfg)` so defaults stop being copy-pasted strings.

---

## 1. Field-verified current state (file:line)

### 1.1 What already exists
- `config.py:301` `HAIKU_MODEL = "claude-haiku-4-5-20251001"` — lone constant, used only by `_PRESET_RESOLUTIONS`.
- `config.py:303-307` `_PRESET_RESOLUTIONS` — maps preset→{encoder, maestro} model. **L1/L2 layer models only.**
- `config.py:309-329` `resolve_layer_models(cfg)` — resolves encoder/maestro model from preset + explicit override. **Covers L1/L2. Does NOT cover the L3 tier→model (gold/silver/bronze) mapping at all.**
- So there is a resolver, but only for the two compression layers. The tier→worker-model identity is **un-resolved**: every consumer hardcodes its own copy.

### 1.2 config.yaml `agents:` shape (the intended primary source)
`config.py:9-50` DEFAULT_CONFIG.agents:
- `gold.name="opus"`, `gold.command="claude --model opus ..."`
- `silver.name="sonnet"`, `silver.command="claude --model sonnet ..."` (+ optional `silver.providers[]` autobalance, each with own `name`+`command --model X`)
- `bronze.name="haiku"`, `bronze.command="claude --model haiku ..."`
So config already carries tier→model **inside the command string** (as an alias). Nothing reads it back as a model id; consumers re-hardcode instead.

### 1.3 The ~16+ hardcoded sites (grep `claude-opus|claude-sonnet|claude-haiku|gpt-5|gpt-4|opus-4|sonnet-4|haiku-4` over src, minus tests/bak/pycache)

Classified:

**A — tier→model default maps / fallbacks (MUST resolve via `resolve_model`)**
- `cli.py:71-73` `MAESTRO_TIER_MODEL = {gold: claude-opus-4-7, silver: claude-sonnet-4-6, bronze: claude-haiku-4-5-20251001}`
- `cli.py:1493` `model = args.model or state.get("brain_model") or "claude-opus-4-7"`
- `chat_mode.py:375-377` `MAESTRO_TIER_MODEL` dup of cli.py:71 (opus-4-7 again)
- `chat_mode.py:379` fallback `"claude-sonnet-4-6"`
- `maestro_legacy.py:21` `DEFAULT_MAIN_MODEL = "claude-opus-4-7"`
- `maestro/core.py:13` `DEFAULT_BRAIN_MODEL = "claude-sonnet-4-6"`
- `maestro_runner.py:19` `DEFAULT_MODEL = "claude-haiku-4-5-20251001"`
- `maestro_layer.py:116` param default `model="claude-sonnet-4-6"`
- `config.py:301` `HAIKU_MODEL` (keep name; source from new constant)

**B — provider default fallbacks (MUST resolve via `DEFAULT_PROVIDER_MODELS` + `normalize_model`)**
- `agents.py:604` `model = "claude-sonnet-4-6" if provider=="claude" else "gpt-5.2"` (warm key default)
- `agents.py:697` same, in `_run_once` touch path
- `live_runner.py:334` `"claude-sonnet-4-6" if provider=="claude" else "gpt-5.2"`
- `live_runner.py:443` same
- `cli.py:284` `_extract_model` codex fallback `return "gpt-5.2"`
- `warm_daemon.py:94` `return "claude-sonnet-4-6"`; `:103` `return "gpt-5.2"`
- `warm_session.py:184,:344` param default `model="claude-sonnet-4-6"`
- `warm_session_codex.py:34` `DEFAULT_MODEL = "gpt-5.2"`
- `cli.py:2123` warm-init claude default `"claude-sonnet-4-6"`; `cli.py:2136` codex default `"gpt-5.2"`
- `keepalive.py:172` `model = ... or "claude-sonnet-4-6"`; `keepalive.py:140` payload `'model':'claude-haiku-4-5-20251001'`

**C — pricing / usage keys (MUST stay keyed by canonical id; bump opus key 4-7→4-8)**
- `maestro_legacy.py:25-27` `PRICES_USD_PER_MTOK` keys: `claude-opus-4-7`/`claude-sonnet-4-6`/`claude-haiku-4-5-20251001`
- `subscription_usage.py:90` `"model":"claude-haiku-4-5-20251001"`

**D — layer / codec defaults (route through `resolve_layer_models` or new constant)**
- `codec/decoder.py:10` `DEFAULT_DECODER_MODEL="claude-haiku-4-5"`
- `codec/encoder.py:11` `DEFAULT_ENCODER_MODEL="claude-haiku-4-5"`
- `codec/police.py:18` param default `"claude-sonnet-4-6"`
- `compression.py:457` `model="claude-haiku-4-5-20251001"`

**E — config GENERATORS (legitimately emit model strings into config; make them emit canonical-or-aliasable ids; bump opus-4-7)**
- `setup_wizard.py:210/212/214` claude commands hardcode `claude-opus-4-7`/`claude-sonnet-4-6`/`claude-haiku-4-5-20251001`
- `setup_wizard.py:35,239-241` codex model strings
- `provider_autodetect.py:64-185` claude-opus/sonnet/haiku + codex gpt-5.x names & commands
- `profiles.py:16,25,50` profile model strings
- `maestro_adapters.py:10-12` `DEFAULT_ANTHROPIC_MODELS` tuple (opus-4-7/sonnet/haiku) — the `/maestro` switch menu
- `maestro_adapters.py:74,81,112,120` codex/openrouter adapter default_model (`gpt-4o`, `anthropic/claude-sonnet-4`) — adapter-specific, leave but note

**F — pure help/comment text (cosmetic; update strings, not logic)**
- `chat_mode.py:41`, `cli.py:1533`, `cli.py:2752`, `config.py:28-29` comments

> opus-4-7 → opus-4-8 must change in: `cli.py:71`, `cli.py:1493`, `chat_mode.py:375`, `maestro_legacy.py:21`, `maestro_legacy.py:25`, `maestro_adapters.py:10`, `setup_wizard.py:210`. With the single constant, these all flip from ONE edit.

### 1.4 The warm normalization gap (precise)
- Command build: config `--model opus` → `_extract_model_from_parts` → `"opus"` (`agents.py:601`).
- Warm lookup: `warm_session.fork_args(root, "opus")` → file `warm/claude/opus.json` (`agents.py:611`, `warm_session.py:47-57`).
- Warm seed (`burnless warm init`): `cli.py:2123` default `"claude-sonnet-4-6"` → file `warm/claude/claude-sonnet-4-6.json`.
- → two different files for the same logical model. Lookup under the alias never finds the seed under the full id. **No call site normalizes.** Fix = normalize at BOTH ends.

---

## 2. THE single source of truth

### 2.1 Canonical constants (new, in `config.py` — the ONLY hardcoded last-resort)
```python
# config.py — canonical model identity. The ONLY place model ids are hardcoded.
DEFAULT_TIER_MODELS = {
    "gold":   "claude-opus-4-8",            # bumped from 4-7 (Roberto, 2026-05-29)
    "silver": "claude-sonnet-4-6",
    "bronze": "claude-haiku-4-5-20251001",
}
DEFAULT_PROVIDER_MODELS = {                 # provider-level last-resort (warm keys, autobalance)
    "claude": "claude-sonnet-4-6",
    "codex":  "gpt-5.2",
}
HAIKU_MODEL = DEFAULT_TIER_MODELS["bronze"]  # keep existing name, now derived

# alias → canonical full id. Both command-build and warm/footer/cache go through normalize_model.
MODEL_ALIASES = {
    "opus":   "claude-opus-4-8",
    "sonnet": "claude-sonnet-4-6",
    "haiku":  "claude-haiku-4-5-20251001",
    "claude-haiku-4-5": "claude-haiku-4-5-20251001",  # codec short form
}
```

### 2.2 Resolver API (new, in `config.py`)
```python
def normalize_model(name: str | None) -> str | None:
    """Map any alias/short form to its canonical full id. Idempotent.
    Unknown / already-canonical / non-claude (codex gpt-*) pass through unchanged.
    Used by BOTH command build AND warm/footer/cache lookup so the key is stable."""
    if not name:
        return name
    n = name.strip()
    return MODEL_ALIASES.get(n, n)

def resolve_model(tier: str, cfg: dict | None = None) -> str:
    """Canonical full model id for a tier.
    Precedence: cfg['agents'][tier] command `--model X` (normalized)
              > cfg['agents'][tier]['name'] (normalized)
              > DEFAULT_TIER_MODELS[tier]
              > DEFAULT_TIER_MODELS['silver']."""
    cfg = cfg or {}
    agent = (cfg.get("agents") or {}).get(tier) or {}
    cmd = agent.get("command") or ""
    m = _extract_model_token(cmd)          # regex --model/-m, stdlib only
    if m:
        return normalize_model(m)
    if agent.get("name"):
        return normalize_model(agent["name"])
    return DEFAULT_TIER_MODELS.get(tier, DEFAULT_TIER_MODELS["silver"])

def resolve_fallback_model(tier: str, cfg: dict | None = None) -> str | None:
    """Canonical fallback id for a tier, or None.
    Reads cfg['agents'][tier]['providers'][1:][0] command --model (autobalance
    fallback), else cfg['agents'][tier]['fallback']. Normalized. None if absent."""
    cfg = cfg or {}
    agent = (cfg.get("agents") or {}).get(tier) or {}
    provs = agent.get("providers") or []
    if len(provs) > 1:
        m = _extract_model_token(provs[1].get("command", "")) or provs[1].get("name")
        return normalize_model(m)
    fb = agent.get("fallback")
    return normalize_model(fb) if fb else None
```
`_extract_model_token(cmd_str)` = shared stdlib regex `--model\s+(\S+)|-m\s+(\S+)`. `agents._extract_model_from_parts` (list form) and `cli._extract_model` (str form) both delegate to / mirror this so there is ONE extraction semantics.

### 2.3 Data flow after the change
```
config.yaml agents.gold.command "claude --model opus"
        │
   resolve_model("gold", cfg) ─┐         _extract_model_from_parts → "opus"
        │ "claude-opus-4-8"     │                   │ normalize_model
   footer / pricing / defaults  │            "claude-opus-4-8"
                                └──────────────────┘  (SAME canonical id)
                                          │
                          warm_file_path("claude-opus-4-8")
                          == seed key == footer key == cache key  → WARM HIT
```

---

## 3. Site → replacement table

| file:line | current hardcode | replacement |
|---|---|---|
| `cli.py:71-73` | `MAESTRO_TIER_MODEL={...}` | `from .config import DEFAULT_TIER_MODELS as MAESTRO_TIER_MODEL` (or `resolve_model(tier,cfg)` at call sites) |
| `cli.py:284` | codex fallback `"gpt-5.2"` | `config.DEFAULT_PROVIDER_MODELS["codex"]` |
| `cli.py:1493` | `or "claude-opus-4-7"` | `or config.resolve_model("gold", cfg)` |
| `cli.py:2123` | warm-init `"claude-sonnet-4-6"` | `config.normalize_model(getattr(args,"model",None)) or config.DEFAULT_PROVIDER_MODELS["claude"]` |
| `cli.py:2136` | warm-init `"gpt-5.2"` | `... or config.DEFAULT_PROVIDER_MODELS["codex"]` |
| `chat_mode.py:375-377` | tier dict (opus-4-7…) | `config.DEFAULT_TIER_MODELS` |
| `chat_mode.py:379` | fallback `"claude-sonnet-4-6"` | `config.DEFAULT_TIER_MODELS["silver"]` |
| `maestro_legacy.py:21` | `DEFAULT_MAIN_MODEL="claude-opus-4-7"` | `= config.DEFAULT_TIER_MODELS["gold"]` |
| `maestro_legacy.py:25-27` | PRICES keys (opus-4-7) | rekey to canonical ids; add `claude-opus-4-8` (keep 4-7 entry for old logs) |
| `maestro/core.py:13` | `DEFAULT_BRAIN_MODEL` | `= config.DEFAULT_TIER_MODELS["silver"]` |
| `maestro_runner.py:19` | `DEFAULT_MODEL` | `= config.DEFAULT_TIER_MODELS["bronze"]` |
| `maestro_layer.py:116` | param `"claude-sonnet-4-6"` | `= config.DEFAULT_TIER_MODELS["silver"]` |
| `agents.py:601` (extract) | returns raw alias | wrap return in `config.normalize_model(...)` |
| `agents.py:604` | provider default | `config.DEFAULT_PROVIDER_MODELS[provider]` |
| `agents.py:697` | provider default | `config.normalize_model(...) or config.DEFAULT_PROVIDER_MODELS[_prov]` |
| `live_runner.py:334,:443` | claude/codex ternary | `config.DEFAULT_PROVIDER_MODELS[provider]` |
| `warm_session.py:184,:344` | param `"claude-sonnet-4-6"` | `= config.DEFAULT_PROVIDER_MODELS["claude"]`; normalize inside `warm_file_path` |
| `warm_session.py:47-57` | keys by raw `model` | `safe_model = normalize_model(model).replace("/","_")` |
| `warm_session_codex.py:34` | `DEFAULT_MODEL="gpt-5.2"` | `= config.DEFAULT_PROVIDER_MODELS["codex"]` |
| `warm_daemon.py:94,:103` | claude/codex string | `config.DEFAULT_PROVIDER_MODELS[...]` |
| `keepalive.py:140,:172` | haiku/sonnet | `config.HAIKU_MODEL` / `config.DEFAULT_PROVIDER_MODELS["claude"]` |
| `codec/decoder.py:10`,`encoder.py:11` | `"claude-haiku-4-5"` | `= config.normalize_model("claude-haiku-4-5")` (=`config.HAIKU_MODEL`) |
| `codec/police.py:18` | param sonnet | `= config.DEFAULT_TIER_MODELS["silver"]` |
| `compression.py:457` | haiku full | `config.HAIKU_MODEL` |
| `subscription_usage.py:90` | haiku full | `config.HAIKU_MODEL` |
| `maestro_adapters.py:10-12` | `DEFAULT_ANTHROPIC_MODELS` | `tuple(config.DEFAULT_TIER_MODELS.values())` (opus-4-8 first) |
| `setup_wizard.py:210/212/214` | hardcoded full ids in commands | f-string from `config.DEFAULT_TIER_MODELS` (or emit aliases `opus/sonnet/haiku`) |
| `pty_shell.py:364,:399` | `cfg.get("brain",{}).get("model","")` | `config.resolve_model(<active tier>, cfg)` (or `resolve_layer_models(cfg)["maestro"]` for the maestro footer) |
| `provider_autodetect.py:*` | model names in generated cfg | f-string from `config.DEFAULT_*` constants |
| `profiles.py:16,25,50` | model strings | `config.DEFAULT_*` constants |

Cosmetic-only (update string, no logic): `chat_mode.py:41`, `cli.py:1533`, `cli.py:2752`, `config.py:28-29`, `maestro_adapters.py:231`.

Leave as-is (adapter-vendor specific, not tier identity): `maestro_adapters.py:74/81/112/120` (`gpt-4o`, `anthropic/claude-sonnet-4`) — note in PR they're OpenRouter/codex adapter constants, out of tier scope.

---

## 4. How warm + footer get fixed

- **Warm command path:** `agents._extract_model_from_parts` returns `normalize_model("opus")="claude-opus-4-8"`. The string that flows into `fork_args`/`init`/`touch` is canonical.
- **Warm storage path:** `warm_session.warm_file_path` normalizes again (defensive, idempotent) → `warm/claude/claude-opus-4-8.json`. Seed (`warm init`) and lookup now produce the identical path.
- **Footer:** `pty_shell` calls `config.resolve_model(tier, cfg)` → same `claude-opus-4-8` → footer prints the real model and the warm/cache state for that exact key. `[no-warm]` only shows when genuinely cold.
- **Cache key:** any cache/preamble key derived from model uses `resolve_model`/`normalize_model` → byte-identical to warm key.

---

## 5. Migration safety

- Behavior is **identical for existing configs**: a config with `--model claude-sonnet-4-6` already canonical → `normalize_model` returns it unchanged; a config with `--model sonnet` now resolves to the same full id the seeds already used → previously-broken warm now hits (strict improvement, no regression).
- `DEFAULT_TIER_MODELS`/`DEFAULT_PROVIDER_MODELS` carry the **same** silver/bronze defaults that were copy-pasted, so any path that fell through to a default lands on the identical id. Only `gold` intentionally changes 4-7→4-8.
- Pricing dict keeps the `claude-opus-4-7` entry (old logs) and ADDS `claude-opus-4-8` — no KeyError on historical data.

**Tests that must stay green** (grep `model|resolve|warm|route` in tests/):
`test_config_tiers.py`, `test_per_layer_tier.py` (resolve_layer_models), `test_provider_autodetect.py`, `test_provider_autobalance.py`, `test_setup_wizard.py`, `test_profiles.py`, `test_chat_mode.py`, `test_maestro_adapters.py`, `test_maestro_layer.py`, `test_maestro_runner.py`, `test_keepalive_metrics.py`, `test_routing.py`, `test_stale.py`, `test_p0_runtime.py`, `test_worker_envelope.py`.
**New tests to add:** `test_model_resolver.py` — `normalize_model` alias→canonical (incl. idempotency + passthrough), `resolve_model` precedence (command > name > default), `resolve_fallback_model` providers/fallback/None, warm key equality (`warm_file_path(normalize("opus")) == warm_file_path("claude-opus-4-8")`), and the opus-4-8 default assertion.

---

## 6. Sequencing (CRITICAL)

This impl edits `cli.py` at :71, :284, :1493, :2123, :2136 (and cosmetic :1533, :2752). **d493 (P0 honest-exit-code)** rewrites the cli.py run/status region (`_cmd_run_body` ~679–971, `:1248-1266`) per `_design/p0_honest_exit_code_2026-05-29.md`. The line ranges don't overlap, but both touch the same file and d493 shifts line numbers. → **This MUST land AFTER d493 is committed.** Re-grep line numbers at impl time; do not trust the literals in §3 if d493 already merged (they only move down, not change content).

---

## 7. Impl ordering for silver

1. Add constants + `normalize_model`/`resolve_model`/`resolve_fallback_model`/`_extract_model_token` to `config.py`. Make `HAIKU_MODEL` derive from `DEFAULT_TIER_MODELS`.
2. Wire warm: `agents._extract_model_from_parts` normalize-on-return; `warm_session.warm_file_path` normalize; defaults → `DEFAULT_PROVIDER_MODELS`.
3. Replace category A/B/C/D sites per table.
4. Footer (`pty_shell`) → `resolve_model`.
5. Generators E (wizard/autodetect/profiles/adapters) → constants.
6. Add `test_model_resolver.py`; run full suite.
