from __future__ import annotations

import json
import inspect
import tempfile
import time
from pathlib import Path

from burnless.pilot import hud


def test_hud_title_with_savings_and_worker():
    """Test hud_title outputs tokens + worker count when data present."""
    with tempfile.TemporaryDirectory() as tmp_home_dir, tempfile.TemporaryDirectory() as tmp_project_dir:
        tmp_home = Path(tmp_home_dir)
        tmp_project = Path(tmp_project_dir)

        savings_path = tmp_home / ".burnless" / "state"
        savings_path.mkdir(parents=True)
        savings_json = savings_path / "savings.json"
        savings_json.write_text(json.dumps({
            "workers": {"tokens_offloaded": 1000000, "usd_avoided": 5.0},
            "capsules": {"reuse_tokens_avoided": 500000},
            "clear": {"context_avoided_total": 1500000},
        }))

        logs_dir = tmp_project / ".burnless" / "logs"
        logs_dir.mkdir(parents=True)
        (logs_dir / "d1.log").touch()
        (logs_dir / "d2.log").touch()

        result = hud.hud_title(tmp_project, home=tmp_home)
        assert "burnless" in result
        assert "3.0M" in result
        assert "worker" in result


def test_hud_title_failopen_no_files():
    """Test hud_title returns 'burnless' when files missing."""
    with tempfile.TemporaryDirectory() as tmp_home_dir, tempfile.TemporaryDirectory() as tmp_project_dir:
        tmp_home = Path(tmp_home_dir)
        tmp_project = Path(tmp_project_dir)

        result = hud.hud_title(tmp_project, home=tmp_home)
        assert result == "burnless"


def test_osc_title_sanitizes():
    """Test osc_title strips control chars and wraps correctly."""
    text = f"test{chr(27)}data{chr(7)}"
    result = hud.osc_title(text)

    assert result.startswith(b"\x1b]0;")
    assert result.endswith(b"\x07")
    assert result.count(b"\x1b") == 1
    assert result.count(b"\x07") == 1


def test_run_pilot_accepts_title_kwargs():
    """Test run_pilot signature includes title_provider and title_interval_s."""
    from burnless.pilot.pty_relay import run_pilot

    sig = inspect.signature(run_pilot)
    assert "title_provider" in sig.parameters
    assert "title_interval_s" in sig.parameters
    assert sig.parameters["title_interval_s"].default == 5.0
