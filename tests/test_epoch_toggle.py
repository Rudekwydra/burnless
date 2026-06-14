from __future__ import annotations

import argparse
import inspect
import tempfile
from pathlib import Path

import pytest

from burnless.epochs import is_enabled, set_enabled, epoch_dir
from burnless import cli


@pytest.fixture
def tmp_root():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


def test_default_off(tmp_root):
    assert is_enabled(tmp_root) is False


def test_marker_on_off(tmp_root):
    assert set_enabled(tmp_root, True) is True
    assert is_enabled(tmp_root) is True
    assert set_enabled(tmp_root, False) is False
    assert is_enabled(tmp_root) is False


def test_config_flag_on(tmp_root):
    assert is_enabled(tmp_root, {"epochs": {"enabled": True}}) is True


def test_cmd_epoch_on_status(tmp_root, capsys):
    args_on = argparse.Namespace(epoch_cmd="on", root=str(tmp_root), chat_id=None)
    rc = cli.cmd_epoch(args_on)
    assert rc == 0
    marker = tmp_root / ".burnless" / "epochs.on"
    assert marker.exists(), "marker not created by epoch on"

    args_status = argparse.Namespace(epoch_cmd="status", root=str(tmp_root), chat_id=None)
    rc2 = cli.cmd_epoch(args_status)
    assert rc2 == 0
    out = capsys.readouterr().out
    assert "ON" in out

