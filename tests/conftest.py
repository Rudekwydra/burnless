import pytest


@pytest.fixture(autouse=True)
def _hermetic_global_config(monkeypatch):
    # Tests must never read the operator's real ~/.config/burnless/config.yaml.
    # Individual tests that exercise the global cascade re-set this env var to a temp path.
    monkeypatch.setenv("BURNLESS_GLOBAL_CONFIG", "")


@pytest.fixture(autouse=True)
def _hermetic_state_dir(monkeypatch, tmp_path_factory):
    # Tests must never write the operator's real ~/.burnless/state — e.g.
    # pilot rollover writes pending_seed.md there; running the suite was
    # contaminating live sessions with fixture seeds (audit 2026-07-03).
    monkeypatch.setenv("BURNLESS_STATE_DIR", str(tmp_path_factory.mktemp("burnless-state")))
