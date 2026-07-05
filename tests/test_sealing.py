import json
import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import Mock

import pytest

from burnless import sealing, recovery


@pytest.fixture
def tmp_burnless(tmp_path):
    """Create a minimal .burnless structure with state directory."""
    burnless_dir = tmp_path / ".burnless"
    burnless_dir.mkdir()
    (burnless_dir / "epochs").mkdir()
    (burnless_dir / "state").mkdir()
    return tmp_path, burnless_dir


def test_seal_epoch_creates_new_capsule(tmp_burnless, monkeypatch):
    """Create new capsule when checkpoint exists and forgetless is available."""
    tmp_path, burnless_dir = tmp_burnless
    root = tmp_path

    # Set isolated FORGETLESS_ROOT
    fake_forgetless_home = tmp_path / "fake-forgetless-home"
    fake_forgetless_home.mkdir()
    monkeypatch.setenv("FORGETLESS_ROOT", str(fake_forgetless_home))

    # Write checkpoint with non-empty living_md
    recovery.write_checkpoint(
        root,
        host="claude",
        host_session_id="abcdefgh-1234",
        process_instance_id="proc-1",
        living_md="## Foco atual\n- objetivo do teste\n",
        harvested_state={},
        applied_through=10,
        journal_head=5,
    )

    # Run seal_epoch
    result = sealing.seal_epoch(root, host="claude", host_session_id="abcdefgh-1234")

    assert result["status"] == "sealed"
    assert result["mode"] == "new"
    assert "capsule" in result

    # Verify capsule was created (with isolated FORGETLESS_ROOT still set)
    get_result = subprocess.run(
        ["forgetless", "get", result["capsule"]],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert get_result.returncode == 0
    assert "objetivo do teste" in get_result.stdout


def test_seal_epoch_updates_existing_capsule_second_time(tmp_burnless, monkeypatch):
    """Update capsule on second seal with same host_session_id."""
    tmp_path, burnless_dir = tmp_burnless
    root = tmp_path

    # Set isolated FORGETLESS_ROOT
    fake_forgetless_home = tmp_path / "fake-forgetless-home"
    fake_forgetless_home.mkdir()
    monkeypatch.setenv("FORGETLESS_ROOT", str(fake_forgetless_home))

    # First seal
    recovery.write_checkpoint(
        root,
        host="claude",
        host_session_id="abcdefgh-1234",
        process_instance_id="proc-1",
        living_md="## Foco atual\n- primeiro objetivo\n",
        harvested_state={},
        applied_through=10,
        journal_head=5,
    )
    result1 = sealing.seal_epoch(root, host="claude", host_session_id="abcdefgh-1234")
    assert result1["status"] == "sealed"
    assert result1["mode"] == "new"

    # Second seal with updated checkpoint
    recovery.write_checkpoint(
        root,
        host="claude",
        host_session_id="abcdefgh-1234",
        process_instance_id="proc-1",
        living_md="## Foco atual\n- segundo objetivo\n",
        harvested_state={},
        applied_through=15,
        journal_head=8,
    )
    result2 = sealing.seal_epoch(root, host="claude", host_session_id="abcdefgh-1234")
    assert result2["status"] == "sealed"
    assert result2["mode"] == "update"


def test_seal_epoch_empty_living_md_skips(tmp_burnless):
    """Skip sealing when living_md is empty."""
    tmp_path, burnless_dir = tmp_burnless
    root = tmp_path

    # Write checkpoint with empty living_md
    recovery.write_checkpoint(
        root,
        host="claude",
        host_session_id="abcdefgh-1234",
        process_instance_id="proc-1",
        living_md="",
        harvested_state={},
        applied_through=10,
        journal_head=5,
    )

    result = sealing.seal_epoch(root, host="claude", host_session_id="abcdefgh-1234")
    assert result["status"] == "seal_skipped"
    assert result["reason"] == "empty_living_md"


def test_seal_epoch_missing_forgetless_binary_skips(tmp_burnless, monkeypatch):
    """Skip sealing when forgetless binary is not found."""
    tmp_path, burnless_dir = tmp_burnless
    root = tmp_path

    # Write valid checkpoint
    recovery.write_checkpoint(
        root,
        host="claude",
        host_session_id="abcdefgh-1234",
        process_instance_id="proc-1",
        living_md="## Foco atual\n- objetivo\n",
        harvested_state={},
        applied_through=10,
        journal_head=5,
    )

    # Monkeypatch _forgetless_binary to return None
    monkeypatch.setattr(sealing, "_forgetless_binary", lambda: None)

    result = sealing.seal_epoch(root, host="claude", host_session_id="abcdefgh-1234")
    assert result["status"] == "seal_skipped"
    assert result["reason"] == "forgetless_not_found"


def test_seal_epoch_never_raises_on_forgetless_error(tmp_burnless, monkeypatch):
    """Never raise exception on forgetless subprocess error."""
    tmp_path, burnless_dir = tmp_burnless
    root = tmp_path

    # Write valid checkpoint
    recovery.write_checkpoint(
        root,
        host="claude",
        host_session_id="abcdefgh-1234",
        process_instance_id="proc-1",
        living_md="## Foco atual\n- objetivo\n",
        harvested_state={},
        applied_through=10,
        journal_head=5,
    )

    # Mock subprocess.run to always fail
    def mock_run(*args, **kwargs):
        fake_result = Mock()
        fake_result.returncode = 1
        fake_result.stderr = "boom"
        return fake_result

    monkeypatch.setattr(sealing.subprocess, "run", mock_run)

    # Should not raise, should return seal_skipped
    result = sealing.seal_epoch(root, host="claude", host_session_id="abcdefgh-1234")
    assert result["status"] == "seal_skipped"
    assert result["reason"] == "forgetless_error"
