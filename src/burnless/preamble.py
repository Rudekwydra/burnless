"""Shared cacheable preamble — the byte-identical anchor for warm prompt-cache.

Single source of the cacheable prefix served (eventually) to encoder + maestro +
worker of the same model. build_shared_preamble() MUST return byte-identical output
across calls and releases, or every layer's prompt cache invalidates. Do NOT inject
runtime/dynamic content (timestamps, cwd, tenant data). MORAL_BLOCK is frozen static
for the same reason.

STEP 1 (2026-05-29): glossary core + pad to clear the Haiku 2048-token cache floor.
MORAL_BLOCK is an empty placeholder until the frozen anti-RLHF text is supplied.
"""
from __future__ import annotations

from .codec.glossary_loader import load_glossary
from .estimator import estimate_tokens

CACHE_FLOOR_TOKENS = 2048  # Haiku prompt-cache minimum (Sonnet/Opus floor = 1024)

# TODO(roberto): frozen anti-RLHF moral block. Empty for now — 2048 floor met by
# glossary + _PAD. Filling this later is a deliberate one-time cache invalidation.
MORAL_BLOCK = ""

# Frozen pad guaranteeing the preamble clears the Haiku floor. COPY-VERBATIM the exact
# multi-line string literal currently assigned to `_CACHE_PAD` in
# src/burnless/chat_mode.py (read that file, copy the literal byte-for-byte). Never vary it.
_PAD = """

[burnless-protocol-extended-reference]

ARCHITECTURE
  User → Encoder LLM → Encoder Software → Maestro → Workers (gold/silver/bronze)
       → Decoder Software → Decoder LLM → User

  Encoder: Translates raw natural language to compact capsule format (~80 chars per turn).
           Default: cloud LLM (Haiku-class). Privacy alternative: local model (Ollama).

  Maestro: The persistent orchestrating agent. Receives ONLY capsules — never raw text.
           Maintains session state as a capsule history. Decides: respond directly |
           delegate to worker | ask for clarification. NEVER executes commands directly.

  Workers: Ephemeral execution agents. Receive a single task capsule with no conversation
           history. Three quality/cost tiers configurable by the user.

  Decoder: Translates capsule results back to natural language for the user.
           Default: cloud LLM (Haiku-class). Privacy alternative: local model (Ollama).

CAPSULE FORMAT
  {tier} {action} {target} :: {status} {detail} [ref:{exec_id}]

  Examples:
    gld imp auth/jwt :: OK schema+router+middleware done [ref:exec/T0042]
    slv doc api/     :: PART openapi.yaml done, examples pending [ref:exec/T0043]
    brz sum logs/    :: OK 3 errors found, 2 warnings [ref:exec/T0044]

  Status values: OK | PART | BLK | ERR

DELEGATION FORMAT
  del T{id} {tier} {action} {target} :: {spec}

  The dispatcher parses delegation lines, resolves the tier to the configured worker agent,
  and executes. Workers receive: (1) core glossary cached prefix, (2) worker role prompt
  cached prefix, (3) specific task capsule — single turn, no history.

COST MODEL
  N  = turns in session
  P  = persistent prefix tokens (system prompt)
  C  ≈ 20 tokens = capsule size (~80 chars)
  T  ≈ 1500 tokens = typical raw turn size

  Standalone: cost ≈ N·P·p_in + T·N(N-1)/2·p_in → Θ(N²)
  Burnless:   cost ≈ P·p_cw + (N-1)·P·p_cr + C·N(N-1)/2·p_in → Θ(N)

  The capsule term C·N(N-1)/2 is technically Θ(N²) but with constant C/T ≈ 0.013
  (~75x smaller). For N ≤ 1000 it remains below the linear cache-read term.

PRIVACY LEVELS (architectural consequence, not a mode flag)
  L0: Encoder=cloud, Maestro=cloud, Workers=cloud → providers see everything
  L1: Encoder=local, Maestro=cloud, Workers=cloud → providers see capsules only
  L2: Encoder=local, Maestro=local, Workers=cloud → providers see disconnected fragments
  L3: Encoder=local, Maestro=local, Workers=local → providers see nothing

  Level 2 is the strongest practical configuration for most users.
  Level 3 is the only configuration with a hard privacy guarantee.

GLOSSARY LAYERS
  1. Core glossary — fixed protocol terms, versioned with spec. Byte-identical across
     all users. Eligible for shared prefix caching (this block).
  2. Tenant/project glossary — local domain language per project (tenant_glossary.yaml).
  3. Session emergent glossary — append-only mappings proposed by encoder, validated
     by Maestro before adoption. Survives compaction as GLOSSARY_SUPERBLOCK.

CACHE ARCHITECTURE
  The Maestro system prompt is byte-identical every turn → persistent prefix caching.
  Cache read price ≈ 10x cheaper than standard input (100x cheaper than write).
  Model switching within same provider does NOT invalidate cache.
  Provider switching resets cache.

This block is byte-identical every session — it is the shared cache anchor.
Modification at runtime invalidates caching for all active sessions.
"""


def build_shared_preamble() -> str:
    """Return the byte-identical shared cache anchor (>= CACHE_FLOOR_TOKENS)."""
    parts = [load_glossary().rstrip(), MORAL_BLOCK.strip()]
    base = "\n\n".join(p for p in parts if p)
    while estimate_tokens(base) < CACHE_FLOOR_TOKENS:
        base = base + "\n\n" + _PAD
    return base


def system_prompt_with_suffix(role_suffix: str) -> str:
    """CLI form: shared preamble + role suffix as one --system-prompt string."""
    return build_shared_preamble() + "\n\n" + role_suffix


_GUARD = build_shared_preamble()
assert estimate_tokens(_GUARD) >= CACHE_FLOOR_TOKENS, (
    f"shared preamble {estimate_tokens(_GUARD)} tok < floor {CACHE_FLOOR_TOKENS}"
)
