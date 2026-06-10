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


def _make_urlopen_stub(response_bytes):
    """Return a fake urlopen that returns a context manager yielding given bytes."""

    class FakeResp:
        def read(self):
            return response_bytes
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass

    def fake_urlopen(req, timeout=None):
        return FakeResp()

    return fake_urlopen


def test_default_anthropic_unchanged(monkeypatch):
    """encoder=None → anthropic path, uses config.HAIKU_MODEL."""
    recorded = {}
    monkeypatch.setattr(compression, "anthropic", _make_anthropic_stub(recorded))

    capsule, stats = compression.compress_transcript(TEXT, mode="balanced")

    assert recorded.get("model") == config.HAIKU_MODEL
    assert capsule.startswith("burnless:")
    assert stats["mode"] == "balanced"


def test_ollama_local_routes_http(monkeypatch):
    """encoder=ollama-local → HTTP API path, capsule returned and unpacks."""
    monkeypatch.setattr(
        compression.urllib.request, "urlopen",
        _make_urlopen_stub(b'{"response":"CMP"}'),
    )

    capsule, stats = compression.compress_transcript(
        TEXT,
        mode="balanced",
        encoder={"provider": "ollama-local", "model": "gemma-x"},
    )

    assert capsule.startswith("burnless:")
    _sid, _key, _ct = unpack(capsule)


def test_ollama_http_strips_channels(monkeypatch):
    """ollama response with harmony channel token → stripped before pack."""
    monkeypatch.setattr(
        compression.urllib.request, "urlopen",
        _make_urlopen_stub(b'{"response":"<channel|>CMP"}'),
    )

    capsule, stats = compression.compress_transcript(
        TEXT,
        mode="balanced",
        encoder={"provider": "ollama-local", "model": "gemma-x"},
    )

    assert capsule.startswith("burnless:")
    from burnless.codec.cipher import decode as cipher_decode
    _sid, key, ciphertext = unpack(capsule)
    decoded = cipher_decode(ciphertext, key)
    assert "<channel" not in decoded


def test_ollama_failure_falls_back_to_minify(monkeypatch):
    """urlopen raises → graceful degrade, no exception, capsule returned."""

    def fake_urlopen_fail(req, timeout=None):
        raise OSError("connection refused")

    monkeypatch.setattr(compression.urllib.request, "urlopen", fake_urlopen_fail)

    capsule, stats = compression.compress_transcript(
        TEXT,
        mode="balanced",
        encoder={"provider": "ollama-local", "model": "gemma-x"},
    )

    assert capsule.startswith("burnless:")
    assert stats["mode"] == "balanced"


def test_ollama_exception_falls_back_to_minify(monkeypatch):
    """urlopen raises (variant) → graceful degrade, capsule returned."""

    def fake_urlopen_exc(req, timeout=None):
        raise OSError("ollama not found")

    monkeypatch.setattr(compression.urllib.request, "urlopen", fake_urlopen_exc)

    capsule, stats = compression.compress_transcript(
        TEXT,
        mode="balanced",
        encoder={"provider": "ollama-local", "model": "gemma-x"},
    )

    assert capsule.startswith("burnless:")


def test_strip_gemma_channels():
    """_strip_gemma_channels drops everything up to and incl the last <channel|>."""
    assert compression._strip_gemma_channels("<|channel>thought\n<channel|>HI") == "HI"
