"""Append-only capsule spine — the rolling-memory transform.

Doctrine: an exchange the model already produced and the user already read is
carried forward as concept, not verbatim. Each completed exchange is
compressed exactly once (keyed by content hash), then frozen forever. The
spine grows only by appending at its end, so the serialized request prefix
stays byte-stable across turns and the provider prompt cache keeps hitting.

Pure module: the only I/O happens through the store object passed in
(get_capsule / put_original / get_original). Fail-open by construction — any
surprise returns the request body untouched.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

SPINE_HEADER = (
    "[burnless spine] Earlier conversation, already processed by both sides, "
    "carried as one frozen capsule per exchange. The full verbatim of any "
    "capsule is on disk — recover it with `burnless retrieve <ref>` (or "
    "`burnless proxy show <ref>`) only if a capsule is genuinely "
    "insufficient.\n"
)
SPINE_ACK = "[burnless spine] Acknowledged. Continuing with full awareness of the exchanges above."

# Approximate token cost of the two injected spine messages (roles, braces,
# header). Substitution must beat the verbatim span by at least this margin.
_INJECT_OVERHEAD_TOKENS = 60


def _canon(obj: Any) -> Any:
    """Content identity: strip cache_control everywhere. Clients roll their
    breakpoints forward as the conversation grows, so the same logical
    exchange must hash the same with or without the marker."""
    if isinstance(obj, dict):
        return {k: _canon(v) for k, v in obj.items() if k != "cache_control"}
    if isinstance(obj, list):
        return [_canon(x) for x in obj]
    return obj


def _is_real_user(msg: Any) -> bool:
    """A message that starts a new exchange: user role carrying NO tool_result
    block. Claude Code routinely packs tool_result together with text blocks
    (system reminders, interrupt notices) in one user message — any
    tool_result at all means the agentic loop of the previous exchange is
    still in flight, and splitting there would orphan the tool_use pair."""
    if not isinstance(msg, dict) or msg.get("role") != "user":
        return False
    content = msg.get("content")
    if isinstance(content, str):
        return True
    if isinstance(content, list):
        return not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
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
    canonical = json.dumps(_canon(exchange), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def approx_tokens(text: str) -> int:
    """Cheap provider-agnostic estimate: ~4 ASCII chars per token, ~1 token
    per non-ASCII char. Both sides of every shrink decision use this, so the
    units can never disagree the way raw len() vs escaped JSON did."""
    non_ascii = sum(1 for ch in text if ord(ch) > 127)
    return (len(text) - non_ascii) // 4 + non_ascii


def _span_tokens(exchanges: list[list]) -> int:
    return approx_tokens(json.dumps(_canon(exchanges), separators=(",", ":"), ensure_ascii=False))


def _count_cache_controls(*parts: Any) -> int:
    n = 0
    for part in parts:
        if isinstance(part, list):
            for item in part:
                if isinstance(item, dict):
                    if item.get("cache_control"):
                        n += 1
                    content = item.get("content")
                    if isinstance(content, list):
                        n += sum(1 for b in content if isinstance(b, dict) and b.get("cache_control"))
    return n


def transform(
    body: dict,
    store: Any,
    *,
    keep_tail_exchanges: int = 1,
    min_exchanges: int = 3,
    cache_breakpoint: bool = True,
    absorb_batch: int = 4,
) -> tuple[dict, list[str], dict]:
    """Rewrite body["messages"] around the capsule spine.

    Returns (new_body, pending_hashes, stats). pending_hashes are exchanges
    whose originals were persisted but whose capsules do not exist yet — the
    caller queues them for background compression. On any surprise the
    original body comes back untouched (stats["applied"] is False).
    """
    try:
        return _transform(
            body, store, keep_tail_exchanges, min_exchanges, cache_breakpoint, absorb_batch
        )
    except Exception as exc:  # fail-open: the proxy must never break a conversation
        return body, [], {"applied": False, "reason": f"error:{type(exc).__name__}"}


def _transform(
    body: dict,
    store: Any,
    keep_tail: int,
    min_exchanges: int,
    cache_breakpoint: bool,
    absorb_batch: int = 4,
):
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
            store.put_original(h, _canon(exchange))
            pending.append(h)

    if len(completed) < min_exchanges or not compressible:
        return body, pending, {"applied": False, "reason": "below_regime"}

    # Prefix absorption: take the longest run of leading exchanges whose
    # capsules exist. The first missing capsule stops the spine — order is
    # preserved and the spine prefix stays append-only. An exchange is only
    # absorbed once its verbatim is recoverable (unless the operator chose
    # raw_retention: none — then the capsule is deliberately all that exists).
    require_original = getattr(store, "raw_retention", "plain") != "none"
    absorbed: list[str] = []
    absorbed_count = 0
    for exchange, h in zip(compressible, hashes):
        capsule = store.get_capsule(h)
        if not capsule:
            break
        if require_original and store.get_original(h) is None:
            store.put_original(h, _canon(exchange))
            if store.get_original(h) is None:
                break
        line = capsule.strip()
        if f"[ref:{h}]" not in line:
            line = f"{line} [ref:{h}]"
        absorbed.append(line)
        absorbed_count += 1

    # Batched absorption. Growing the spine by one capsule per turn is a
    # measured loss: the capsule lands BEFORE the verbatim tail, so the whole
    # tail is cache-written again every turn, while a plain client only ever
    # appends its delta. Quantizing the spine to multiples of `absorb_batch`
    # keeps the request byte-identical between batches — pure append, same
    # write behavior as the baseline — and pays the prefix rewrite once per
    # batch instead of once per turn. Deterministic in the history alone, so
    # the depth never depends on which capsule happened to finish first.
    if not absorbed:
        return body, pending, {"applied": False, "reason": "no_capsules_yet"}

    batch = max(1, int(absorb_batch))
    absorbed_count = (absorbed_count // batch) * batch
    absorbed = absorbed[:absorbed_count]
    if not absorbed:
        return body, pending, {"applied": False, "reason": "batch_not_full"}

    span = compressible[:absorbed_count]
    spine_text = SPINE_HEADER + "\n".join(absorbed)
    saved_tokens = _span_tokens(span) - approx_tokens(spine_text) - _INJECT_OVERHEAD_TOKENS
    if saved_tokens <= 0:
        return body, pending, {"applied": False, "reason": "not_smaller"}

    # One content block per capsule, appended in order. Earlier blocks stay
    # byte-identical across turns, so the provider's breakpoint from the
    # previous request still hits and only the delta is cache-written. The
    # ephemeral marker rides the LAST capsule block (metadata, not content —
    # moving it does not invalidate the matched prefix).
    spine_blocks: list[dict] = [{"type": "text", "text": SPINE_HEADER}]
    spine_blocks += [{"type": "text", "text": line} for line in absorbed]

    remaining = [m for ex in completed[absorbed_count:] for m in ex]
    # Breakpoint budget is counted on what actually ships — absorbed messages
    # take their markers out of the request with them.
    marked = False
    if cache_breakpoint and _count_cache_controls(body.get("system"), body.get("tools"), remaining + tail) < 4:
        spine_blocks[-1] = dict(spine_blocks[-1], cache_control={"type": "ephemeral"})
        marked = True

    new_messages = (
        [
            {"role": "user", "content": spine_blocks},
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
        "tokens_saved_approx": saved_tokens,
        "cache_breakpoint": marked,
    }
    return new_body, pending, stats
