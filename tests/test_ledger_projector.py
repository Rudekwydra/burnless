from burnless.ledger_projector import Window, project, read_ledger, resolve_basis
from burnless.pricing import rate, rate_versioned


def test_legacy_line_mapped_as_saving():
    events = [
        {"ts": "2026-05-02T12:21:23.308601+00:00", "source": "capsule_compression",
         "amount": 171658, "reason": "capsule mode=balanced", "delegation_id": "d001"},
    ]
    snap = project(events)
    assert snap.by_source["capsule_compression"] == 171658
    assert snap.accounted_total_tokens == 171658


def test_spend_event_sums_tokens_and_cost():
    events = [
        {"schema": "usage_event/v1", "kind": "spend", "ts": "2026-07-21T18:40:00+00:00",
         "event_id": "e1", "tokens": {"input": 100, "output": 50, "cache_read": 0, "cache_write": 0},
         "cost": {"usd": 0.01}},
        {"schema": "usage_event/v1", "kind": "spend", "ts": "2026-07-21T18:41:00+00:00",
         "event_id": "e2", "tokens": {"input": 200, "output": 25, "cache_read": 10, "cache_write": 5},
         "cost": {"usd": 0.02}},
    ]
    snap = project(events)
    assert snap.spend_tokens == {"input": 300, "output": 75, "cache_read": 10, "cache_write": 5}
    assert abs(snap.spend_usd - 0.03) < 1e-9
    assert snap.counts["spend_events"] == 2


def test_raw_logs_isolated_excluded_from_monetizable():
    events = [
        {"schema": "usage_event/v1", "kind": "saving", "ts": "2026-07-21T18:00:00+00:00",
         "source": "raw_logs_isolated", "amount": 1000},
        {"schema": "usage_event/v1", "kind": "saving", "ts": "2026-07-21T18:01:00+00:00",
         "source": "capsule_compression", "amount": 500},
    ]
    snap = project(events)
    assert snap.accounted_total_tokens == 1500
    assert snap.monetizable_tokens == 500
    assert snap.excluded_categories == [("raw_logs_isolated", 1000)]


def test_encoder_calls_derived_from_capsule_compression_savings():
    events = [
        {"ts": "2026-05-02T12:21:23.308601+00:00", "source": "capsule_compression", "amount": 100},
        {"schema": "usage_event/v1", "kind": "saving", "ts": "2026-07-21T18:00:00+00:00",
         "source": "capsule_compression", "call_kind": "delegation_capsule", "amount": 200},
        {"schema": "usage_event/v1", "kind": "saving", "ts": "2026-07-21T18:01:00+00:00",
         "source": "capsule_compression", "call_kind": "delegation_capsule", "amount": 300},
        {"schema": "usage_event/v1", "kind": "saving", "ts": "2026-07-21T18:02:00+00:00",
         "source": "repeated_context_avoided", "amount": 50},
    ]
    snap = project(events)
    assert snap.encoder_calls == 3


def test_unrecognized_kind_becomes_warning_not_summed():
    base = [
        {"schema": "usage_event/v1", "kind": "saving", "ts": "2026-07-21T18:00:00+00:00",
         "source": "capsule_compression", "amount": 100},
    ]
    mystery = {"schema": "usage_event/v1", "kind": "mystery", "ts": "2026-07-21T18:05:00+00:00"}

    snap_without = project(base)
    snap_with = project(base + [mystery])

    assert any("mystery" in w for w in snap_with.warnings)
    assert snap_with.accounted_total_tokens == snap_without.accounted_total_tokens


def test_replay_deterministic():
    events = [
        {"ts": "2026-05-02T12:21:23.308601+00:00", "source": "capsule_compression", "amount": 100},
        {"schema": "usage_event/v1", "kind": "spend", "ts": "2026-07-21T18:40:00+00:00",
         "event_id": "e1", "tokens": {"input": 10, "output": 5, "cache_read": 0, "cache_write": 0},
         "cost": {"usd": 0.001}},
    ]
    snap1 = project(events)
    snap2 = project(events)
    assert snap1.ledger_sha256 == snap2.ledger_sha256


def test_window_filters_by_ts():
    events = [
        {"schema": "usage_event/v1", "kind": "saving", "ts": "2026-07-21T10:00:00+00:00",
         "source": "capsule_compression", "amount": 100},
        {"schema": "usage_event/v1", "kind": "saving", "ts": "2026-07-21T20:00:00+00:00",
         "source": "capsule_compression", "amount": 900},
    ]
    window = Window(start_ts="2026-07-21T09:00:00+00:00", end_ts="2026-07-21T11:00:00+00:00")
    snap = project(events, window=window)
    assert snap.saving_tokens == 100


def test_read_ledger_tolerant_to_corrupt_line(tmp_path):
    path = tmp_path / "audit.jsonl"
    path.write_text(
        '{"ts": "2026-07-21T18:00:00+00:00", "source": "capsule_compression", "amount": 1}\n'
        "not json at all\n"
    )
    events = list(read_ledger(path))
    assert len(events) == 1
    assert events[0]["source"] == "capsule_compression"


def test_resolve_basis_precedence():
    with_basis = {"basis": {"input": "observed"}}
    without_basis = {}
    assert resolve_basis(with_basis, "input") == "observed"
    assert resolve_basis(without_basis, "input") == "chars"


def test_rate_versioned_matches_rate_for_2026_01():
    assert rate_versioned("opus", "input") == rate("opus", "input")
    assert rate_versioned("sonnet", "output") == rate("sonnet", "output")
