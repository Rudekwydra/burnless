"""RM-2: living_rewriter endpoint/timeout must come from config
(encoder.endpoint / encoder.timeout_s), with BURNLESS_LOCAL_API as an
override — not the only path."""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _project_with_encoder(tmp_path: Path, encoder_yaml: str) -> Path:
    project = tmp_path / "proj"
    (project / ".burnless").mkdir(parents=True)
    (project / ".burnless" / "config.yaml").write_text(encoder_yaml, encoding="utf-8")
    return project


def test_rewriter_uses_config_timeout_and_endpoint(tmp_path, monkeypatch):
    from burnless import epochs_v2

    project = _project_with_encoder(
        tmp_path,
        "encoder:\n"
        "  provider: ollama-local\n"
        "  model: gemma\n"
        "  endpoint: http://localhost:9999/api/generate\n"
        "  timeout_s: 7\n",
    )

    seen: dict = {}

    def fake_urlopen(req, timeout=None):
        seen["timeout"] = timeout
        seen["url"] = req.full_url
        return _FakeResponse(json.dumps({"response": "## Foco atual\n- ok\n"}).encode())

    monkeypatch.delenv("BURNLESS_LOCAL_API", raising=False)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    out = epochs_v2.living_rewriter(project)("prompt qualquer")
    assert seen["timeout"] == 7
    assert seen["url"] == "http://localhost:9999/api/generate"
    assert out and "ok" in out


def test_rewriter_default_timeout_is_realistic(tmp_path, monkeypatch):
    """Cold local models cannot answer a ~2.5k-token prompt in 20s; the
    default must be >= 90s (RM-2)."""
    from burnless import epochs_v2

    project = _project_with_encoder(
        tmp_path,
        "encoder:\n  provider: ollama-local\n  model: gemma\n",
    )

    seen: dict = {}

    def fake_urlopen(req, timeout=None):
        seen["timeout"] = timeout
        return _FakeResponse(json.dumps({"response": "x"}).encode())

    monkeypatch.delenv("BURNLESS_LOCAL_API", raising=False)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    epochs_v2.living_rewriter(project)("prompt")
    assert seen["timeout"] >= 90
