# Burnless Protocol Specification

**Version:** 1.0-draft  
**Date:** 2026-05-04  
**Author:** Roberto Wydra (RudekWydra)  
**Repository:** https://github.com/Rudekwydra/burnless  
**Status:** Draft — establishing prior art  

---

## Abstract

Burnless defines a protocol for human-LLM and LLM-LLM communication that
reduces multi-turn agent loop cost from Θ(N²) to Θ(N) by replacing raw
conversation history with compact operational capsules, shared prefix caching,
and a three-tier worker delegation architecture. The protocol is
provider-agnostic, open (MIT), and separates the orchestration layer from the
execution layer in a way that produces configurable privacy guarantees as an
architectural consequence — not a feature flag.

---

## 1. Problem Statement

Every turn in a standalone LLM agent loop replays the full conversation as
input. At turn N, the model receives the prefix P plus the sum of all prior
exchanges. Total input cost across N turns is Θ(N²). This is arithmetic from
published pricing pages, not a property of any SDK or model.

At 1–5% of global electricity within a decade (projected LLM inference load),
the quadratic cost shape is not a pricing quirk — it is a trajectory.

---

## 2. Core Architecture

A Burnless session has three configurable components:

```
User
  ↓  raw natural language
[Encoder]
  Deterministic minifier (pure Python, zero cost)
  + Semantic encoder LLM (compact capsule, ~$0.001/turn)
  ↓  capsule (80-char compressed representation)
[Maestro]
  Receives ONLY capsules — never raw text
  Maintains session state via capsule history
  Decides: respond directly | delegate to worker | ask for clarification
  NEVER executes commands directly
  ↓  delegation instruction (capsule format)
[Worker: gold | silver | bronze]
  Receives isolated task capsule — no conversation history
  Executes task, returns compact capsule result
  Logs execution to disk (encrypted in privacy modes)
  ↓  result capsule
[Maestro]
  Receives result, updates session state
  ↓  response capsule
[Decoder]
  Semantic decoder LLM (expands capsule → natural language)
  ↓  natural language response
User
```

### 2.1 Component Roles

**Encoder/Decoder:** Translates between natural language and capsule format.
Default: cloud LLM (Haiku-class). Privacy alternative: local model (Ollama).

**Maestro:** The persistent orchestrating agent. Receives only capsules, holds
session context as a capsule history (not a transcript), and emits delegation
instructions. The Maestro is configurable — any LLM at any provider, or a
local model.

**Workers:** Ephemeral execution agents. Receive a single task capsule with no
conversation context. Three quality/cost tiers:
- `gold` — highest capability, complex reasoning, architecture
- `silver` — standard implementation, documentation, code
- `bronze` — fast classification, summarization, extraction

Workers are provider-agnostic. A session can mix Claude, GPT, Codex, and local
Ollama workers simultaneously.

---

## 3. The Capsule Format

A capsule is an 80-character-target compressed representation of a turn or
task. Format:

```
{tier} {action} {target} :: {status} {detail} [ref:{exec_id}]
```

Examples:
```
gld imp auth/jwt :: OK schema+router+middleware done [ref:exec/T0042]
slv doc api/ :: PART openapi.yaml done, examples pending [ref:exec/T0043]
brz sum logs/app.log :: OK 3 errors found, 2 warnings [ref:exec/T0044]
```

Status values: `OK` | `PART` | `BLK` | `ERR`

### 3.1 Capsule Versions

**v1 (legacy):** `burnless:<session_id>:<key>:<base64_ciphertext>`  
Key embedded in capsule. Retained for compatibility only. Do not use for
privacy claims.

**v2 (current):** `burnless:v2:<session_id>:<key_id>:<base64_ciphertext>`  
Key held in local process memory. Key ID references the local keyring only.

---

## 4. Cost Model

Let:
- `N` = turns in the session
- `P` = persistent prefix tokens (system prompt)
- `C` ≈ 20 tokens = capsule size (≈ 80 chars)
- `T` ≈ 1,500 tokens = typical raw turn size
- `p_cr` = cache read price (e.g., $0.15/MTok)
- `p_cw` = cache write price (e.g., $3.75/MTok)
- `p_in` = standard input price (e.g., $15/MTok)

**Standalone loop total input cost:**
```
cost_standalone(N) ≈ N·P·p_in + T·N(N-1)/2·p_in  →  Θ(N²)
```

**Burnless loop total input cost:**
```
cost_burnless(N) ≈ P·p_cw + (N-1)·P·p_cr + C·N(N-1)/2·p_in  →  Θ(N)
```

The capsule term `C·N(N-1)/2` is technically Θ(N²) but with constant
`C/T ≈ 0.013` — approximately 75× smaller. For N ≤ 1,000 it remains below
the linear cache-read term. Practically: Θ(N).

**Cache activation invariant:** The system anchor must satisfy `P ≥ P_min`
where `P_min` is the provider's minimum cacheable prefix length (1,024 tokens
for current Anthropic models). If `P < P_min`, no cache write occurs and cost
remains Θ(N²) regardless of capsule compression. The Burnless glossary anchor
is designed to guarantee `P ≥ P_min` from turn 1. An undersized anchor is
a silent correctness failure — the formula holds but the constant factor
does not improve.

Benchmark result (reproducible, `bench/run.py`): **88% cost reduction at
turn 10** with default Anthropic pricing.

---

## 5. Privacy Levels

Privacy in Burnless is a consequence of where each component runs — not a
mode flag. Four levels:

| Level | Encoder/Decoder | Maestro | Workers | What cloud providers see |
|-------|----------------|---------|---------|--------------------------|
| **0** | Cloud | Cloud | Cloud | Everything |
| **1** | Local | Cloud | Cloud | Capsules only — not raw text |
| **2** | Local | Local | Cloud | Disconnected task fragments — no session context |
| **3** | Local | Local | Local | Nothing |

**Level 2** is the strongest practical configuration for most users. Cloud
workers receive individual task capsules with no conversation history, no
project context, and no user identity linkage beyond the API account. Each
cloud call is an isolated, contextless fragment.

**Important:** Level 2 reduces exposure but is not a cryptographic guarantee.
Provider-side correlation via account metadata, timing, and embedding
similarity remains possible. Level 3 is the only configuration with a hard
privacy guarantee.

The cost reduction (Θ(N²) → Θ(N)) applies at all four levels.

---

## 6. Glossary Architecture

Burnless uses a three-layer compression language:

1. **Core glossary** — fixed protocol terms, versioned with the spec.
   Byte-identical across all users. Eligible for shared prefix caching.

2. **Tenant/project glossary** — local domain language defined per project in
   `tenant_glossary.yaml`. Cached per project.

3. **Session emergent glossary** — append-only mappings proposed by the
   encoder during a session and validated by the Maestro before adoption.
   Survives compaction as a `GLOSSARY_SUPERBLOCK`.

---

## 7. Cache Architecture

The Maestro's system prompt is byte-identical every turn, enabling persistent
prefix caching (e.g., Anthropic `cache_control: ephemeral`). Cache read price
is approximately 10× cheaper than standard input price (100× cheaper than
cache write). This is the primary source of cost reduction alongside capsule
compression.

**Model switching within the same provider** does not invalidate the cache.
The cache key is the content of the cached block, not the model identifier.
Switching Opus → Sonnet on Anthropic recovers a warm cache within one turn.

**Provider switching** resets the cache. The Burnless CLI surfaces a tip when
this occurs.

---

## 8. Delegation Protocol

The Maestro emits delegation instructions in the following format:

```
del T{id} {tier} {action} {target} :: {spec}
```

The dispatcher parses delegation lines, resolves the tier to the configured
worker agent, and executes. Workers receive:
1. The core glossary (cached prefix)
2. The worker role prompt (cached prefix)
3. The specific task capsule (single turn, no history)

Workers MUST respond with a capsule in the standard format. The dispatcher
validates the capsule and injects the result back to the Maestro as the next
turn.

---

## 9. What This Specification Does Not Claim

- This spec does not claim zero-knowledge.
- This spec does not claim providers cannot correlate sessions via metadata.
- Level 2 privacy requires local Maestro deployment; it is not achieved by
  capsule compression alone.
- The XOR/base64 cipher in v0.5 is a lightweight protocol envelope, not
  enterprise-grade cryptography.
- Capsule compression is obfuscation, not confidentiality.

---

## 10. Prior Art Statement

This specification was first published on 2026-05-04T16:02:20Z via commit to
https://github.com/Rudekwydra/burnless. The architecture described herein —
specifically the three-component separation (Encoder / Maestro / Worker), the
capsule format, the shared prefix cache architecture, and the privacy-by-
architecture model — was designed and implemented by Roberto Wydra, operating
as RudekWydra, between April and May 2026.

The reference implementation is available under the MIT License.

---

## 11. References

- Reference implementation: https://github.com/Rudekwydra/burnless
- Benchmark: `bench/run.py` (reproducible, no mocks)
- Cost derivation: `MATH.md`
- Architecture details: `PROTOCOL.md`
- Vision: `VISION.md`
