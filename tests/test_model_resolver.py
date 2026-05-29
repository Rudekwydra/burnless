from burnless.config import (
    DEFAULT_TIER_MODELS,
    HAIKU_MODEL,
    MODEL_ALIASES,
    normalize_model,
    resolve_fallback_model,
    resolve_model,
)


def test_normalize_model_aliases():
    assert normalize_model("opus") == "claude-opus-4-8"
    assert normalize_model("sonnet") == "claude-sonnet-4-6"
    assert normalize_model("haiku") == "claude-haiku-4-5-20251001"


def test_normalize_model_idempotent():
    for alias in MODEL_ALIASES:
        assert normalize_model(normalize_model(alias)) == normalize_model(alias)
    assert normalize_model(normalize_model("claude-opus-4-8")) == "claude-opus-4-8"


def test_normalize_model_passthrough():
    assert normalize_model("gpt-5.2") == "gpt-5.2"
    assert normalize_model("claude-sonnet-4-6") == "claude-sonnet-4-6"


def test_normalize_model_none():
    assert normalize_model(None) is None


def test_defaults():
    assert DEFAULT_TIER_MODELS["gold"] == "claude-opus-4-8"
    assert HAIKU_MODEL == "claude-haiku-4-5-20251001"


def test_resolve_model_command_precedence():
    cfg = {"agents": {"gold": {"command": "claude --model opus -p"}}}
    assert resolve_model("gold", cfg) == "claude-opus-4-8"


def test_resolve_model_name_precedence():
    cfg = {"agents": {"silver": {"name": "sonnet"}}}
    assert resolve_model("silver", cfg) == "claude-sonnet-4-6"


def test_resolve_model_empty_cfg():
    assert resolve_model("gold") == "claude-opus-4-8"
    assert resolve_model("silver") == "claude-sonnet-4-6"
    assert resolve_model("bronze") == "claude-haiku-4-5-20251001"


def test_resolve_model_unknown_tier_falls_back_to_silver():
    assert resolve_model("nonexistent") == DEFAULT_TIER_MODELS["silver"]


def test_resolve_fallback_model_providers():
    cfg = {
        "agents": {
            "gold": {
                "providers": [
                    {"command": "claude --model opus -p"},
                    {"command": "claude --model sonnet -p"},
                ]
            }
        }
    }
    assert resolve_fallback_model("gold", cfg) == "claude-sonnet-4-6"


def test_resolve_fallback_model_fallback_key():
    cfg = {"agents": {"silver": {"fallback": "haiku"}}}
    assert resolve_fallback_model("silver", cfg) == "claude-haiku-4-5-20251001"


def test_resolve_fallback_model_none():
    assert resolve_fallback_model("gold") is None
    cfg = {"agents": {"gold": {}}}
    assert resolve_fallback_model("gold", cfg) is None
