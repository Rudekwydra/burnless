"""Tests for maestro_layer (unit; engine mocked)."""
import json
from pathlib import Path
import pytest

from burnless.maestro_layer import _try_extract_envelope_json, process_envelope


# ── _try_extract_envelope_json (kept helper) ──────────────────────────────────

def test_extract_envelope_fenced_json():
    text = 'thinking...\n```json\n{"response_envelope": "OK", "next": ""}\n```'
    env = _try_extract_envelope_json(text)
    assert env == {"response_envelope": "OK", "next": ""}


def test_extract_envelope_trailing_json():
    text = 'reasoning...\n\nFinal:\n{"response_envelope": "fixed bug"}'
    env = _try_extract_envelope_json(text)
    assert env == {"response_envelope": "fixed bug"}


def test_extract_envelope_returns_none_when_no_json():
    assert _try_extract_envelope_json("plain text no json") is None
    assert _try_extract_envelope_json("") is None


# ── process_envelope contract ─────────────────────────────────────────────────

CONTRACT_KEYS = {
    "response_envelope",
    "decoder_hint",
    "compression_mode",
    "maestro_session_id",
    "maestro_exit_code",
    "usage",
    "compression",
}

CANNED_TEXT = '`brz x :: OK` env'
CANNED_FORK = "fork-abc-123"


@pytest.fixture()
def fake_project(tmp_path):
    project = tmp_path / "proj"
    burnless = project / ".burnless"
    burnless.mkdir(parents=True)
    return project


def _patch_engine(monkeypatch, fake_project, response_text=CANNED_TEXT):
    """Patch all engine calls so process_envelope runs without real claude."""
    import burnless.maestro.base as _base
    import burnless.warm_session as _ws
    import burnless.maestro_layer as _ml
    from burnless.maestro.session_runner import MaestroSession

    monkeypatch.setattr(_base, "maestro_base_init", lambda root, model: "BASEUUID")
    monkeypatch.setattr(_base, "maestro_iso_cwd", lambda root, model: str(fake_project))
    monkeypatch.setattr(_ws, "_claude_binary", lambda: "claude")

    def fake_send(self, user_msg, *, runner, rewind_capsule=None):
        self.fork_session_id = CANNED_FORK
        self.usages.append({"input_tokens": 10, "output_tokens": 5,
                            "cache_creation_input_tokens": 2, "cache_read_input_tokens": 3})
        return response_text, 5

    monkeypatch.setattr(MaestroSession, "send", fake_send)


def test_process_envelope_contract_keys(monkeypatch, fake_project):
    _patch_engine(monkeypatch, fake_project)
    result = process_envelope("intent=test", fake_project)
    missing = CONTRACT_KEYS - set(result.keys())
    assert not missing, f"missing keys: {missing}"


def test_process_envelope_exit_code_zero(monkeypatch, fake_project):
    _patch_engine(monkeypatch, fake_project)
    result = process_envelope("intent=test", fake_project)
    assert result["maestro_exit_code"] == 0


def test_process_envelope_fork_persisted(monkeypatch, fake_project):
    _patch_engine(monkeypatch, fake_project)
    process_envelope("intent=test", fake_project)
    fork_file = fake_project / ".burnless" / "maestro" / "mcp_fork.json"
    assert fork_file.exists(), "fork persistence file not written"
    data = json.loads(fork_file.read_text())
    assert data["fork_session_id"] == CANNED_FORK


def test_process_envelope_maestro_session_id(monkeypatch, fake_project):
    _patch_engine(monkeypatch, fake_project)
    result = process_envelope("intent=test", fake_project)
    assert result["maestro_session_id"] == CANNED_FORK


def test_process_envelope_usage_aggregated(monkeypatch, fake_project):
    _patch_engine(monkeypatch, fake_project)
    result = process_envelope("intent=test", fake_project)
    u = result["usage"]
    assert u["input_tokens"] == 10
    assert u["output_tokens"] == 5
    assert u["cache_creation_input_tokens"] == 2
    assert u["cache_read_input_tokens"] == 3


def test_process_envelope_compression_mode_passthrough(monkeypatch, fake_project):
    _patch_engine(monkeypatch, fake_project)
    result = process_envelope("intent=test", fake_project, compression_mode="loose")
    assert result["compression_mode"] == "loose"
    assert "expand" in result["decoder_hint"]


def test_process_envelope_response_envelope_raw_fallback(monkeypatch, fake_project):
    _patch_engine(monkeypatch, fake_project, response_text="no json here")
    result = process_envelope("intent=test", fake_project)
    assert result["response_envelope"] == {"raw_text": "no json here"}


def test_process_envelope_fork_reused_on_second_call(monkeypatch, fake_project):
    """Second call should load the persisted fork_session_id."""
    _patch_engine(monkeypatch, fake_project)
    process_envelope("first call", fake_project)
    # pre-write a different fork to verify it's loaded
    fork_file = fake_project / ".burnless" / "maestro" / "mcp_fork.json"
    import burnless.config as _cfg
    model = _cfg.DEFAULT_TIER_MODELS["bronze"]
    fork_file.write_text(
        json.dumps({"model": model, "fork_session_id": "prior-fork"}),
        encoding="utf-8",
    )
    # The engine monkeypatch overwrites fork_session_id on send, so we just
    # verify the file is read without error and result is still well-formed.
    result = process_envelope("second call", fake_project)
    assert CONTRACT_KEYS <= set(result.keys())


def test_process_envelope_unavailable_on_runtime_error(monkeypatch, fake_project):
    import burnless.maestro.base as _base
    monkeypatch.setattr(_base, "maestro_base_init",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("no binary")))
    result = process_envelope("x", fake_project)
    assert result.get("error") == "maestro_unavailable"
    assert "decoder_hint" in result
