import argparse
import tempfile
from pathlib import Path
import yaml

from burnless.cli import cmd_models
from burnless.config import global_config_path


def test_global_config_path_with_env_var(monkeypatch):
    """Test that global_config_path() returns BURNLESS_GLOBAL_CONFIG when set."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / "custom_config.yaml"
        monkeypatch.setenv("BURNLESS_GLOBAL_CONFIG", str(tmp_path))
        assert global_config_path() == tmp_path


def test_models_set_with_default_persists(monkeypatch):
    """Test that 'models set <tier> <spec> --default' persists to global config."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_file = Path(tmpdir) / "config.yaml"
        monkeypatch.setenv("BURNLESS_GLOBAL_CONFIG", str(tmp_file))

        # Build args for: models set silver ollama:gemma4-e4b --default
        args = argparse.Namespace(
            models_action="set",
            tier="silver",
            spec="ollama:gemma4-e4b",
            make_default=True,
        )

        result = cmd_models(args)
        assert result == 0
        assert tmp_file.exists()

        # Load the saved YAML and verify the agent config
        cfg = yaml.safe_load(tmp_file.read_text())
        assert cfg["agents"]["silver"]["provider"] == "ollama-local"
        assert cfg["agents"]["silver"]["model"] == "gemma4-e4b"


def test_models_set_without_default_does_not_persist(monkeypatch):
    """Test that 'models set <tier> <spec>' without --default does NOT persist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_file = Path(tmpdir) / "config.yaml"
        monkeypatch.setenv("BURNLESS_GLOBAL_CONFIG", str(tmp_file))

        # Build args for: models set silver ollama:gemma4-e4b (no --default)
        args = argparse.Namespace(
            models_action="set",
            tier="silver",
            spec="ollama:gemma4-e4b",
            make_default=False,
        )

        result = cmd_models(args)
        assert result == 0
        # File should NOT be created or modified
        assert not tmp_file.exists()
