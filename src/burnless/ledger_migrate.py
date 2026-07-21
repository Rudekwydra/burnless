from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from . import metrics as metrics_mod


def migrate(project_root: Path) -> dict:
    """Idempotent one-time backfill: freeze metrics.json, re-emit spend.jsonl rows into
    audit.jsonl as usage_event/v1 kind=spend, and emit one legacy_snapshot opening-balance
    event. Safe to call repeatedly — second+ calls are a no-op (returns the same counts as
    'already done', new counts as 0)."""
    burnless_root = Path(project_root) / ".burnless"
    metrics_path = burnless_root / "metrics.json"
    audit_path = burnless_root / "audit.jsonl"
    spend_path = burnless_root / "spend.jsonl"
    legacy_metrics_path = burnless_root / "metrics.legacy.json"

    # 1. Freeze metrics.json once. If already frozen, never overwrite (idempotent anchor).
    if not legacy_metrics_path.exists():
        frozen = metrics_mod.load(metrics_path)
        legacy_metrics_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_metrics_path.write_text(
            json.dumps(frozen, indent=2, sort_keys=True, ensure_ascii=False),
            encoding="utf-8",
        )
    frozen_bytes = legacy_metrics_path.read_bytes()
    migration_id = "legacy_snapshot:" + hashlib.sha256(frozen_bytes).hexdigest()

    existing = metrics_mod.read_audit(audit_path)
    existing_event_ids = {
        e.get("event_id") for e in existing if isinstance(e, dict) and e.get("schema") == "usage_event/v1"
    }

    legacy_snapshot_emitted = False
    if migration_id not in existing_event_ids:
        frozen = json.loads(frozen_bytes.decode("utf-8"))
        snapshot_entry = {
            "schema": "usage_event/v1",
            "event_id": migration_id,
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": "saving",
            "source": "legacy_snapshot",
            "amount": 0,
            "tier": "unknown",
            "delegation_id": None,
            "call_kind": "legacy_snapshot",
            "reason": "opening balance: frozen metrics.legacy.json scalars",
            "basis": {"amount": "legacy"},
            "extra": {
                "legacy_run_calls": frozen.get("legacy_run_calls", 0),
                "legacy_compress_calls": frozen.get("legacy_compress_calls", 0),
                "legacy_decompress_calls": frozen.get("legacy_decompress_calls", 0),
                "dead_logs_isolated": frozen.get("dead_logs_isolated", 0),
                "keepalive_pings_total": frozen.get("keepalive_pings_total", 0),
                "keepalive_pings_ok": frozen.get("keepalive_pings_ok", 0),
                "keepalive_pings_miss": frozen.get("keepalive_pings_miss", 0),
                "keepalive_pings_err": frozen.get("keepalive_pings_err", 0),
                "keepalive_cost_usd": frozen.get("keepalive_cost_usd", 0.0),
                "legacy_encoder_calls": frozen.get("encoder_calls", 0),
            },
        }
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with audit_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(snapshot_entry, ensure_ascii=False) + "\n")
        existing_event_ids.add(migration_id)
        legacy_snapshot_emitted = True

    # 2. Re-emit spend.jsonl rows as kind=spend, deduped by content hash.
    spend_rows = metrics_mod.read_spend(spend_path)
    migrated = 0
    skipped_existing = 0
    for row in spend_rows:
        row_id = hashlib.sha256(
            json.dumps(row, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        if row_id in existing_event_ids:
            skipped_existing += 1
            continue
        usage = row.get("usage") or {}
        tokens = metrics_mod.tokens_from_usage(usage)
        model = row.get("model")
        cost_usd = metrics_mod.cost_for_tokens(model, tokens)
        spend_entry = {
            "schema": "usage_event/v1",
            "event_id": row_id,
            "ts": row.get("ts") or datetime.now(timezone.utc).isoformat(),
            "kind": "spend",
            "provider": row.get("provider"),
            "model": model or "unknown",
            "tier": row.get("tier") or "unknown",
            "delegation_id": row.get("delegation_id"),
            "tokens": tokens,
            "cost": {"usd": cost_usd, "basis": "pricing_table", "pricing_version": metrics_mod.PRICING_VERSION},
            "basis": {
                "input": "observed",
                "output": "observed",
                "cache_read": "observed",
                "cache_write": "observed",
                "cost.usd": "pricing_table",
            },
            "reason": f"migrated spend row ({row.get('tier') or 'unknown'}/{row.get('provider') or 'unknown'})",
            "extra": {"duration_s": row.get("duration_s"), "backend": row.get("backend"), "migrated_from": "spend.jsonl"},
        }
        with audit_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(spend_entry, ensure_ascii=False) + "\n")
        existing_event_ids.add(row_id)
        migrated += 1

    return {
        "migration_id": migration_id,
        "legacy_snapshot_emitted": legacy_snapshot_emitted,
        "spend_rows_migrated": migrated,
        "spend_rows_skipped_existing": skipped_existing,
        "spend_rows_total": len(spend_rows),
    }
