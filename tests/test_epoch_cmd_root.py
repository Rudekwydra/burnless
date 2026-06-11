from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from burnless import cli, paths as paths_mod


@pytest.fixture
def tmp_project():
    """Create a temporary project directory with .burnless/config.yaml."""
    with tempfile.TemporaryDirectory() as d:
        tmp_dir = Path(d)
        burnless_dir = tmp_dir / ".burnless"
        burnless_dir.mkdir(parents=True, exist_ok=True)
        # Create minimal config so find_root recognizes this as a burnless project
        config_file = burnless_dir / "config.yaml"
        config_file.write_text("project: test\n", encoding="utf-8")
        yield tmp_dir


def test_epoch_on_writes_correct_marker(tmp_project):
    """Test that epoch on writes to .burnless/epochs.on (not doubled .burnless/.burnless/)."""
    burnless_dir = tmp_project / ".burnless"

    # Monkeypatch find_root to return the .burnless dir when cwd is the project dir
    def mock_find_root():
        return burnless_dir

    with patch.object(paths_mod, "find_root", mock_find_root):
        args = argparse.Namespace(
            epoch_cmd="on",
            root=None,
            chat_id=None,
        )
        rc = cli.cmd_epoch(args)

    assert rc == 0, "cmd_epoch returned non-zero"

    # Check correct path exists
    marker_path = burnless_dir / "epochs.on"
    assert marker_path.exists(), f"marker not found at {marker_path}"

    # Check doubled path does NOT exist
    doubled_path = burnless_dir / ".burnless" / "epochs.on"
    assert not doubled_path.exists(), f"doubled path should not exist: {doubled_path}"


def test_epoch_explicit_root_unchanged(tmp_project):
    """Test that passing explicit --root bypasses find_root and uses it directly."""
    burnless_dir = tmp_project / ".burnless"

    args = argparse.Namespace(
        epoch_cmd="on",
        root=str(tmp_project),  # Explicit root = project dir
        chat_id=None,
    )
    rc = cli.cmd_epoch(args)

    assert rc == 0, "cmd_epoch returned non-zero"

    # Check marker at correct location
    marker_path = burnless_dir / "epochs.on"
    assert marker_path.exists(), f"marker not found at {marker_path}"

    # Check doubled path does NOT exist
    doubled_path = burnless_dir / ".burnless" / "epochs.on"
    assert not doubled_path.exists(), f"doubled path should not exist: {doubled_path}"
