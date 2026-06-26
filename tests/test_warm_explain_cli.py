import io
import json
import types
from contextlib import redirect_stdout

import burnless.cli as cli
import burnless.events as events
import burnless.session_hud as h
import burnless.warm_session as ws


def test_render_explain_includes_warm():
    out = h.render_explain({})
    assert "Warm session: (none recorded)" in out


def test_render_explain_warm_value():
    out = h.render_explain({"last_warm_status": "claude: fresh"})
    assert "Warm session: claude: fresh" in out


_FAKE_EXPLAIN = {
    "m": {
        "exists": True,
        "model": "m",
        "ttl_status": "fresh",
        "uuid_prefix": "abcd1234",
        "alive": True,
        "needs_refresh": False,
        "ttl_remaining_min": 55.0,
        "cache_read": 1,
        "cache_write": 2,
        "compaction_caution": "warm prefix is hot",
        "project_root": "/x",
    }
}


def test_cmd_warm_explain_json(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_resolve_burnless_root", lambda: tmp_path)
    monkeypatch.setattr(ws, "explain", lambda root, model=None: _FAKE_EXPLAIN)
    args = types.SimpleNamespace(provider="claude", json=True)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli.cmd_warm_explain(args)
    assert rc == 0
    out = buf.getvalue()
    parsed = json.loads(out)
    assert "ttl_status" in out
    assert parsed["claude"]["m"]["ttl_status"] == "fresh"


def test_cmd_warm_explain_event_written(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_resolve_burnless_root", lambda: tmp_path)
    monkeypatch.setattr(ws, "explain", lambda root, model=None: _FAKE_EXPLAIN)
    args = types.SimpleNamespace(provider="claude", json=False)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli.cmd_warm_explain(args)
    assert rc == 0
    evs = events.read_events(tmp_path, event_type="warm_session_status")
    assert len(evs) >= 1
