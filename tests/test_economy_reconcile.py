from burnless import ledger_projector, economy, dashboard


def test_accounted_vs_monetizable_with_raw_logs_isolated():
    events = [
        {"schema": "usage_event/v1", "kind": "saving", "ts": "2026-07-21T18:00:00+00:00",
         "source": "capsule_compression", "amount": 500},
        {"schema": "usage_event/v1", "kind": "saving", "ts": "2026-07-21T18:01:00+00:00",
         "source": "raw_logs_isolated", "amount": 1000},
    ]
    snap = ledger_projector.project(events)
    r = economy.compute_economy_snapshot(snap)
    assert r.accounted_total == 1500
    assert r.monetizable_subtotal == 500
    assert r.excluded_categories == [("raw_logs_isolated", 1000)]


def test_render_economy_shows_excluded_when_subtotal_less_than_total():
    events = [
        {"schema": "usage_event/v1", "kind": "saving", "ts": "2026-07-21T18:00:00+00:00",
         "source": "capsule_compression", "amount": 500},
        {"schema": "usage_event/v1", "kind": "saving", "ts": "2026-07-21T18:01:00+00:00",
         "source": "raw_logs_isolated", "amount": 1000},
    ]
    snap = ledger_projector.project(events)
    r = economy.compute_economy_snapshot(snap)
    out = dashboard.render_economy(r)
    assert "raw_logs_isolated" in out
    assert "1,000" in out


def test_no_exclusion_when_no_raw_logs_isolated():
    events = [
        {"schema": "usage_event/v1", "kind": "saving", "ts": "2026-07-21T18:00:00+00:00",
         "source": "capsule_compression", "amount": 200},
    ]
    snap = ledger_projector.project(events)
    r = economy.compute_economy_snapshot(snap)
    assert r.accounted_total == r.monetizable_subtotal == 200
    assert r.excluded_categories == []
    out = dashboard.render_economy(r)
    assert "Excluded:             (none)" in out


def test_render_audit_handles_dict_basis_without_crashing():
    entries = [
        {"ts": "2026-07-21T00:00:00+00:00", "source": "capsule_compression",
         "amount": 10, "basis": {"amount": "chars"}, "delegation_id": "d1", "reason": "r"},
        {"ts": "2026-07-21T00:01:00+00:00", "source": "capsule_compression",
         "amount": 20, "basis": "estimated", "delegation_id": "d2", "reason": "r2"},
    ]
    out = dashboard.render_audit(entries)
    assert isinstance(out, str)
    lines = out.splitlines()
    assert "estimated" in lines[1]


def test_compute_economy_snapshot_matches_ledger_totals():
    events = [
        {"schema": "usage_event/v1", "kind": "saving", "ts": "2026-07-21T18:00:00+00:00",
         "source": "capsule_compression", "amount": 300},
        {"schema": "usage_event/v1", "kind": "saving", "ts": "2026-07-21T18:01:00+00:00",
         "source": "repeated_context_avoided", "amount": 150},
    ]
    snap = ledger_projector.project(events)
    r = economy.compute_economy_snapshot(snap)
    assert r.accounted_total == snap.accounted_total_tokens
    assert r.monetizable_subtotal == snap.monetizable_tokens
