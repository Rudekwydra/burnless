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


def test_cmd_maestro_off_echoes_telegram(tmp_path, monkeypatch, capsys):
    from burnless import cli, maestro_runner, config as config_mod
    import argparse

    monkeypatch.setattr(config_mod, "resolve_layer_models", lambda cfg: {"encoder": "passthrough", "maestro": "off"})
    monkeypatch.setattr(config_mod, "load", lambda *a, **k: {})
    monkeypatch.setattr(cli.paths_mod, "require_root", lambda: tmp_path)
    monkeypatch.setattr(cli.paths_mod, "paths_for", lambda root: {"config": tmp_path / "config.yaml"})

    called = {"ran": False}
    monkeypatch.setattr(maestro_runner, "run_maestro", lambda *a, **k: called.__setitem__("ran", True) or {})

    args = argparse.Namespace(telegram='{"intent":"x"}', model=None)
    rc = cli.cmd_maestro(args)
    out = capsys.readouterr().out
    assert rc == 0
    assert '{"intent":"x"}' in out
    assert called["ran"] is False
