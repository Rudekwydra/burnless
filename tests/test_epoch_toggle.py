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


def test_default_on(tmp_root):
    assert is_enabled(tmp_root) is True


def test_marker_on_off(tmp_root):
    assert is_enabled(tmp_root) is True
    assert set_enabled(tmp_root, False) is False
    assert is_enabled(tmp_root) is False
    assert set_enabled(tmp_root, True) is True
    assert is_enabled(tmp_root) is True


def test_config_flag_off(tmp_root):
    assert is_enabled(tmp_root, {"epochs": {"enabled": False}}) is False


def test_cmd_epoch_off_on_status(tmp_root, capsys):
    args_off = argparse.Namespace(epoch_cmd="off", root=str(tmp_root), chat_id=None)
    assert cli.cmd_epoch(args_off) == 0
    marker = tmp_root / ".burnless" / "epochs.off"
    assert marker.exists(), "off marker not created by epoch off"
    args_status = argparse.Namespace(epoch_cmd="status", root=str(tmp_root), chat_id=None)
    assert cli.cmd_epoch(args_status) == 0
    assert "OFF" in capsys.readouterr().out
    args_on = argparse.Namespace(epoch_cmd="on", root=str(tmp_root), chat_id=None)
    assert cli.cmd_epoch(args_on) == 0
    assert not marker.exists(), "off marker not removed by epoch on"
    assert cli.cmd_epoch(args_status) == 0
    assert "ON" in capsys.readouterr().out
