import json
from datetime import datetime, timezone

from burnless import agents
from burnless.pricing import blended_cost


def test_blended_cost():
    o = blended_cost("claude-opus-4-8")
    h = blended_cost("claude-haiku-4-5")
    g = blended_cost("gemma-4-12b-it")
    assert o > h > 0, (o, h, g)
    assert g == 0.0, g


def test_cheaper_preferred_when_equal_health(tmp_path, monkeypatch):
    monkeypatch.setenv("BURNLESS_PROVIDER_HEALTH_PATH", str(tmp_path / "ph.json"))
    cfg = {
        "name": "test-agent",
        "command": "echo test",
        "providers": [
            {"provider": "expensive", "model": "claude-opus-4-8", "command": "echo opus"},
            {"provider": "cheap", "model": "claude-haiku-4-5", "command": "echo haiku"},
        ],
    }
    ranked = agents.rank_providers(cfg, tier="silver")
    assert ranked[0]["cfg"]["provider"] == "cheap"
    assert ranked[1]["cfg"]["provider"] == "expensive"


def test_local_is_best_cost(tmp_path, monkeypatch):
    monkeypatch.setenv("BURNLESS_PROVIDER_HEALTH_PATH", str(tmp_path / "ph.json"))
    cfg = {
        "name": "test-agent",
        "command": "echo test",
        "providers": [
            {"provider": "big-paid", "model": "claude-opus-4-8", "command": "echo opus"},
            {"provider": "local", "model": "gemma-4-12b-it", "command": "echo gemma"},
            {"provider": "small-paid", "model": "claude-haiku-4-5", "command": "echo haiku"},
        ],
    }
    ranked = agents.rank_providers(cfg, tier="silver")
    providers_ordered = [r["cfg"]["provider"] for r in ranked]
    # free (local) and cheapest-paid both get cost_score=1.0 → rank above opus
    assert providers_ordered.index("big-paid") > providers_ordered.index("local")
    assert providers_ordered.index("big-paid") > providers_ordered.index("small-paid")


def test_recent_error_deprioritized(tmp_path, monkeypatch):
    health_path = tmp_path / "ph.json"
    monkeypatch.setenv("BURNLESS_PROVIDER_HEALTH_PATH", str(health_path))
    now_iso = datetime.now(timezone.utc).isoformat()
    health_path.write_text(
        json.dumps({
            "silver:errored": {
                "tier": "silver", "provider": "errored",
                "success_rate": 1.0, "avg_latency": 1.0,
                "last_error_at": now_iso,
            },
            "silver:clean": {
                "tier": "silver", "provider": "clean",
                "success_rate": 1.0, "avg_latency": 1.0,
                "last_error_at": None,
            },
        }),
        encoding="utf-8",
    )
    cfg = {
        "name": "test-agent",
        "command": "echo test",
        "providers": [
            {"provider": "errored", "model": "claude-haiku-4-5", "command": "echo errored"},
            {"provider": "clean", "model": "claude-haiku-4-5", "command": "echo clean"},
        ],
    }
    ranked = agents.rank_providers(cfg, tier="silver")
    assert ranked[0]["cfg"]["provider"] == "clean"
    assert ranked[1]["cfg"]["provider"] == "errored"
