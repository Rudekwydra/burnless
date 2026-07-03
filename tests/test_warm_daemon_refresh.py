"""Regression tests for the TTL/heartbeat contradiction bug in the warm daemon.

Bug (2026-07-02 deep audit, finding #3): TTL_S and HEARTBEAT_INTERVAL_S were
both 300s, so is_alive() (age < ttl_s) and needs_refresh() (age >= heartbeat)
could never both be true — the daemon never refreshed Codex warm sessions.
"""
from pathlib import Path

import burnless.warm_daemon as warm_daemon
import burnless.warm_session_codex as wsc


def test_codex_heartbeat_interval_below_ttl():
    """Invariant: needs_refresh()'s threshold must fire before is_alive()
    expires, otherwise no age satisfies 'alive and needs_refresh' together."""
    assert wsc.HEARTBEAT_INTERVAL_S < wsc.TTL_S


def test_maybe_refresh_calls_codex_refresh_when_alive_and_due(monkeypatch, tmp_path):
    """At an age between HEARTBEAT_INTERVAL_S and TTL_S, _maybe_refresh must
    call ws_codex.refresh() for the model."""
    age_s = (wsc.HEARTBEAT_INTERVAL_S + wsc.TTL_S) / 2.0
    fake_path = tmp_path / "gpt-5.2.json"
    fake_path.write_text("{}")

    refreshed = []
    monkeypatch.setattr(warm_daemon.ws_codex, "list_warm_files", lambda: [fake_path])
    monkeypatch.setattr(warm_daemon.ws_codex, "is_alive", lambda root, model: age_s < wsc.TTL_S)
    monkeypatch.setattr(warm_daemon.ws_codex, "needs_refresh", lambda root, model: age_s >= wsc.HEARTBEAT_INTERVAL_S)
    monkeypatch.setattr(warm_daemon.ws_codex, "refresh", lambda root, model: refreshed.append(model))
    monkeypatch.setattr(warm_daemon.ws_claude, "list_warm_files", lambda: [])

    warm_daemon._maybe_refresh(tmp_path, {})

    assert refreshed == ["gpt-5.2"]
