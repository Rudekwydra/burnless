"""Unit tests for warm_session.explain() and warm_session_codex.explain()."""
import pytest
import burnless.warm_session as ws
import burnless.warm_session_codex as wsc


class TestWarmSessionExplain:
    """Tests for warm_session.explain() function."""

    def test_explain_fresh(self, monkeypatch, tmp_path):
        """Fresh cache: age < 59 min, alive -> ttl_status='fresh'."""
        monkeypatch.setattr(
            ws, "status",
            lambda *a, **k: {
                "exists": True,
                "uuid": "abcd1234efgh",
                "alive": True,
                "age_minutes": 5.0,
                "cache_read": 10,
                "cache_write": 2,
            }
        )
        result = ws.explain(tmp_path, model="m")
        assert result["ttl_status"] == "fresh"
        assert result["uuid_prefix"] == "abcd1234"
        assert result["provider"] == "claude"
        assert result["model"] == "m"
        assert result["ttl_remaining_min"] == 55.0
        assert "hot" in result["compaction_caution"]

    def test_explain_aging(self, monkeypatch, tmp_path):
        """Aging cache: 59 <= age < 60 min, alive -> ttl_status='aging'."""
        monkeypatch.setattr(
            ws, "status",
            lambda *a, **k: {
                "exists": True,
                "uuid": "xyz9876abcd",
                "alive": True,
                "age_minutes": 59.5,
            }
        )
        result = ws.explain(tmp_path, model="test_model")
        assert result["ttl_status"] == "aging"
        assert result["uuid_prefix"] == "xyz9876a"
        assert "hot" in result["compaction_caution"]

    def test_explain_expired_by_age(self, monkeypatch, tmp_path):
        """Expired cache: age >= 60 min, alive=True -> ttl_status='expired'."""
        monkeypatch.setattr(
            ws, "status",
            lambda *a, **k: {
                "exists": True,
                "uuid": "old_uuid01",
                "alive": True,
                "age_minutes": 70.0,
            }
        )
        result = ws.explain(tmp_path, model="old")
        assert result["ttl_status"] == "expired"
        assert "cold" in result["compaction_caution"]
        assert result["ttl_remaining_min"] == 0.0

    def test_explain_expired_when_dead(self, monkeypatch, tmp_path):
        """Expired cache: alive=False, age < 60 -> ttl_status='expired'."""
        monkeypatch.setattr(
            ws, "status",
            lambda *a, **k: {
                "exists": True,
                "uuid": "dead_uuid",
                "alive": False,
                "age_minutes": 5.0,
            }
        )
        result = ws.explain(tmp_path, model="dead")
        assert result["ttl_status"] == "expired"
        assert "cold" in result["compaction_caution"]

    def test_explain_absent(self, monkeypatch, tmp_path):
        """Non-existent cache: status returns exists=False."""
        monkeypatch.setattr(
            ws, "status",
            lambda *a, **k: {"exists": False}
        )
        result = ws.explain(tmp_path, model="absent")
        assert result["exists"] is False
        assert result["provider"] == "claude"
        assert result["model"] == "absent"

    def test_explain_age_none(self, monkeypatch, tmp_path):
        """Age is None -> ttl_status='expired', ttl_remaining_min=0.0."""
        monkeypatch.setattr(
            ws, "status",
            lambda *a, **k: {
                "exists": True,
                "uuid": "test_uuid",
                "alive": True,
                "age_minutes": None,
            }
        )
        result = ws.explain(tmp_path, model="m")
        assert result["ttl_status"] == "expired"
        assert result["ttl_remaining_min"] == 0.0

    def test_explain_defaults_cache_read_write(self, monkeypatch, tmp_path):
        """Missing cache_read/cache_write in status -> defaults to 0."""
        monkeypatch.setattr(
            ws, "status",
            lambda *a, **k: {
                "exists": True,
                "uuid": "test",
                "alive": True,
                "age_minutes": 10.0,
            }
        )
        result = ws.explain(tmp_path, model="m")
        assert result["cache_read"] == 0
        assert result["cache_write"] == 0


class TestWarmSessionCodexExplain:
    """Tests for warm_session_codex.explain() function."""

    def test_explain_fresh(self, monkeypatch, tmp_path):
        """Fresh codex cache: age well under HEARTBEAT_INTERVAL_S -> ttl_status='fresh'.

        Regression guard for the TTL/heartbeat contradiction bug: explain()
        must derive its thresholds from the real TTL_S/HEARTBEAT_INTERVAL_S
        constants, not a hardcoded 60-minute expectation that never matched
        the 5-minute TTL actually enforced by is_alive()/needs_refresh()."""
        age_s = 10.0
        assert age_s < wsc.HEARTBEAT_INTERVAL_S, "test fixture must land in the 'fresh' window"
        monkeypatch.setattr(
            wsc, "status",
            lambda *a, **k: {
                "exists": True,
                "uuid": "zzzz9999",
                "alive": True,
                "age_s": age_s,
                "last_cache_ratio": 0.85,
            }
        )
        result = wsc.explain(tmp_path, model="m")
        assert result["ttl_status"] == "fresh"
        assert result["provider"] == "codex"
        assert result["uuid_prefix"] == "zzzz9999"
        assert result["model"] == "m"
        expected_remaining = (wsc.TTL_S - age_s) / 60.0
        assert result["ttl_remaining_min"] == pytest.approx(expected_remaining, abs=0.05)
        assert "hot" in result["compaction_caution"]

    def test_explain_aging(self, monkeypatch, tmp_path):
        """Aging codex cache: HEARTBEAT_INTERVAL_S <= age < TTL_S -> ttl_status='aging'."""
        age_s = (wsc.HEARTBEAT_INTERVAL_S + wsc.TTL_S) / 2.0
        assert wsc.HEARTBEAT_INTERVAL_S <= age_s < wsc.TTL_S, "test fixture must land in the 'aging' window"
        monkeypatch.setattr(
            wsc, "status",
            lambda *a, **k: {
                "exists": True,
                "uuid": "aging_01",
                "alive": True,
                "age_s": age_s,
            }
        )
        result = wsc.explain(tmp_path, model="test")
        assert result["ttl_status"] == "aging"
        assert "hot" in result["compaction_caution"]

    def test_explain_expired_by_age(self, monkeypatch, tmp_path):
        """Expired codex cache: age_s >= TTL_S -> ttl_status='expired'."""
        age_s = wsc.TTL_S
        monkeypatch.setattr(
            wsc, "status",
            lambda *a, **k: {
                "exists": True,
                "uuid": "expired_x",
                "alive": True,
                "age_s": age_s,
            }
        )
        result = wsc.explain(tmp_path, model="expired")
        assert result["ttl_status"] == "expired"
        assert "cold" in result["compaction_caution"]
        assert result["ttl_remaining_min"] == 0.0

    def test_explain_expired_when_dead(self, monkeypatch, tmp_path):
        """Expired codex cache: alive=False -> ttl_status='expired'."""
        monkeypatch.setattr(
            wsc, "status",
            lambda *a, **k: {
                "exists": True,
                "uuid": "dead_codex",
                "alive": False,
                "age_s": 30.0,
            }
        )
        result = wsc.explain(tmp_path, model="dead")
        assert result["ttl_status"] == "expired"
        assert "cold" in result["compaction_caution"]

    def test_explain_absent(self, monkeypatch, tmp_path):
        """Non-existent codex cache: status returns exists=False."""
        monkeypatch.setattr(
            wsc, "status",
            lambda *a, **k: {"exists": False}
        )
        result = wsc.explain(tmp_path, model="absent")
        assert result["exists"] is False
        assert result["provider"] == "codex"
        assert result["model"] == "absent"

    def test_explain_age_none(self, monkeypatch, tmp_path):
        """Age is None -> ttl_status='expired', ttl_remaining_min=0.0."""
        monkeypatch.setattr(
            wsc, "status",
            lambda *a, **k: {
                "exists": True,
                "uuid": "test_uuid",
                "alive": True,
                "age_s": None,
            }
        )
        result = wsc.explain(tmp_path, model="m")
        assert result["ttl_status"] == "expired"
        assert result["ttl_remaining_min"] == 0.0

    def test_explain_defaults_cache_ratio(self, monkeypatch, tmp_path):
        """Missing last_cache_ratio in status -> defaults to 0.0."""
        monkeypatch.setattr(
            wsc, "status",
            lambda *a, **k: {
                "exists": True,
                "uuid": "test",
                "alive": True,
                "age_s": 120.0,
            }
        )
        result = wsc.explain(tmp_path, model="m")
        assert result["last_cache_ratio"] == 0.0

    def test_explain_provider_tag(self, monkeypatch, tmp_path):
        """Provider field is always 'codex' in codex.explain()."""
        monkeypatch.setattr(
            wsc, "status",
            lambda *a, **k: {
                "exists": True,
                "uuid": "provider_test",
                "alive": True,
                "age_s": 60.0,
            }
        )
        result = wsc.explain(tmp_path, model="any_model")
        assert result["provider"] == "codex"


class TestExplainNoneModel:
    """Tests for explain(burnless_root, model=None) — returns dict of all models."""

    def test_explain_all_models_empty(self, monkeypatch, tmp_path):
        """model=None with no warm files -> returns empty dict."""
        monkeypatch.setattr(ws, "list_warm_files", lambda: [])
        result = ws.explain(tmp_path, model=None)
        assert result == {}

    def test_explain_all_models_codex_empty(self, monkeypatch, tmp_path):
        """codex model=None with no warm files -> returns empty dict."""
        monkeypatch.setattr(wsc, "list_warm_files", lambda: [])
        result = wsc.explain(tmp_path, model=None)
        assert result == {}
