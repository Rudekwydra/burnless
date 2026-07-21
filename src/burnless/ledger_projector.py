"""Pure, offline-testable projector: usage_event/v1 ledger -> LedgerSnapshot.

No I/O, no subprocess, no LLM calls, no network inside project(). See
docs/plans/2026-07-21-M2a-accounting-design.md ("Projector" section) for the
design this implements literally.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

from . import pricing

_EXCLUDED_SOURCES = {"raw_logs_isolated"}


@dataclass(frozen=True)
class Window:
    start_ts: str | None = None  # ISO-8601 UTC string, inclusive lower bound; None = unbounded
    end_ts: str | None = None    # ISO-8601 UTC string, exclusive upper bound; None = unbounded


@dataclass(frozen=True)
class LedgerSnapshot:
    window: Window | None
    pricing_version: str
    spend_usd: float
    spend_tokens: dict          # {"input": int, "output": int, "cache_read": int, "cache_write": int}
    saving_tokens: int
    saving_usd: float
    accounted_total_tokens: int
    monetizable_tokens: int
    excluded_categories: list   # list of (source: str, tokens: int) tuples, sorted by source name
    by_source: dict             # {source: tokens}
    encoder_calls: int
    decoder_calls: int
    brain_calls: int
    counts: dict                # {"spend_events": int, "saving_events": int}
    last_event_id: str | None
    last_offset: int
    ledger_sha256: str
    warnings: list              # list[str]


def read_ledger(path: Path) -> Iterator[dict]:
    """Tolerant line-by-line JSONL reader. Corrupted/non-JSON lines are silently skipped
    (they never reach project(), so project()'s own warnings list only covers semantically
    invalid *parsed* events, not JSON parse errors). Missing file -> empty iterator."""
    p = Path(path)
    if not p.exists():
        return
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(obj, dict):
                yield obj


def resolve_basis(event: dict, field: str) -> str:
    """Precedence: observed > provider_reported > tokenizer > chars.

    The event only ever carries one basis label per field (writers pick the
    strongest basis they actually have), so this is a lookup with a "chars"
    default, not a comparison across candidates.
    """
    basis = event.get("basis") if isinstance(event, dict) else None
    if isinstance(basis, dict):
        value = basis.get(field)
        if value:
            return value
    return "chars"


def _in_window(ts, window: Window | None) -> bool:
    if window is None or ts is None:
        return True
    if window.start_ts is not None and ts < window.start_ts:
        return False
    if window.end_ts is not None and ts >= window.end_ts:
        return False
    return True


def _bump_derived_call_counts(source, counts):
    if source == "capsule_compression":
        counts["encoder_calls"] += 1
    elif source == "output_decompression_avoided":
        counts["decoder_calls"] += 1
    elif source == "repeated_context_avoided":
        counts["brain_calls"] += 1


def project(events: Iterable[dict], *, window: Window | None = None,
            pricing_version: str = "2026-01") -> LedgerSnapshot:
    events_list = list(events)

    spend_tokens = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    spend_usd = 0.0
    saving_tokens = 0
    by_source: dict = {}
    accounted_total_tokens = 0
    call_counts = {"encoder_calls": 0, "decoder_calls": 0, "brain_calls": 0}
    spend_events = 0
    saving_events = 0
    last_event_id = None
    warnings: list = []

    for event in events_list:
        if not isinstance(event, dict):
            warnings.append(f"non-dict event: {event!r}")
            continue

        is_legacy = event.get("schema") != "usage_event/v1"

        if is_legacy:
            if not _in_window(event.get("ts"), window):
                continue
            source = event.get("source")
            amount = int(event.get("amount", 0) or 0)
            saving_tokens += amount
            by_source[source] = by_source.get(source, 0) + amount
            accounted_total_tokens += amount
            saving_events += 1
            _bump_derived_call_counts(source, call_counts)
            continue

        kind = event.get("kind")
        if not _in_window(event.get("ts"), window):
            continue

        if kind == "spend":
            tokens = event.get("tokens") or {}
            for tfield in ("input", "output", "cache_read", "cache_write"):
                spend_tokens[tfield] += int(tokens.get(tfield, 0) or 0)
            cost = event.get("cost") or {}
            spend_usd += float(cost.get("usd", 0) or 0)
            spend_events += 1
            event_id = event.get("event_id")
            if event_id:
                last_event_id = event_id
        elif kind == "saving":
            source = event.get("source")
            amount = int(event.get("amount", 0) or 0)
            saving_tokens += amount
            by_source[source] = by_source.get(source, 0) + amount
            accounted_total_tokens += amount
            saving_events += 1
            _bump_derived_call_counts(source, call_counts)
            event_id = event.get("event_id")
            if event_id:
                last_event_id = event_id
        else:
            warnings.append(f"unrecognized kind: {kind!r}")
            continue

    excluded_categories = sorted(
        (source, tokens) for source, tokens in by_source.items()
        if source in _EXCLUDED_SOURCES and tokens
    )
    monetizable_tokens = accounted_total_tokens - sum(tokens for _, tokens in excluded_categories)
    saving_usd = saving_tokens * pricing.rate_versioned("opus", "input", pricing_version)

    payload = (
        spend_tokens, spend_usd, saving_tokens, by_source, accounted_total_tokens,
        monetizable_tokens, excluded_categories,
        call_counts["encoder_calls"], call_counts["decoder_calls"], call_counts["brain_calls"],
    )
    ledger_sha256 = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

    return LedgerSnapshot(
        window=window,
        pricing_version=pricing_version,
        spend_usd=spend_usd,
        spend_tokens=spend_tokens,
        saving_tokens=saving_tokens,
        saving_usd=saving_usd,
        accounted_total_tokens=accounted_total_tokens,
        monetizable_tokens=monetizable_tokens,
        excluded_categories=excluded_categories,
        by_source=by_source,
        encoder_calls=call_counts["encoder_calls"],
        decoder_calls=call_counts["decoder_calls"],
        brain_calls=call_counts["brain_calls"],
        counts={"spend_events": spend_events, "saving_events": saving_events},
        last_event_id=last_event_id,
        last_offset=len(events_list),
        ledger_sha256=ledger_sha256,
        warnings=warnings,
    )
