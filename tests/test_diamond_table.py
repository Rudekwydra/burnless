from burnless.menu import render_models_table
from burnless.config import DEFAULT_CONFIG


def test_diamond_present_in_table():
    cfg = {"agents": {"diamond": {"name": "fable", "provider": "anthropic"}}}
    t = render_models_table(cfg, cfg)
    assert "diamond" in t
    assert "fable" in t


def test_diamond_not_set_when_missing():
    cfg = {"agents": {"gold": {"name": "opus", "provider": "anthropic"}}}
    default_cfg = {"agents": {"gold": {"name": "opus", "provider": "anthropic"}}}
    t = render_models_table(cfg, default_cfg)
    assert "diamond" in t
    assert "(not set)" in t


def test_default_config_diamond_name():
    assert DEFAULT_CONFIG["agents"]["diamond"]["name"] == "fable"
