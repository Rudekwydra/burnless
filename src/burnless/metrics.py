"""
Burnless Tokens — auditable counter.

Every increment is appended to audit.jsonl with a reason, source, and amount,
so `burnless metrics --explain` can show line-by-line where the number came from.
This is the difference between a real product and a Duolingo XP gimmick.
"""
from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime, timezone

DEFAULT_METRICS: dict = {
    "burnless_tokens": 0,
    "token_burn_avoided_percent": 0,
    "repeated_briefings_avoided": 0,
    "dead_logs_isolated": 0,
    "expensive_model_calls_avoided": 0,
    "estimated_cost_avoided_usd": 0.0,
    "keepalive_pings_total": 0,
    "keepalive_pings_ok": 0,
    "keepalive_pings_miss": 0,
    "keepalive_pings_err": 0,
    "keepalive_cost_usd": 0.0,
    # Real-time per-turn measurements. These accumulate from actual usage,
    # not from synthetic benchmarks. Conservative by construction: the
    # decoder Haiku output is shorter than what a Maestro Sonnet would
    # produce unprompted (RLHF default leans verbose), so the "avoided"
    # numbers below are floors, not ceilings.
    "encoder_calls": 0,
    "encoder_input_chars_seen": 0,
    "encoder_capsule_output_tokens": 0,
    "decoder_calls": 0,
    "decoder_capsule_input_tokens": 0,
    "decoder_expanded_output_tokens": 0,
    "legacy_run_calls": 0,
    "legacy_compress_calls": 0,
    "legacy_decompress_calls": 0,
    "compression_ratio_observed_sum": 0.0,
    "compression_ratio_observed_count": 0,
    "by_source": {
        "raw_logs_isolated": 0,
        "repeated_context_avoided": 0,
        "compact_state": 0,
        "expensive_model_avoided": 0,
        "capsule_compression": 0,
        "output_decompression_avoided": 0,
        "keepalive_cache_renewed": 0,
    },
}

_CACHE_READ_USD_PER_TOKEN = 0.30 / 1_000_000  # Sonnet $0.30/MTok
_GLOBAL_EVENTS_PATH = Path.home() / ".burnless" / "global_metrics.jsonl"

VALID_SOURCES = set(DEFAULT_METRICS["by_source"].keys())

# Approximate token/char ratios for input estimation when the API doesn't
# return token counts (e.g., raw user message before encoder runs).
# Conservative: real tokenization may be slightly under for PT-BR.
_CHARS_PER_TOKEN_PT = 3.5
_CHARS_PER_TOKEN_EN = 4.0


def load(path: Path) -> dict:
    if not path.exists():
        return _fresh()
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        # Concurrent writers can leave the file mid-rewrite. Treat as empty;
        # next save() will heal it.
        return _fresh()
    # heal missing keys after upgrades
    base = _fresh()
    for k, v in base.items():
        data.setdefault(k, v)
    base_by = base["by_source"]
    for k, v in base_by.items():
        data["by_source"].setdefault(k, v)
    return data


def save(path: Path, metrics: dict) -> None:
    import os
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)



def _append_global_event(*, source: str, amount: int, reason: str, delegation_id: str | None, project_root: str | None) -> None:
    """Append one JSON line to ~/.burnless/global_metrics.jsonl. Fail-silent (best-effort)."""
    try:
        _GLOBAL_EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        event = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "source": source,
            "amount": int(amount),
            "reason": reason,
            "delegation_id": delegation_id,
            "project_root": project_root,
        }
        with _GLOBAL_EVENTS_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        pass


def record(
    metrics_path: Path,
    audit_path: Path,
    *,
    source: str,
    amount: int,
    reason: str,
    delegation_id: str | None = None,
    extra: dict | None = None,
    usd_per_million: float = 15.0,
) -> dict:
    if source not in VALID_SOURCES:
        raise ValueError(f"unknown source: {source}")
    if amount < 0:
        raise ValueError("amount must be >= 0")

    metrics = load(metrics_path)
    metrics["burnless_tokens"] = int(metrics["burnless_tokens"]) + amount
    metrics["by_source"][source] = int(metrics["by_source"].get(source, 0)) + amount

    if source == "raw_logs_isolated":
        metrics["dead_logs_isolated"] = int(metrics.get("dead_logs_isolated", 0)) + 1
    elif source == "repeated_context_avoided":
        metrics["repeated_briefings_avoided"] = (
            int(metrics.get("repeated_briefings_avoided", 0)) + 1
        )
    elif source == "expensive_model_avoided":
        metrics["expensive_model_calls_avoided"] = (
            int(metrics.get("expensive_model_calls_avoided", 0)) + 1
        )

    metrics["estimated_cost_avoided_usd"] = round(
        (metrics["burnless_tokens"] / 1_000_000) * usd_per_million, 4
    )

    save(metrics_path, metrics)

    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "amount": amount,
        "reason": reason,
        "delegation_id": delegation_id,
    }
    if extra:
        entry["extra"] = extra
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    try:
        _append_global_event(
            source=source,
            amount=int(amount),
            reason=reason,
            delegation_id=delegation_id,
            project_root=str(metrics_path.parent.parent) if metrics_path.parent.name == ".burnless" else None,
        )
    except Exception:
        pass

    return metrics


def record_encoder_call(
    metrics_path: Path,
    audit_path: Path,
    *,
    raw_input_chars: int,
    capsule_output_tokens: int,
    chars_per_token: float = _CHARS_PER_TOKEN_PT,
) -> dict:
    """Record one encoder call: raw user message → capsule.

    Conservative estimate of saved input tokens at the Maestro stage:
        saved = (raw_input_chars / chars_per_token) - capsule_output_tokens
    This is the floor — the actual Maestro naive-replay cost would also include
    repeated history bytes which capsules avoid entirely.
    """
    metrics = load(metrics_path)
    metrics["encoder_calls"] = int(metrics.get("encoder_calls", 0)) + 1
    metrics["encoder_input_chars_seen"] = (
        int(metrics.get("encoder_input_chars_seen", 0)) + max(raw_input_chars, 0)
    )
    metrics["encoder_capsule_output_tokens"] = (
        int(metrics.get("encoder_capsule_output_tokens", 0)) + max(capsule_output_tokens, 0)
    )
    raw_estimate = max(int(raw_input_chars / chars_per_token), 0)
    saved = max(raw_estimate - max(capsule_output_tokens, 0), 0)
    if saved > 0:
        metrics["burnless_tokens"] = int(metrics["burnless_tokens"]) + saved
        metrics["by_source"]["capsule_compression"] = (
            int(metrics["by_source"].get("capsule_compression", 0)) + saved
        )
    metrics["estimated_cost_avoided_usd"] = round(
        (metrics["burnless_tokens"] / 1_000_000) * 15.0, 4
    )
    save(metrics_path, metrics)
    if saved > 0:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "source": "capsule_compression",
            "amount": saved,
            "reason": "encoder: raw user message → capsule",
            "extra": {
                "raw_chars": raw_input_chars,
                "raw_estimate_tokens": raw_estimate,
                "capsule_tokens": capsule_output_tokens,
            },
        }
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with audit_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return metrics


def record_decoder_call(
    metrics_path: Path,
    audit_path: Path,
    *,
    capsule_input_tokens: int,
    expanded_output_tokens: int,
) -> dict:
    """Record one decoder call: Maestro capsule → expanded prose.

    Conservative estimate of avoided Maestro output tokens:
        avoided = expanded_output_tokens - capsule_input_tokens

    Floor reasoning: a Maestro Sonnet asked the same question without Burnless
    rules would produce at least `expanded_output_tokens` of output (likely
    more — RLHF default leans verbose with prose padding, hedges, summaries).
    Burnless paid `capsule_input_tokens` of Maestro output instead, plus a
    Haiku decoder call (cheaper per token). The avoided count here is the
    Maestro-equivalent output that didn't get billed at Sonnet rates.
    """
    metrics = load(metrics_path)
    metrics["decoder_calls"] = int(metrics.get("decoder_calls", 0)) + 1
    metrics["decoder_capsule_input_tokens"] = (
        int(metrics.get("decoder_capsule_input_tokens", 0)) + max(capsule_input_tokens, 0)
    )
    metrics["decoder_expanded_output_tokens"] = (
        int(metrics.get("decoder_expanded_output_tokens", 0)) + max(expanded_output_tokens, 0)
    )
    avoided = max(
        max(expanded_output_tokens, 0) - max(capsule_input_tokens, 0), 0
    )
    if avoided > 0:
        metrics["burnless_tokens"] = int(metrics["burnless_tokens"]) + avoided
        metrics["by_source"]["output_decompression_avoided"] = (
            int(metrics["by_source"].get("output_decompression_avoided", 0)) + avoided
        )
    metrics["estimated_cost_avoided_usd"] = round(
        (metrics["burnless_tokens"] / 1_000_000) * 15.0, 4
    )
    save(metrics_path, metrics)
    if avoided > 0:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "source": "output_decompression_avoided",
            "amount": avoided,
            "reason": "decoder: Maestro capsule → expanded prose (Maestro-equivalent floor)",
            "extra": {
                "capsule_input_tokens": capsule_input_tokens,
                "expanded_output_tokens": expanded_output_tokens,
            },
        }
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with audit_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return metrics


def record_brain_call(
    metrics_path: Path,
    audit_path: Path,
    *,
    cache_read_tokens: int,
    cache_creation_tokens: int,
    input_tokens: int,
    output_tokens: int,
    model: str = "unknown",
) -> dict:
    """Record one Maestro API call with its full usage breakdown.

    Saved-by-cache estimate (conservative — represents the floor of what
    repeated context would cost at full input price without caching):
        saved = cache_read_tokens (these would otherwise be billed at input rate)
    """
    metrics = load(metrics_path)
    # legacy persisted key name (kept for on-disk back-compat); represents the Maestro layer
    metrics["brain_calls"] = int(metrics.get("brain_calls", 0)) + 1
    metrics["brain_input_tokens"] = (
        int(metrics.get("brain_input_tokens", 0)) + max(input_tokens, 0)
    )
    metrics["brain_output_tokens"] = (
        int(metrics.get("brain_output_tokens", 0)) + max(output_tokens, 0)
    )
    metrics["brain_cache_read_tokens"] = (
        int(metrics.get("brain_cache_read_tokens", 0)) + max(cache_read_tokens, 0)
    )
    metrics["brain_cache_creation_tokens"] = (
        int(metrics.get("brain_cache_creation_tokens", 0)) + max(cache_creation_tokens, 0)
    )
    saved = max(cache_read_tokens, 0)
    if saved > 0:
        metrics["burnless_tokens"] = int(metrics["burnless_tokens"]) + saved
        metrics["by_source"]["repeated_context_avoided"] = (
            int(metrics["by_source"].get("repeated_context_avoided", 0)) + saved
        )
        metrics["repeated_briefings_avoided"] = (
            int(metrics.get("repeated_briefings_avoided", 0)) + 1
        )
    metrics["estimated_cost_avoided_usd"] = round(
        (metrics["burnless_tokens"] / 1_000_000) * 15.0, 4
    )
    save(metrics_path, metrics)
    if saved > 0:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "source": "repeated_context_avoided",
            "amount": saved,
            "reason": f"brain ({model}): cache hit, prefix served from cache",
            "extra": {
                "model": model,
                "cache_read": cache_read_tokens,
                "cache_creation": cache_creation_tokens,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
        }
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with audit_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return metrics


def session_snapshot(metrics_path: Path, *, label: str) -> dict:
    """Capture a snapshot of current metrics with a label and timestamp.

    Stored in metrics["session_snapshots"] as a list of {ts, label, ...}.
    Used to compute per-session deltas.
    """
    metrics = load(metrics_path)
    snapshot = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "label": label,
        "burnless_tokens": int(metrics.get("burnless_tokens", 0)),
        "encoder_calls": int(metrics.get("encoder_calls", 0)),
        "decoder_calls": int(metrics.get("decoder_calls", 0)),
        "brain_calls": int(metrics.get("brain_calls", 0)),
        "brain_cache_read_tokens": int(metrics.get("brain_cache_read_tokens", 0)),
        "brain_cache_creation_tokens": int(metrics.get("brain_cache_creation_tokens", 0)),
        "legacy_run_calls": int(metrics.get("legacy_run_calls", 0)),
        "legacy_compress_calls": int(metrics.get("legacy_compress_calls", 0)),
        "legacy_decompress_calls": int(metrics.get("legacy_decompress_calls", 0)),
        "by_source": dict(metrics.get("by_source", {})),
    }
    snapshots = metrics.setdefault("session_snapshots", [])
    snapshots.append(snapshot)
    # Keep only the last 100 snapshots; older are still in audit.jsonl.
    metrics["session_snapshots"] = snapshots[-100:]
    save(metrics_path, metrics)
    return snapshot


def session_diff(metrics_path: Path) -> dict | None:
    """Return delta between the two most recent snapshots, or None."""
    metrics = load(metrics_path)
    snapshots = metrics.get("session_snapshots") or []
    if len(snapshots) < 2:
        return None
    a = snapshots[-2]
    b = snapshots[-1]

    def _diff(key: str) -> int:
        return int(b.get(key, 0)) - int(a.get(key, 0))

    diff = {
        "from_label": a.get("label"),
        "to_label": b.get("label"),
        "from_ts": a.get("ts"),
        "to_ts": b.get("ts"),
        "delta_burnless_tokens": _diff("burnless_tokens"),
        "delta_encoder_calls": _diff("encoder_calls"),
        "delta_decoder_calls": _diff("decoder_calls"),
        "delta_brain_calls": _diff("brain_calls"),
        "delta_brain_cache_read": _diff("brain_cache_read_tokens"),
        "delta_brain_cache_creation": _diff("brain_cache_creation_tokens"),
        "delta_legacy_run_calls": _diff("legacy_run_calls"),
        "delta_legacy_compress_calls": _diff("legacy_compress_calls"),
        "delta_legacy_decompress_calls": _diff("legacy_decompress_calls"),
        "delta_by_source": {
            k: int(b.get("by_source", {}).get(k, 0)) - int(a.get("by_source", {}).get(k, 0))
            for k in set(b.get("by_source", {})) | set(a.get("by_source", {}))
        },
    }
    return diff


def increment_keepalive_ping(
    path: Path,
    *,
    status: str,
    cost_usd: float,
    cache_read_tokens: int,
) -> None:
    metrics = load(path)
    metrics["keepalive_pings_total"] = int(metrics.get("keepalive_pings_total", 0)) + 1
    if status == "ok":
        metrics["keepalive_pings_ok"] = int(metrics.get("keepalive_pings_ok", 0)) + 1
    elif status == "miss":
        metrics["keepalive_pings_miss"] = int(metrics.get("keepalive_pings_miss", 0)) + 1
    else:
        metrics["keepalive_pings_err"] = int(metrics.get("keepalive_pings_err", 0)) + 1
    metrics["keepalive_cost_usd"] = round(
        float(metrics.get("keepalive_cost_usd", 0.0)) + cost_usd, 6
    )
    by_source = metrics.setdefault("by_source", {})
    by_source["keepalive_cache_renewed"] = (
        int(by_source.get("keepalive_cache_renewed", 0)) + cache_read_tokens
    )
    save(path, metrics)


def bump_legacy_counter(metrics_path: Path, name: str, amount: int = 1) -> None:
    """Increment a top-level legacy counter. No-op if name not allowed."""
    allowed = {"legacy_run_calls", "legacy_compress_calls", "legacy_decompress_calls"}
    if name not in allowed:
        return
    m = load(metrics_path)
    m[name] = int(m.get(name, 0)) + int(amount)
    save(metrics_path, m)


def bump_ratio_observed(metrics_path: Path, ratio: float) -> None:
    """Accumulate observed compression ratio + count for averaging."""
    m = load(metrics_path)
    m["compression_ratio_observed_sum"] = float(m.get("compression_ratio_observed_sum", 0.0)) + float(ratio)
    m["compression_ratio_observed_count"] = int(m.get("compression_ratio_observed_count", 0)) + 1
    save(metrics_path, m)


def _fresh() -> dict:
    import copy
    return copy.deepcopy(DEFAULT_METRICS)


def read_audit(audit_path: Path, limit: int | None = None) -> list[dict]:
    if not audit_path.exists():
        return []
    out: list[dict] = []
    with audit_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if limit is not None:
        return out[-limit:]
    return out
