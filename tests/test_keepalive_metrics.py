from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from burnless import metrics as metrics_mod
from burnless.maestro_adapters import MaestroAdapter, MaestroCapabilities
from burnless.keepalive import KeepaliveDaemon


@pytest.fixture(autouse=True)
def mock_oauth_token():
    """Mock OAuth token loading to isolate API key path testing."""
    with patch("burnless.keepalive._load_claude_oauth_token", return_value=None):
        yield


def _make_adapter() -> MaestroAdapter:
    return MaestroAdapter(
        key="anthropic",
        label="Anthropic SDK",
        kind="anthropic",
        capabilities=MaestroCapabilities(single_shot=True),
        status="active",
        api_key_env="ANTHROPIC_API_KEY",
        default_model="claude-sonnet-4-6",
    )


def _fake_usage(cache_read: int = 5000) -> MagicMock:
    usage = MagicMock()
    usage.cache_read_input_tokens = cache_read
    return usage


def _fake_response(cache_read: int = 5000) -> MagicMock:
    resp = MagicMock()
    resp.usage = _fake_usage(cache_read)
    return resp


def test_keepalive_ping_ok_increments_metrics(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text("{}")
    metrics_path = tmp_path / "metrics.json"

    daemon = KeepaliveDaemon(
        state_path=state_path,
        cfg={"keepalive": {"enabled": True}},
        adapter=_make_adapter(),
        system_prefix=[{"type": "text", "text": "sys"}],
        inflight_lock=threading.Lock(),
    )

    with patch("anthropic.Anthropic") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create.return_value = _fake_response(cache_read=8000)
        daemon._send_ping()

    m = metrics_mod.load(metrics_path)
    assert m["keepalive_pings_ok"] == 1, "pings_ok should be 1 after an ok ping"
    assert m["keepalive_pings_total"] == 1
    assert m["keepalive_cost_usd"] > 0, "cost_usd should be > 0 for cache_read > 0"
    assert m["by_source"]["keepalive_cache_renewed"] == 8000


def test_keepalive_ping_miss_increments_miss_counter(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text("{}")
    metrics_path = tmp_path / "metrics.json"

    daemon = KeepaliveDaemon(
        state_path=state_path,
        cfg={"keepalive": {"enabled": True}},
        adapter=_make_adapter(),
        system_prefix=[],
        inflight_lock=threading.Lock(),
    )

    with patch("anthropic.Anthropic") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.messages.create.return_value = _fake_response(cache_read=0)
        daemon._send_ping()

    m = metrics_mod.load(metrics_path)
    assert m["keepalive_pings_miss"] == 1
    assert m["keepalive_pings_ok"] == 0
    assert m["keepalive_cost_usd"] == 0.0


def test_keepalive_ping_err_increments_err_counter(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text("{}")
    metrics_path = tmp_path / "metrics.json"

    daemon = KeepaliveDaemon(
        state_path=state_path,
        cfg={"keepalive": {"enabled": True}},
        adapter=_make_adapter(),
        system_prefix=[],
        inflight_lock=threading.Lock(),
    )

    with patch("anthropic.Anthropic") as mock_cls:
        mock_cls.side_effect = RuntimeError("network error")
        daemon._send_ping()

    m = metrics_mod.load(metrics_path)
    assert m["keepalive_pings_err"] == 1
    assert m["keepalive_pings_ok"] == 0
