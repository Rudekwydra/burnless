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
    "by_source": {
        "raw_logs_isolated": 0,
        "repeated_context_avoided": 0,
        "compact_state": 0,
        "expensive_model_avoided": 0,
        "capsule_compression": 0,
        "keepalive_cache_renewed": 0,
    },
}

_CACHE_READ_USD_PER_TOKEN = 0.30 / 1_000_000  # Sonnet $0.30/MTok

VALID_SOURCES = set(DEFAULT_METRICS["by_source"].keys())


def load(path: Path) -> dict:
    if not path.exists():
        return _fresh()
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    # heal missing keys after upgrades
    base = _fresh()
    for k, v in base.items():
        data.setdefault(k, v)
    base_by = base["by_source"]
    for k, v in base_by.items():
        data["by_source"].setdefault(k, v)
    return data


def save(path: Path, metrics: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2, ensure_ascii=False)


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

    return metrics


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
