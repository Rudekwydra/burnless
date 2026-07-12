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


def test_llamacpp_sends_system_prompt_via_chat_endpoint(tmp_path, monkeypatch):
    """llamacpp branch must use /v1/chat/completions endpoint with system prompt."""
    from burnless import epochs_v2

    project = _project_with_encoder(
        tmp_path,
        "encoder:\n  provider: ollama-local\n  model: gemma\n",
    )

    seen: dict = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["body"] = json.loads(req.data.decode())
        return _FakeResponse(json.dumps({
            "choices": [{"message": {"content": "## Foco atual\n- x"}}]
        }).encode())

    monkeypatch.setenv("BURNLESS_LOCAL_API", "llamacpp")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    out = epochs_v2.living_rewriter(project)("test prompt")
    assert seen["url"].endswith("/v1/chat/completions")
    assert seen["body"]["messages"][0]["role"] == "system"
    assert epochs_v2.ENCODER_SYSTEM_PROMPT in seen["body"]["messages"][0]["content"]
    assert out and "Foco atual" in out


def test_llamacpp_parse_fallback_legacy_body(tmp_path, monkeypatch):
    """llamacpp must fallback to legacy content/response format if choices is absent."""
    from burnless import epochs_v2

    project = _project_with_encoder(
        tmp_path,
        "encoder:\n  provider: ollama-local\n  model: gemma\n",
    )

    def fake_urlopen(req, timeout=None):
        return _FakeResponse(json.dumps({
            "content": "## Foco atual\n- y"
        }).encode())

    monkeypatch.setenv("BURNLESS_LOCAL_API", "llamacpp")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    out = epochs_v2.living_rewriter(project)("test")
    assert out and "Foco atual" in out


def test_anthropic_timeout_respects_config(tmp_path, monkeypatch):
    """anthropic provider must respect encoder.timeout_s from config."""
    import subprocess
    from burnless import epochs_v2

    project = _project_with_encoder(
        tmp_path,
        "encoder:\n  provider: anthropic\n  model: haiku\n  timeout_s: 7\n",
    )

    seen: dict = {}

    class FakeResult:
        returncode = 0
        stdout = json.dumps({"result": "## Foco atual\n- z"})

    def fake_run(*args, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        return FakeResult()

    monkeypatch.setattr("subprocess.run", fake_run)

    out = epochs_v2.living_rewriter(project)("test")
    assert seen["timeout"] == 7
    assert out and "Foco atual" in out
