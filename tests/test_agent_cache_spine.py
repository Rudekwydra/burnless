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


def test_resolve_cache_mode_codex_subscription():
    a = Agent(name="silver", role="execute", provider="codex", auth="subscription")
    cm = resolve_cache_mode(a)
    assert cm.name == "codex_subscription"


def test_resolve_cache_mode_codex_api():
    a = Agent(name="silver", role="execute", provider="codex", auth="api")
    cm = resolve_cache_mode(a)
    assert cm.name == "codex_api"


def test_resolve_cache_mode_gemini_subscription():
    a = Agent(name="silver", role="execute", provider="gemini", auth="subscription")
    cm = resolve_cache_mode(a)
    assert cm.name == "gemini_subscription"


def test_resolve_cache_mode_gemini_api():
    a = Agent(name="silver", role="execute", provider="gemini", auth="api")
    cm = resolve_cache_mode(a)
    assert cm.name == "gemini_api"


def test_cfg_flips_mode():
    cfg = {"agents": {"silver": {"provider": "codex"}}}
    a = resolve_agent("silver", cfg)
    assert a.provider == "codex"
    cm = resolve_cache_mode(a)
    assert cm.name == "codex_subscription"

    cfg2 = {"agents": {"silver": {"auth": "api"}}}
    a2 = resolve_agent("silver", cfg2)
    assert a2.auth == "api"
    cm2 = resolve_cache_mode(a2)
    assert cm2.name == "anthropic_api"


def test_cache_modes_registry():
    keys = [
        "anthropic_subscription", "anthropic_api",
        "codex_subscription", "codex_api",
        "gemini_subscription", "gemini_api",
        "none",
    ]
    mods = [cache_modes.get(k) for k in keys]
    for k, mod in zip(keys, mods):
        assert mod is not None, k

    # All 7 are distinct modules
    names = [m.__name__ for m in mods]
    assert len(set(names)) == 7, names

    sub = cache_modes.get("anthropic_subscription")
    api = cache_modes.get("anthropic_api")
    assert sub.__name__ != api.__name__
