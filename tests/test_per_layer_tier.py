from burnless import config


def test_default_preset_is_protocol_haiku():
    r = config.resolve_layer_models(config.DEFAULT_CONFIG)
    assert r["encoder"] == "claude-haiku-4-5-20251001"
    assert r["maestro"] == "claude-haiku-4-5-20251001"


def test_direct_preset_passthrough_off():
    r = config.resolve_layer_models({"preset": "direct"})
    assert r["encoder"] == "passthrough"
    assert r["maestro"] == "off"


def test_explicit_overrides_preset():
    r = config.resolve_layer_models({"preset": "protocol", "encoder": {"model": "claude-opus-4-8"}, "maestro": {"model": "off"}})
    assert r["encoder"] == "claude-opus-4-8"
    assert r["maestro"] == "off"


def test_unknown_preset_falls_back_protocol():
    r = config.resolve_layer_models({"preset": "bogus"})
    assert r["encoder"] == "claude-haiku-4-5-20251001"
