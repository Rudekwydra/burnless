from __future__ import annotations

import json


def test_migrate_freezes_metrics_once(tmp_path):
    from burnless import metrics as metrics_mod
    from burnless.ledger_migrate import migrate

    burnless_root = tmp_path / ".burnless"
    burnless_root.mkdir(parents=True, exist_ok=True)
    metrics_path = burnless_root / "metrics.json"
    m = metrics_mod.load(metrics_path)
    m["legacy_run_calls"] = 5
    metrics_mod.save(metrics_path, m)

    migrate(tmp_path)
    legacy_path = burnless_root / "metrics.legacy.json"
    first = legacy_path.read_text(encoding="utf-8")

    migrate(tmp_path)
    second = legacy_path.read_text(encoding="utf-8")

    assert first == second


def _write_spend_rows(burnless_root):
    spend_path = burnless_root / "spend.jsonl"
    rows = [
        {
            "ts": "2026-07-02T00:00:00Z",
            "tier": "silver",
            "provider": "claude",
            "model": "claude-sonnet",
            "usage": {"input_tokens": 10, "output_tokens": 20},
        },
        {
            "ts": "2026-07-02T00:01:00Z",
            "tier": "gold",
            "provider": "claude",
            "model": "claude-opus",
            "usage": {"input_tokens": 30, "output_tokens": 40},
        },
    ]
    with spend_path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    return spend_path


def test_migrate_reemits_spend_as_kind_spend(tmp_path):
    from burnless import metrics as metrics_mod
    from burnless.ledger_migrate import migrate

    burnless_root = tmp_path / ".burnless"
    burnless_root.mkdir(parents=True, exist_ok=True)
    _write_spend_rows(burnless_root)

    result = migrate(tmp_path)
    assert result["spend_rows_migrated"] == 2

    audit_path = burnless_root / "audit.jsonl"
    rows = metrics_mod.read_audit(audit_path)
    spend_events = [r for r in rows if r.get("kind") == "spend"]
    assert len(spend_events) == 2
    for e in spend_events:
        assert e["schema"] == "usage_event/v1"


def test_migrate_idempotent_no_duplication(tmp_path):
    from burnless import metrics as metrics_mod
    from burnless.ledger_migrate import migrate

    burnless_root = tmp_path / ".burnless"
    burnless_root.mkdir(parents=True, exist_ok=True)
    _write_spend_rows(burnless_root)

    migrate(tmp_path)
    audit_path = burnless_root / "audit.jsonl"
    count_after_first = len(metrics_mod.read_audit(audit_path))

    result_second = migrate(tmp_path)
    assert result_second["spend_rows_migrated"] == 0
    assert result_second["spend_rows_skipped_existing"] == 2

    count_after_second = len(metrics_mod.read_audit(audit_path))
    assert count_after_second == count_after_first


def test_migrate_legacy_snapshot_event_present_once(tmp_path):
    from burnless import metrics as metrics_mod
    from burnless.ledger_migrate import migrate

    burnless_root = tmp_path / ".burnless"
    burnless_root.mkdir(parents=True, exist_ok=True)
    _write_spend_rows(burnless_root)

    result_first = migrate(tmp_path)
    migration_id = result_first["migration_id"]
    migrate(tmp_path)

    audit_path = burnless_root / "audit.jsonl"
    rows = metrics_mod.read_audit(audit_path)
    matching = [r for r in rows if r.get("event_id") == migration_id]
    assert len(matching) == 1


def test_migrate_handles_missing_spend_file(tmp_path):
    from burnless.ledger_migrate import migrate

    burnless_root = tmp_path / ".burnless"
    burnless_root.mkdir(parents=True, exist_ok=True)

    result = migrate(tmp_path)
    assert result["spend_rows_migrated"] == 0
    assert result["spend_rows_total"] == 0
