from burnless.config import resolve_stale_timeout


def test_bronze_builtin():
    assert resolve_stale_timeout({}, "bronze") == 120


def test_silver_builtin():
    assert resolve_stale_timeout({}, "silver") == 600


def test_gold_builtin():
    assert resolve_stale_timeout({}, "gold") == 900


def test_legacy_global_wins_over_builtin():
    assert resolve_stale_timeout({"display": {"stale_timeout_seconds": 450}}, "silver") == 450


def test_tier_map_wins_over_builtin():
    cfg = {"display": {"tier_stale_timeout_seconds": {"silver": 700}}}
    assert resolve_stale_timeout(cfg, "silver") == 700


def test_tier_map_wins_over_legacy():
    cfg = {"display": {"tier_stale_timeout_seconds": {"silver": 700}, "stale_timeout_seconds": 300}}
    assert resolve_stale_timeout(cfg, "silver") == 700


def test_cli_override_wins_all():
    assert resolve_stale_timeout({}, "silver", cli_override=42) == 42


def test_unknown_tier_fallback():
    assert resolve_stale_timeout({}, "weird_tier") == 300
