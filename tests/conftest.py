import os
import stat
import tempfile
from pathlib import Path

import pytest


def _install_security_shim() -> None:
    """macOS Keychain guard.

    Several tests spawn the real ``claude`` CLI (setup_wizard's --version probe,
    MCP registration) and burnless shells out to ``security find-generic-password``
    to read the Claude Code OAuth token. Under the tests' isolated ``$HOME`` the
    login keychain is absent, so macOS SecurityAgent pops a *blocking* "Keychain
    Not Found" modal — dozens per run (62 ``security`` calls observed in one pass,
    one of them ``security -i``). Prepend a no-op ``security`` shim to ``PATH`` so
    every such call returns cleanly with no GUI. Test-only; never touches the real
    keychain. Set ``BURNLESS_ALLOW_KEYCHAIN=1`` to opt out.
    """
    if os.environ.get("BURNLESS_ALLOW_KEYCHAIN") == "1":
        return
    shim_dir = Path(tempfile.gettempdir()) / "burnless_test_shims"
    shim_dir.mkdir(parents=True, exist_ok=True)
    shim = shim_dir / "security"
    # Exit 0 with no output: callers treat it as "no credential" and move on.
    shim.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    path = os.environ.get("PATH", "")
    if str(shim_dir) not in path.split(os.pathsep):
        os.environ["PATH"] = f"{shim_dir}{os.pathsep}{path}"


# Run at import — before collection — so PATH is set for every spawned subprocess.
_install_security_shim()


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
