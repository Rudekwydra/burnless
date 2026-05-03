# Maestro v1 Schemas

## Capsule (line format)

```
{tier} {action} {target} :: {status} {summary} [ref:...]
```

- `tier` ∈ `gld | slv | brz | dia | ~gld | +slv` (com modificador opcional)
- `action` ∈ `imp | val | aud | fix | rev | del→T<id> | ret | enc | dec`
- `target` ∈ `app/auth` | `svc/cron` | `web/seo` | etc (domain/feature, short)
- `status` ∈ `OK | PART | BLK | ERR | WIP`
- `summary` < 80 chars, telegráfico
- `[ref:...]` opcional, aponta pra exec_log/T<id> ou outra capsule

## exec_log/T<id>.md

Schema fixo (worker preenche, Brain só carrega se auditar):

```yaml
---
id: T42
parent_capsule: "gld del→T42 slv imp app/auth :: schema+router+prompts"
tier: silver
model: claude-sonnet-4-6
started: 2026-05-02T22:15:30Z
ended: 2026-05-02T22:21:54Z
duration_s: 384
status: OK
files_touched:
  - db/schema.sql
  - src/lib/auth/router.ts
  - src/lib/auth/prompts.ts
  - src/components/auth/login-form.tsx
validations:
  - cmd: db migrate
    result: pass
  - cmd: build
    result: pass
issues: []
tokens:
  input: 8421
  output: 2103
  cache_read: 5832
  cache_creation: 412
---

## Full transcript

(raw worker thoughts, file contents, decision log — disposable, only loaded on aud)
```

## Brain history (in-memory, persisted to .burnless/brain_history.jsonl)

Append-only log of capsule turns. Each turn is one user input + brain output.
The full jsonl is the messages array passed to Anthropic API on next turn.

```jsonl
{"role": "user", "ts": "...", "raw": "tive uma ideia, ...", "capsule": "raw:tive uma ideia... → enc:brz/usr q-T01"}
{"role": "assistant", "ts": "...", "think": "...", "capsule": "gld :: ...", "delegates": ["del→T42"]}
{"role": "user", "ts": "...", "raw": "ok continua", "capsule": "raw:ok continua"}
...
```

The `messages` array sent to API contains ONLY the `capsule` field — never raw
or think. Raw stays in jsonl for replay/debug; think is shown to user but
discarded from API.

## Cache breakpoints

System blocks (4 max breakpoints per Anthropic API):
1. `glossary` (always, ttl 1h)
2. `role` (brain or worker variant, ttl 1h)
3. `project_memory` (last MEMORY.md snapshot, ttl 1h, refresh on mutation)
4. `recent_capsules` (last 20 turns, ttl 5min, rolling)

Live (no cache):
5. Current user capsule

## Encoder/Decoder (bronze)

### Encoder
Input: raw user message in PT-BR (any length).
Output: glossary capsule(s).

Few-shot prompt to Haiku includes glossary block + 5-10 examples of
PT-BR → capsule transforms. Output strictly capsule format, nothing else.

### Decoder
Input: glossary capsule(s) from Brain output `[CAPSULE]` block.
Output: PT-BR natural, tom Burnless (amigável, direto, sem rodeio).

Few-shot prompt to Haiku includes glossary + style guide examples.

## Police (Sonnet, opt-in)

Triggered when:
- Encoder Haiku marks confidence < 0.8
- Brain outputs capsule with `?` (asking user)
- Manual flag `BURNLESS_POLICE=1`

Police re-reads original user raw message + Haiku's encoded capsule, validates
the capsule preserves the meaning. If not, replaces with corrected capsule.
Police output stays in jsonl for audit; only the final capsule goes to API.
