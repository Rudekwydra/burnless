from __future__ import annotations

import json
from pathlib import Path

from burnless.init_claude_code import is_wired, wire_settings_hook


def test_wire_settings_hook_installs_sessionend(tmp_path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    (home / ".claude" / "settings.json").write_text("{}", encoding="utf-8")

    status = wire_settings_hook(home)
    assert status == "wired"

    wired = is_wired(home)
    assert wired["sessionend"] is True
    assert wired["epoch_session"] is True

    data = json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))
    commands = [
        hook.get("command", "")
        for group in data.get("hooks", {}).get("SessionEnd", [])
        for hook in group.get("hooks", [])
    ]
    assert any("burnless_epoch_end.sh" in cmd for cmd in commands)

