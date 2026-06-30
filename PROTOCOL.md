# Burnless Protocol

Burnless is a candidate protocol layer for human-LLM and LLM-LLM
communication: a living, compressed, privacy-aware intermediary language
between humans, maestros, and workers.

Burnless separates four surfaces:

1. what the human says,
2. what the model needs,
3. what the provider sees,
4. what the project must remember.

## Architecture

Every Burnless session has three configurable components:

- **Encoder/Decoder** — compresses raw text into capsules and expands capsules
  back to natural language. Default: cloud LLM (Haiku). Private: local Ollama.
- **Maestro** — the orchestrating LLM that receives only capsules, decides
  what to do, and emits delegation instructions. Never executes directly.
  Default: cloud LLM. Private: local Ollama.
- **Workers** — gold/silver/bronze agents that execute delegated tasks and
  respond with compact capsules. Default: cloud LLMs. Can be local.

The Maestro never sees raw text — only capsules. Workers never see conversation
history — only the specific task capsule they receive.

## The economy: separating verbose, execution, and thinking

The base economy of Burnless is not compression — it is **separating three
intents that a single LLM turn conflates**:

- **Verbose** — narration, explanation, justification written for the human.
- **Execution** — the actual action (tool call, edit, command, artifact).
- **Thinking** — reasoning needed to produce the action.

Conflated in one turn, verbose and thinking become **dead weight**: they are
re-sent as input on every subsequent turn, which is the O(N²) blow-up. The win
comes from keeping each intent on its own surface:

- **Thinking is one-shot** — consumed in the turn that produces the action,
  never carried forward as context. Reasoning is leverage at the moment of the
  decision, dead weight the moment after.
- **Execution returns a compact capsule**, not its own transcript — the capsule
  is what the next turn sees.
- **Verbose is routed to the human surface**, not into the model's working
  context. The human reads it; the model never re-ingests it.

This is why the Maestro sees only capsules and workers see only their task: the
architecture *enforces* the separation rather than hoping the model self-limits.

> **Evolution note.** Mechanically compacting verbose *after the fact* (a
> context-GC / input-collapse pass) was an earlier attempt at this same goal —
> separating the intents downstream. It was superseded: input to the model is
> already cache-cheap (cache reads ~0.1×), so compacting it is marginal, and
> rewriting the prefix fights the cache. The correct lever is separating the
> intents **at the source**, not compressing them after they have already been
> conflated.

## Privacy Levels

Privacy is a consequence of where each component runs, not a mode flag.

| Level | Encoder/Decoder | Maestro | Workers | Cloud sees |
|-------|----------------|---------|---------|------------|
| **0** | Cloud | Cloud | Cloud | Everything |
| **1** | Local | Cloud | Cloud | Compressed capsules only — not raw text |
| **2** | Local | Local | Cloud | Disconnected task fragments — no context |
| **3** | Local | Local | Local | Nothing |

**Level 0** is the default. Maximum cost efficiency, zero additional privacy.

**Level 1** (local encoder): the cloud Maestro receives compressed capsules,
never the raw message. Meaningful reduction in what any provider stores.

**Level 2** (local Maestro): the strongest practical configuration. Cloud
workers receive individual task capsules with no conversation context — they
cannot reconstruct who the user is, what the project is, or what came before.
Each cloud API call is an isolated, contextless fragment.

**Level 3** (all local): zero cloud exposure. Fully offline. API cost zero.

The cost math (O(N²) → O(N)) applies at all four levels. Privacy level is
independent of cost reduction.

## Cache and Model Switching

Within the same provider, switching models mid-session does not invalidate the
cache. The cache key is the byte-identical content of the system prompt, not
the model identifier. Switching Opus → Sonnet on Anthropic recovers a warm
cache in one turn.

Switching providers (Anthropic → OpenAI, cloud → local) starts a fresh cache.
The Burnless CLI surfaces a tip when this happens.

## Modes

### Cost Mode

Current default. Burnless compresses raw conversation into operational capsules
and keeps repeated context small. This reduces cost and repeated exposure, but
it is not a strong privacy guarantee.

### Redact Mode

Planned. Burnless replaces sensitive local values with placeholders before any
provider call:

```text
Roberto, CPF 123... -> PERSON_1, TAX_ID_1
```

The provider sees placeholders. The map stays local.

### Audit Mode

Planned. Capsules use authenticated encryption and the key is stored in a
customer-controlled local keystore. Old capsules remain auditable by the
customer.

### Opaque Mode

Planned. Capsules use authenticated encryption and the key is memory-only. When
the process/session ends, old capsules are intentionally undecodable.

### Burnkey Mode

Planned. An explicit local operation destroys the decryption key for selected
capsules or sessions before the natural end of the process. When no other copy
of the key or raw source is retained, the encrypted capsule becomes
unrecoverable by Burnless.

Burnkey is a protocol operation, not a legal shortcut. Strong claims require
matching retention policy, key custody, audit behavior, and tests.

## Capsule Versions

### v1 Legacy

```text
burnless:<session_id>:<key>:<base64_ciphertext>
```

This format embeds the key and is kept only for backward compatibility. Do not
use v1 for privacy claims.

### v2 Current Envelope

```text
burnless:v2:<session_id>:<key_id>:<base64_ciphertext>
```

The key is not embedded in the capsule. The default keyring is local process
memory. This closes the v1 footgun, but the current envelope is still not an
enterprise cryptography claim.

## Non-Goals In v0.5

- Burnless v0.5 does not claim zero-knowledge.
- Burnless v0.5 does not claim providers cannot see any sensitive text.
- Burnless v0.5 does not yet enforce local redaction before provider calls.
- Burnless v0.5 does not yet persist audit keys in a customer keystore.
- Burnless v0.5 does not yet implement burnkey destruction semantics.

## Glossary Layers

Burnless uses a compression language, not only a compression algorithm. The
target design has three glossary layers:

1. **Core glossary**: fixed protocol terms, versioned with Burnless.
2. **Tenant/project glossary**: local domain language controlled by the user or
   customer.
3. **Session emergent glossary**: append-only mappings proposed by the encoder
   and validated by Burnless during a conversation.

Session glossary compaction must preserve glossary meaning separately from
capsule meaning. A future compactor should emit a `GLOSSARY_SUPERBLOCK` and a
`CAPSULE_SUPERBLOCK` instead of mixing both into one lossy summary.

## Design Target

The protocol target is:

```text
human intent -> local transform -> provider-safe capsule -> local memory/audit
```

The implementation target is to make `privacy.mode` explicit instead of implied.
