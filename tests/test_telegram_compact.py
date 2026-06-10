from __future__ import annotations
import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _fake_config(provider="ollama-local", model="gemma"):
    return {"encoder": {"provider": provider, "model": model}}


def _patch_config(monkeypatch, provider="ollama-local", model="gemma"):
    cfg = _fake_config(provider, model)
    # patch on the actual modules (telegram_compact imports via `from . import config, paths`)
    monkeypatch.setattr("burnless.config.load", lambda *a, **kw: cfg)
    monkeypatch.setattr("burnless.paths.require_root", lambda: Path("/tmp/fake-burnless"))


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

def test_ollama_provider(monkeypatch):
    _patch_config(monkeypatch, provider="ollama-local", model="gemma")

    raw_response = b'{"response":"<channel|>{\\"i\\":\\"fix\\",\\"r\\":\\"auth\\"}"}'

    class FakeResp:
        def read(self):
            return raw_response
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass

    import urllib.request as _ureq
    monkeypatch.setattr(_ureq, "urlopen", lambda *a, **kw: FakeResp())

    from burnless.telegram_compact import compact_to_telegram
    result = compact_to_telegram("fix the auth module")
    assert result is not None
    parsed = json.loads(result)
    assert parsed["i"] == "fix"
    assert parsed["r"] == "auth"


def test_anthropic_provider(monkeypatch):
    _patch_config(monkeypatch, provider="anthropic", model="claude-haiku-4-5-20251001")

    fake_proc = subprocess.CompletedProcess(
        args=[], returncode=0,
        stdout='{"result":"{\\"i\\":\\"x\\"}"}',
        stderr="",
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: fake_proc)

    from burnless.telegram_compact import compact_to_telegram
    result = compact_to_telegram("do something")
    assert result is not None
    assert json.loads(result) == {"i": "x"}


def test_fail_open_on_error(monkeypatch):
    _patch_config(monkeypatch, provider="ollama-local", model="gemma")

    def _raise(*a, **kw):
        raise OSError("connection refused")

    import urllib.request as _ureq
    monkeypatch.setattr(_ureq, "urlopen", _raise)

    from burnless.telegram_compact import compact_to_telegram
    result = compact_to_telegram("some prompt that would fail")
    assert result is None


def test_passthrough_disabled(monkeypatch):
    _patch_config(monkeypatch, provider="anthropic", model="passthrough")

    from burnless.telegram_compact import compact_to_telegram
    result = compact_to_telegram("anything here")
    assert result is None
