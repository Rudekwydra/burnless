# Burnless Protocol

Burnless is a candidate protocol layer for human-LLM and LLM-LLM
communication: a living, compressed, privacy-aware intermediary language
between humans, maestros, and workers.

Burnless separates four surfaces:

1. what the human says,
2. what the model needs,
3. what the provider sees,
4. what the project must remember.

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
