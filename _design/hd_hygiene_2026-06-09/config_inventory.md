# Burnless Config Inventory — HD Hygiene 2026-06-09

Delegation **d516**. Read-only inventory of every `.burnless/config.yaml` on the HD,
plus tier-divergence analysis. Quarantine actions documented in `quarantine_report.md`.

Columns: tier → `name` (model from `command`, truncated). `command` truncated to the
model/binary identity only. `—` = tier absent in that file. `n/a` = key absent.

## Comparison table

| # | Config file | gold | silver | bronze | diamond | preset | encoder.model | maestro.model | hardcore_filter |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `~/.burnless/config.yaml` | claude-opus (`claude --model opus`) | codex-o4mini (`codex exec -m o4-mini`) | claude-haiku (`claude --model haiku`) | — | n/a | n/a | n/a | false |
| 2 | `antigravity/.burnless` | claude-opus (`claude --model opus`) | **claude-haiku** (`claude --model haiku`) | **codex-gpt-5.4-mini-low** (`codex exec --model gpt-5.4-mini`) | claude-opus-ultrathink (`opus … ultrathink`) | n/a | n/a | n/a | false |
| 3 | `antigravity/burnless/.burnless` | claude-opus (`rtk … claude --model opus`) | **codex-gpt-5.2** (`codex exec --sandbox danger-full-access`) | claude-haiku-4-5 (`rtk … haiku-4-5`) | — (gemini-diamond extra) | n/a | n/a | n/a | false |
| 4 | `antigravity/forgetless/.burnless` | opus (`claude --model opus`) | codex (`codex exec --sandbox workspace-write`) | haiku (`claude --model haiku`) | — | n/a | n/a | n/a | false |
| 5 | `antigravity/nutri/.burnless` | opus (`claude --model opus`) | **haiku** (`claude --model haiku`) | **haiku** (`claude --model haiku`) | **codex** (`codex exec -m gpt-5.5`) | n/a | n/a | n/a | **n/a (absent)** |
| 6 | `antigravity/fw-social/.burnless` | claude-opus (`rtk … opus`) | claude-sonnet-4-6 (`rtk … sonnet-4-6`) | **claude-sonnet-4-6** (`rtk … sonnet-4-6`) | — | n/a | n/a | n/a | false |
| 7 | `antigravity/fw-social-next/.burnless` | claude-opus (`rtk … opus`) | claude-sonnet-4-6 (`rtk … sonnet-4-6`) | **claude-sonnet-4-6** (`rtk … sonnet-4-6`) | — | n/a | n/a | n/a | false |
| 8 | `antigravity/agilize/.burnless` | opus (`claude --model opus`) | **haiku** (`claude --model haiku`) | **codex-gpt-5.4-mini-low** (`codex exec --model gpt-5.4-mini`) | opus-ultrathink (`opus … ultrathink`) | n/a | n/a | n/a | **n/a (absent)** |
| 9 | `antigravity/app_paty/.burnless` | claude-opus (`claude --model opus`) | codex-gpt-5.2 (`codex exec --sandbox danger-full-access`) | claude-haiku-4-5 (`claude --model haiku-4-5`) | — | **protocol** | **null** | **null** | false |
| 10 | `antigravity/aeomachine/.burnless` | claude-opus (`claude --model opus`) | codex-gpt-5.2 (`codex exec --sandbox danger-full-access`) | claude-haiku-4-5 (`claude --model haiku-4-5`) | — | n/a | n/a | n/a | false |
| 11 | `antigravity/leads-rudekwydra/.burnless` | opus (`claude --model opus`) | **haiku** (`claude --model haiku`) | **codex-gpt-5.4-mini-low** (`codex exec --model gpt-5.4-mini`) | opus-ultrathink (`opus … ultrathink`) | n/a | n/a | n/a | false |
| 12 | `antigravity/rudekwydra-atendimento/.burnless` | opus (`claude --model opus`) | **haiku** (`claude --model haiku`) | **codex-gpt-5.4-mini-low** (`codex exec --model gpt-5.4-mini`) | opus-ultrathink (`opus … ultrathink`) | n/a | n/a | n/a | false |
| 13 | `~/.burnless/desktop/config.yaml` | — | — | — | — | n/a | n/a | n/a | n/a |
| 14 | `…/Dropbox/…/chardon_catalog/.burnless` **[QUARANTINED]** | claude-opus (`claude --model opus`) | codex-gpt-5.2 (`codex exec --sandbox danger-full-access`) | claude-haiku-4-5 (`claude --model haiku-4-5`) | — | n/a | n/a | n/a | false |
| 15 | `semgit/social-machine/.burnless` **[QUARANTINED]** | claude-opus (`claude --model opus`) | codex-gpt-5.2 (`codex exec --sandbox danger-full-access`) | claude-haiku-4-5 (`claude --model haiku-4-5`) | — | n/a | n/a | n/a | false |
| 16 | `semgit/burnless-launch/.burnless` **[QUARANTINED]** | opus (`claude --model opus`) | **codex** (`codex exec --sandbox workspace-write`) | haiku (`claude --model haiku`) | codex (`codex exec`) | n/a | n/a | n/a | **n/a (absent)** |
| 17 | `semgit/burnless-launch/experiment/.burnless` **[QUARANTINED]** | opus (`claude --model opus`) | **sonnet** (`claude --model sonnet`) | haiku (`claude --model haiku`) | codex (`codex exec --sandbox workspace-write`) | n/a | n/a | n/a | **n/a (absent)** |

> Note: 17 config.yaml files inventoried (spec context said "20 scattered"; 17 explicit
> paths were provided and all 17 read). `encoder.model` / `maestro.model` / `preset`
> keys exist in only ONE file (#9 app_paty) — everywhere else they are absent (n/a),
> not null.

## Divergences

### 1. `~/.burnless/desktop/config.yaml` is NOT a tier config
File #13 has **no `agents`/`routing`** at all — it is a `features.gapless` (dev/work
path-watch) config. Listed in the spec but carries zero tier mapping. Treated as a
different schema, left untouched (not a zombie, not a tier remap).

### 2. SILVER is the most divergent tier — 4 different mappings
- **codex (gpt-5.2, danger-full-access)** → burnless, app_paty, aeomachine, chardon[Q], social-machine[Q]
- **codex (gpt, workspace-write)** → forgetless, burnless-launch[Q]
- **claude-sonnet-4-6** → fw-social, fw-social-next, (+ experiment[Q] uses bare `sonnet`)
- **claude-haiku** → antigravity, nutri, agilize, leads-rudekwydra, rudekwydra-atendimento
- **codex-o4mini** → ~/.burnless (root, oldest)

The Roberto-default family (antigravity + the rudekwydra/leads/agilize/nutri cluster)
maps **silver→haiku**, which is unusual: silver is "everyday execution" yet points at
the cheapest claude model. The burnless/fw-social cluster maps silver→codex or sonnet
(stronger). Root `~/.burnless` still on the legacy `codex-o4mini`.

### 3. BRONZE diverges 4 ways
- **claude-haiku / haiku-4-5** → ~/.burnless, burnless, forgetless, nutri, app_paty, aeomachine, chardon[Q], social-machine[Q], burnless-launch[Q], experiment[Q]
- **codex-gpt-5.4-mini-low** → antigravity, agilize, leads-rudekwydra, rudekwydra-atendimento
- **claude-sonnet-4-6** → fw-social, fw-social-next (bronze == silver == sonnet; cache-aware tiering, intentional per file comment)

fw-social / fw-social-next deliberately collapse silver==bronze==sonnet for prompt-cache
sharing (documented in-file). nutri collapses gold-aside silver==bronze==haiku and even
diamond==codex — effectively a 2-model setup.

### 4. GOLD is nearly uniform — opus everywhere
Every tier config maps **gold→opus** (`claude-opus` / `opus`). Only cosmetic divergence:
`rtk` wrapper + `BURNLESS_WORKER_MODE_v1` system-prompt (burnless, fw-social, fw-social-next)
vs bare `claude --model opus -p` (the rest). No model-identity disagreement on gold.

### 5. DIAMOND tier presence is inconsistent
Present in: antigravity (opus-ultrathink), nutri (codex gpt-5.5), agilize/leads/rudekwydra-atendimento
(opus-ultrathink), burnless-launch[Q]/experiment[Q] (codex). Absent in the
burnless/fw-social/app_paty/aeomachine/chardon/social-machine cluster. Where present, it
splits opus-ultrathink (decision/second-opinion) vs codex (code/debug) — two incompatible
philosophies of what "diamond" means.

### 6. `hardcore_filter` mostly false, absent in 4
`false` in 11 files; **absent** in nutri, agilize, burnless-launch[Q], experiment[Q]
(those routing blocks omit the key). No file sets it `true`.

### 7. Wrapper / worker-mode divergence
`rtk` warm-cache wrapper + `BURNLESS_WORKER_MODE_v1` appended prompt only in burnless,
fw-social, fw-social-next, antigravity (silver). The rest invoke `claude`/`codex` bare —
those workers may read CLAUDE.md as operator instead of as a worker spec.

### 8. compression.mode spread
`light` (burnless, forgetless, fw-social, fw-social-next, rudekwydra-atendimento),
`balanced` (~/.burnless, app_paty, aeomachine, social-machine[Q]),
`extreme` (leads-rudekwydra). Mixed, no standard.
