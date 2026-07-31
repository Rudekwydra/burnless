"""Append-only capsule spine — the rolling-memory transform.

Doctrine: an exchange the model already produced and the user already read is
carried forward as concept, not verbatim. Each completed exchange is
compressed exactly once (keyed by content hash), then frozen forever. The
spine grows only by appending at its end, so the serialized request prefix
stays byte-stable across turns and the provider prompt cache keeps hitting.

Pure module: the only I/O happens through the store object passed in
(get_capsule / put_original). Fail-open by construction — any surprise
returns the request body untouched.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

SPINE_HEADER = (
    "[burnless spine] Earlier conversation, already processed by both sides, "
    "carried as one frozen capsule per exchange. Full verbatim of any capsule "
    "is on disk at .burnless/proxy/exchanges/<ref>.json — ask the operator to "
    "retrieve a ref only if a capsule is genuinely insufficient.\n"
)
SPINE_ACK = "[burnless spine] Acknowledged. Continuing with full awareness of the exchanges above."

# Serialization overhead of the two injected spine messages (roles, braces…).
# Substitution must beat the verbatim span by at least this margin.
_INJECT_OVERHEAD = 160


def _is_real_user(msg: Any) -> bool:
    """A message that starts a new exchange: user role with any non-tool_result
    content. A user message that is purely tool_result blocks belongs to the
    agentic loop of the exchange already in flight."""
    if not isinstance(msg, dict) or msg.get("role") != "user":
        return False
    content = msg.get("content")
    if isinstance(content, str):
        return True
    if isinstance(content, list):
        for block in content:
            if not (isinstance(block, dict) and block.get("type") == "tool_result"):
                return True
        return False
    return False


def split_exchanges(messages: list) -> tuple[list[list], list]:
    """Split a messages array into (completed_exchanges, tail).

    An exchange starts at a real user message and runs until the message
    before the next real user message (tool loops included). The tail is the
    final span — the current prompt and anything in flight after it.
    Returns ([], messages) whenever the shape is not the one we understand.
    """
    starts = [i for i, m in enumerate(messages) if _is_real_user(m)]
    if not starts or starts[0] != 0:
        return [], list(messages)
    spans: list[list] = []
    for j, s in enumerate(starts):
        end = starts[j + 1] if j + 1 < len(starts) else len(messages)
        spans.append(messages[s:end])
    return spans[:-1], spans[-1]


def exchange_hash(exchange: list) -> str:
    """Deterministic content identity for one exchange (12 hex chars).

    Same exchange → same hash in any request, parallel window, or replay;
    this is what removes the need for session tracking."""
    canonical = json.dumps(exchange, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def _span_chars(exchanges: list[list]) -> int:
    return len(json.dumps(exchanges, separators=(",", ":"), ensure_ascii=True))


def transform(
    body: dict,
    store: Any,
    *,
    keep_tail_exchanges: int = 1,
    min_exchanges: int = 3,
) -> tuple[dict, list[str], dict]:
    """Rewrite body["messages"] around the capsule spine.

    Returns (new_body, pending_hashes, stats). pending_hashes are exchanges
    whose originals were persisted but whose capsules do not exist yet — the
    caller queues them for background compression. On any surprise the
    original body comes back untouched (stats["applied"] is False).
    """
    try:
        return _transform(body, store, keep_tail_exchanges, min_exchanges)
    except Exception as exc:  # fail-open: the proxy must never break a conversation
        return body, [], {"applied": False, "reason": f"error:{type(exc).__name__}"}


def _transform(body: dict, store: Any, keep_tail: int, min_exchanges: int):
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        return body, [], {"applied": False, "reason": "no_messages"}

    completed, tail = split_exchanges(messages)
    keep_tail = max(0, int(keep_tail))
    compressible = completed[: len(completed) - keep_tail] if keep_tail else list(completed)

    # Persist originals and queue capsules even before the regime engages, so
    # they are ready the moment it does. Compression never blocks a request.
    pending: list[str] = []
    hashes: list[str] = []
    for exchange in compressible:
        h = exchange_hash(exchange)
        hashes.append(h)
        if store.get_capsule(h) is None:
            store.put_original(h, exchange)
            pending.append(h)

    if len(completed) < min_exchanges or not compressible:
        return body, pending, {"applied": False, "reason": "below_regime"}

    # Prefix absorption: take the longest run of leading exchanges whose
    # capsules exist. The first missing capsule stops the spine — order is
    # preserved and the spine prefix stays append-only.
    absorbed: list[str] = []
    absorbed_count = 0
    for exchange, h in zip(compressible, hashes):
        capsule = store.get_capsule(h)
        if not capsule:
            break
        line = capsule.strip()
        if f"[ref:{h}]" not in line:
            line = f"{line} [ref:{h}]"
        absorbed.append(line)
        absorbed_count += 1

    if not absorbed:
        return body, pending, {"applied": False, "reason": "no_capsules_yet"}

    spine_text = SPINE_HEADER + "\n".join(absorbed)
    span = compressible[:absorbed_count]
    saved = _span_chars(span) - len(spine_text) - _INJECT_OVERHEAD
    if saved <= 0:
        return body, pending, {"applied": False, "reason": "not_smaller"}

    remaining = [m for ex in completed[absorbed_count:] for m in ex]
    new_messages = (
        [
            {"role": "user", "content": spine_text},
            {"role": "assistant", "content": SPINE_ACK},
        ]
        + remaining
        + tail
    )
    new_body = dict(body)
    new_body["messages"] = new_messages
    stats = {
        "applied": True,
        "absorbed": absorbed_count,
        "verbatim_exchanges": len(completed) - absorbed_count,
        "chars_saved": saved,
    }
    return new_body, pending, stats
