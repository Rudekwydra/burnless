import argparse

from burnless import cli


def test_run_uses_configured_worker_backend_by_default():
    args = argparse.Namespace(maestro=False, no_maestro=False)

    assert not cli._should_use_maestro_backend(args, {}, "silver")


def test_maestro_backend_is_explicit_opt_in():
    args = argparse.Namespace(maestro=True, no_maestro=False)

    assert cli._should_use_maestro_backend(args, {}, "silver")


def test_config_can_opt_into_maestro_backend():
    args = argparse.Namespace(maestro=False, no_maestro=False)
    cfg = {"maestro": {"run_backend": True}}

    assert cli._should_use_maestro_backend(args, cfg, "bronze")

