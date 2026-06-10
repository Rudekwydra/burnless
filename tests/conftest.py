import pytest


@pytest.fixture(autouse=True)
def _hermetic_global_config(monkeypatch):
    # Tests must never read the operator's real ~/.config/burnless/config.yaml.
    # Individual tests that exercise the global cascade re-set this env var to a temp path.
    monkeypatch.setenv("BURNLESS_GLOBAL_CONFIG", "")
