from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .brain_adapters import BrainAdapter

logger = logging.getLogger(__name__)

_DEFAULT_IDLE_THRESHOLD_S = 3000  # 50 min
_POLL_INTERVAL_S = 30
_DEFAULT_MAX_PINGS = 24
_DEFAULT_MAX_CONSECUTIVE_MISSES = 3


def keepalive_enabled_by_default(adapter: BrainAdapter | None) -> bool:
    if adapter is None:
        return False
    if adapter.kind != "anthropic":
        return False
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


class KeepaliveDaemon(threading.Thread):
    def __init__(
        self,
        *,
        state_path: Path,
        cfg: dict,
        adapter: BrainAdapter | None,
        system_prefix: list[dict[str, Any]],
        inflight_lock: threading.Lock,
        model: str | None = None,
    ) -> None:
        super().__init__(name="keepalive-daemon", daemon=True)
        self._state_path = state_path
        self._cfg = cfg
        self._adapter = adapter
        self._system_prefix = system_prefix
        self._inflight_lock = inflight_lock
        self._model = model
        self.stop_event = threading.Event()
        self._pings_sent = 0
        self._consecutive_misses = 0

    def run(self) -> None:
        while not self.stop_event.wait(timeout=_POLL_INTERVAL_S):
            try:
                self._maybe_ping()
            except Exception as exc:
                logger.warning("keepalive: unexpected error: %s", exc)

    def stop(self) -> None:
        self.stop_event.set()

    def _maybe_ping(self) -> None:
        from . import state as state_mod

        ka_cfg = self._cfg.get("keepalive") or {}
        enabled = ka_cfg.get("enabled", keepalive_enabled_by_default(self._adapter))
        if not enabled:
            return

        max_pings = ka_cfg.get("max_pings_per_session", _DEFAULT_MAX_PINGS)
        if self._pings_sent >= max_pings:
            return

        st = state_mod.load(self._state_path)

        if not self._system_prefix and not st.get("active_session_id"):
            return

        idle_threshold_s = ka_cfg.get("idle_threshold_s", _DEFAULT_IDLE_THRESHOLD_S)
        last_activity = st.get("last_activity_ts")
        if not last_activity:
            return
        try:
            last_ts = datetime.fromisoformat(last_activity)
        except ValueError:
            return
        now = datetime.now(timezone.utc)
        idle_s = (now - last_ts).total_seconds()
        if idle_s < idle_threshold_s:
            return

        next_keepalive = st.get("next_keepalive_ts")
        if next_keepalive:
            try:
                next_ts = datetime.fromisoformat(next_keepalive)
                if now < next_ts - timedelta(seconds=60):
                    return
            except ValueError:
                pass

        acquired = self._inflight_lock.acquire(blocking=False)
        if not acquired:
            return
        try:
            self._send_ping()
        finally:
            self._inflight_lock.release()

    def _send_ping(self) -> None:
        from . import state as state_mod
        from . import metrics as metrics_mod

        if self._adapter is None or self._adapter.kind != "anthropic":
            return

        status = "err:skipped"
        cache_read = 0
        try:
            import anthropic

            client = anthropic.Anthropic()
            model = self._model or self._adapter.default_model or "claude-sonnet-4-6"
            resp = client.messages.create(
                model=model,
                system=self._system_prefix,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
            )
            usage = getattr(resp, "usage", None)
            cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
            if cache_read > 0:
                status = "ok"
                self._consecutive_misses = 0
                self._pings_sent += 1
                logger.debug("keepalive: ping ok, cache_read=%d", cache_read)
            else:
                status = "miss"
                self._pings_sent += 1
                self._consecutive_misses += 1
                logger.warning(
                    "keepalive: cache miss (cache_read=0); consecutive=%d",
                    self._consecutive_misses,
                )
                max_misses = (self._cfg.get("keepalive") or {}).get(
                    "max_consecutive_misses", _DEFAULT_MAX_CONSECUTIVE_MISSES
                )
                if self._consecutive_misses >= max_misses:
                    logger.warning(
                        "keepalive: %d consecutive misses; sleeping 1h",
                        self._consecutive_misses,
                    )
                    self.stop_event.wait(timeout=3600)
        except Exception as exc:
            status = f"err:{type(exc).__name__}"
            logger.warning("keepalive: ping error: %s", exc)

        try:
            metrics_path = self._state_path.parent / "metrics.json"
            cost_usd = cache_read * metrics_mod._CACHE_READ_USD_PER_TOKEN
            metrics_mod.increment_keepalive_ping(
                metrics_path,
                status=status,
                cost_usd=cost_usd,
                cache_read_tokens=cache_read,
            )
        except Exception as exc:
            logger.debug("keepalive: metrics update failed: %s", exc)

        try:
            st = state_mod.load(self._state_path)
            st["keepalive_last_ts"] = datetime.now(timezone.utc).isoformat()
            st["keepalive_last_status"] = status
            state_mod.save(self._state_path, st)
        except Exception as exc:
            logger.warning("keepalive: failed to update state: %s", exc)
