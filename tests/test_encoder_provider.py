"""Tests for compress_transcript encoder provider selection (anthropic vs ollama-local)."""
import types
import pytest

from burnless import compression
from burnless import config
from burnless.codec.cipher import unpack


TEXT = (
    "We decided to ship the feature on Friday. "
    "Next step: write migration script and notify QA team. "
    "Risk: DB lock during peak hours — schedule for off-peak window at 2 AM."
    * 10
)


def _make_anthropic_stub(recorded):
    """Return a fake anthropic module whose client records model used."""

    class FakeContent:
        text = "SEM compressed"

    class FakeResponse:
        content = [FakeContent()]

    class FakeMessages:
        def create(self, **kwargs):
            recorded["model"] = kwargs.get("model")
            return FakeResponse()

    class FakeClient:
        messages = FakeMessages()

    class FakeAnthropic:
        def Anthropic(self):
            return FakeClient()

    return FakeAnthropic()


def test_default_anthropic_unchanged(monkeypatch):
    """encoder=None → anthropic path, uses config.HAIKU_MODEL."""
    recorded = {}
    monkeypatch.setattr(compression, "anthropic", _make_anthropic_stub(recorded))

    capsule, stats = compression.compress_transcript(TEXT, mode="balanced")

    assert recorded.get("model") == config.HAIKU_MODEL
    assert capsule.startswith("burnless:")
    assert stats["mode"] == "balanced"


def test_ollama_local_routes_subprocess(monkeypatch):
    """encoder=ollama-local → subprocess path, correct cmd, capsule returned."""
    called_with = {}

    def fake_run(cmd, **kwargs):
        called_with["cmd"] = cmd
        result = types.SimpleNamespace(returncode=0, stdout="CMP", stderr="")
        return result

    monkeypatch.setattr(compression, "subprocess", types.SimpleNamespace(run=fake_run))

    capsule, stats = compression.compress_transcript(
        TEXT,
        mode="balanced",
        encoder={"provider": "ollama-local", "model": "gemma-x"},
    )

    assert called_with.get("cmd") == ["ollama", "run", "gemma-x"], called_with
    assert capsule.startswith("burnless:")
    # unpack to verify the compressed payload is accessible
    _sid, _key, _ct = unpack(capsule)


def test_ollama_failure_falls_back_to_minify(monkeypatch):
    """ollama returncode!=0 → graceful degrade, no exception, capsule returned."""

    def fake_run_fail(cmd, **kwargs):
        return types.SimpleNamespace(returncode=1, stdout="", stderr="connection refused")

    monkeypatch.setattr(compression, "subprocess", types.SimpleNamespace(run=fake_run_fail))

    # Must not raise
    capsule, stats = compression.compress_transcript(
        TEXT,
        mode="balanced",
        encoder={"provider": "ollama-local", "model": "gemma-x"},
    )

    assert capsule.startswith("burnless:")
    assert stats["mode"] == "balanced"


def test_ollama_exception_falls_back_to_minify(monkeypatch):
    """subprocess.run raises → graceful degrade, capsule returned."""

    def fake_run_exc(cmd, **kwargs):
        raise OSError("ollama not found")

    monkeypatch.setattr(compression, "subprocess", types.SimpleNamespace(run=fake_run_exc))

    capsule, stats = compression.compress_transcript(
        TEXT,
        mode="balanced",
        encoder={"provider": "ollama-local", "model": "gemma-x"},
    )

    assert capsule.startswith("burnless:")
