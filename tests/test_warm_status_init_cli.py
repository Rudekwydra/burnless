"""Regression tests for 2026-07-02 audit findings #7 and #11.

#7  (important): `burnless warm status` called ws[.codex].status(root) with
    model=None (multi-model shape: {model: status_dict}) but then checked
    status_result.get("exists") on the TOP-LEVEL dict — a key that only
    exists on the single-model shape. Result: it always printed
    "NOT INITIALIZED" after the per-(provider,model) warm pool refactor,
    even when warm files existed on disk. cmd_warm_explain() already
    handled both shapes; cmd_warm_status() now mirrors it.
#11 (minor): `burnless warm init --provider both --model X` applied the
    SAME model id to both the claude and codex CLIs, which is almost never
    correct (they use disjoint model namespaces).
"""
import io
import types
from contextlib import redirect_stdout

import burnless.cli as cli
import burnless.warm_session as ws
import burnless.warm_session_codex as ws_codex


_MULTI_MODEL_STATUS = {
    "gpt-5.5": {
        "exists": True,
        "uuid": "abcd1234efgh5678",
        "alive": True,
        "needs_refresh": False,
        "age_s": 42.0,
        "last_cache_ratio": 0.8,
        "project_root": "/x",
        "created_at": "2026-07-02T00:00:00Z",
        "last_used": "2026-07-02T00:00:42Z",
    }
}

_EMPTY_STATUS: dict = {}

_SINGLE_MODEL_STATUS_MISSING = {"exists": False}


class TestWarmStatusMultiModel:
    def test_status_prints_details_for_existing_multi_model_pool(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli, "_resolve_burnless_root", lambda: tmp_path)
        monkeypatch.setattr(ws, "status", lambda root: _EMPTY_STATUS)
        monkeypatch.setattr(ws_codex, "status", lambda root: _MULTI_MODEL_STATUS)
        args = types.SimpleNamespace(provider="both")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.cmd_warm_status(args)
        out = buf.getvalue()
        assert rc == 0
        # claude legitimately has no warm files in this fixture — that block
        # correctly says NOT INITIALIZED. The bug was codex's POPULATED
        # multi-model dict also always saying NOT INITIALIZED because the
        # code checked the wrong shape.
        assert "warm session [codex]: NOT INITIALIZED" not in out
        assert "warm session [codex/gpt-5.5]" in out
        assert "abcd1234efgh5678" in out
        assert "age_s:" in out

    def test_status_reports_not_initialized_when_truly_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli, "_resolve_burnless_root", lambda: tmp_path)
        monkeypatch.setattr(ws, "status", lambda root: _EMPTY_STATUS)
        monkeypatch.setattr(ws_codex, "status", lambda root: _EMPTY_STATUS)
        args = types.SimpleNamespace(provider="both")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.cmd_warm_status(args)
        out = buf.getvalue()
        assert rc == 0
        assert "warm session [claude]: NOT INITIALIZED" in out
        assert "warm session [codex]: NOT INITIALIZED" in out

    def test_status_still_handles_legacy_single_model_shape(self, tmp_path, monkeypatch):
        """Backward compat: a status() implementation that still returns the
        single-model {'exists': ...} shape directly must keep working."""
        single = dict(_MULTI_MODEL_STATUS["gpt-5.5"])
        single["model"] = "gpt-5.5"
        monkeypatch.setattr(cli, "_resolve_burnless_root", lambda: tmp_path)
        monkeypatch.setattr(ws, "status", lambda root: _SINGLE_MODEL_STATUS_MISSING)
        monkeypatch.setattr(ws_codex, "status", lambda root: single)
        args = types.SimpleNamespace(provider="both")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.cmd_warm_status(args)
        out = buf.getvalue()
        assert rc == 0
        assert "warm session [claude]: NOT INITIALIZED" in out
        assert "warm session [codex/gpt-5.5]" in out


class TestWarmInitProviderBothModelAmbiguity:
    def test_provider_both_with_bare_model_is_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli, "_resolve_burnless_root", lambda: tmp_path)
        args = types.SimpleNamespace(provider="both", model="gpt-5.5", claude_model=None, codex_model=None)
        buf = io.StringIO()
        with redirect_stdout(io.StringIO()):
            rc = cli.cmd_warm_init(args)
        assert rc == 2

    def test_provider_both_with_per_provider_models_routes_correctly(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cli, "_resolve_burnless_root", lambda: tmp_path)
        seen = {}

        def fake_claude_init(root, *, model):
            seen["claude"] = model
            return {"uuid": "claude-uuid-1234", "init_usage": {}}

        def fake_codex_init(root, *, model):
            seen["codex"] = model
            return {"uuid": "codex-uuid-5678", "init_usage": {"cached": 0}}

        monkeypatch.setattr(ws, "init", fake_claude_init)
        monkeypatch.setattr(ws_codex, "init", fake_codex_init)

        args = types.SimpleNamespace(
            provider="both", model=None, claude_model="claude-sonnet-4-6", codex_model="gpt-5.5",
        )
        with redirect_stdout(io.StringIO()):
            rc = cli.cmd_warm_init(args)

        assert rc == 0
        assert seen["claude"] == "claude-sonnet-4-6"
        assert seen["codex"] == "gpt-5.5"

    def test_single_provider_still_accepts_bare_model(self, tmp_path, monkeypatch):
        """--provider codex --model X is unambiguous and must keep working."""
        monkeypatch.setattr(cli, "_resolve_burnless_root", lambda: tmp_path)
        seen = {}

        def fake_codex_init(root, *, model):
            seen["codex"] = model
            return {"uuid": "codex-uuid-5678", "init_usage": {"cached": 0}}

        monkeypatch.setattr(ws_codex, "init", fake_codex_init)

        args = types.SimpleNamespace(provider="codex", model="gpt-5.5", claude_model=None, codex_model=None)
        with redirect_stdout(io.StringIO()):
            rc = cli.cmd_warm_init(args)

        assert rc == 0
        assert seen["codex"] == "gpt-5.5"
