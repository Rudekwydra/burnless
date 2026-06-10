from burnless.config import resolve_stale_timeout


def test_ollama_local_floor():
    assert resolve_stale_timeout({}, "bronze", provider="ollama-local") == 1800


def test_anthropic_unchanged():
    assert resolve_stale_timeout({}, "bronze", provider="anthropic") == 120


def test_explicit_cli_wins():
    assert resolve_stale_timeout({}, "bronze", cli_override=60, provider="ollama-local") == 60


def test_higher_tier_default_kept():
    assert resolve_stale_timeout({}, "gold", provider="ollama-local") == 1800


def test_none_provider_unchanged():
    assert resolve_stale_timeout({}, "bronze") == 120
