"""Tests for live_runner pump thread drain — verify tail events aren't lost."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import pytest

from burnless import live_runner


@pytest.mark.parametrize("iteration", range(5))
def test_live_runner_drain_final_events(tmp_path: Path, iteration: int):
    """Verify that final buffered events from pump threads are drained and captured.

    Test the race condition fix: when pump threads finish, their final events
    may still be queued. The drain loop after thread.join(timeout=2.0) must
    capture them. Run 5 times to expose intermittent race conditions.
    """
    # Create a Python script that outputs 300 lines + final marker.
    script_path = tmp_path / "tail_test.py"
    script_path.write_text(
        "#!/usr/bin/env python3\n"
        "for i in range(300):\n"
        "    print(f'Line {i:03d}')\n"
        "print('TAIL_MARKER_FIM')\n",
        encoding="utf-8",
    )
    script_path.chmod(0o755)

    # Run the script via run_with_live_panel with mode='plain'.
    log_path = tmp_path / f"d_tail_test_{iteration}.log"
    result = live_runner.run_with_live_panel(
        delegation_id=f"d_tail_test_{iteration}",
        tier="bronze",
        agent_cfg={
            "name": "test",
            "command": str(script_path),
        },
        prompt="",  # No prompt needed for subprocess script
        log_path=log_path,
        mode="plain",
        timeout=10,
    )

    # Verify the final marker is in stdout.
    assert "TAIL_MARKER_FIM" in result.stdout, (
        f"Final marker lost in iteration {iteration}. "
        f"stdout length: {len(result.stdout)}, ends with: {result.stdout[-200:]}"
    )
    # Verify the script actually ran.
    assert "Line 000" in result.stdout, "Script did not execute"
    assert result.returncode == 0, f"Script failed with code {result.returncode}"
