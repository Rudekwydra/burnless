"""Tests for Agent + CacheMode spine — no network, no subprocess."""
from burnless.coreconfig.resolver import resolve_agent, resolve_cache_mode
from burnless.coreconfig.schema import Agent
from burnless import cache_modes


def test_resolve_agent_silver():
    a = resolve_agent("silver")
    assert a.provider == "anthropic"
    assert a.auth == "subscription"
    assert a.role == "execute"


def test_resolve_agent_maestro():
    m = resolve_agent("maestro")
    assert m.role == "orchestrate"
    assert m.rules == "never_execute"


def test_resolve_cache_mode_subscription():
    a = resolve_agent("silver")
    cm = resolve_cache_mode(a)
    assert cm.name == "anthropic_subscription"
    assert cm.mechanism == "cli_setting_sources"


def test_resolve_cache_mode_api():
    a = Agent(name="silver", role="execute", provider="anthropic", auth="api")
    cm = resolve_cache_mode(a)
    assert cm.name == "anthropic_api"
    assert cm.mechanism == "sdk_cache_control"


def test_resolve_cache_mode_codex():
    a = Agent(name="silver", role="execute", provider="codex", auth="subscription")
    cm = resolve_cache_mode(a)
    assert cm.name == "codex"


def test_cfg_flips_mode():
    cfg = {"agents": {"silver": {"provider": "codex"}}}
    a = resolve_agent("silver", cfg)
    assert a.provider == "codex"
    cm = resolve_cache_mode(a)
    assert cm.name == "codex"

    cfg2 = {"agents": {"silver": {"auth": "api"}}}
    a2 = resolve_agent("silver", cfg2)
    assert a2.auth == "api"
    cm2 = resolve_cache_mode(a2)
    assert cm2.name == "anthropic_api"


def test_cache_modes_registry():
    for k in ("anthropic_subscription", "anthropic_api", "codex", "none"):
        mod = cache_modes.get(k)
        assert mod is not None, k

    sub = cache_modes.get("anthropic_subscription")
    api = cache_modes.get("anthropic_api")
    assert sub.__name__ != api.__name__
