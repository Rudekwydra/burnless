"""Unified quota usage fetcher (Anthropic).

Used by the PTY footer to display "% of quota used" for monthly plans.
Best-effort: if credentials are missing or headers are not returned, it
returns None and the caller should fall back to other hints.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class UnifiedUsage:
    u5h: float | None = None
    r5h: str | None = None
    u7d: float | None = None
    r7d: str | None = None
    fallback: str | None = None
    fallback_pct: float | None = None
    overage_status: str | None = None
    overage_reason: str | None = None


def _load_api_key_from_env_or_files() -> str | None:
    k = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if k:
        return k
    for p in (
        Path.home() / ".config" / "burnless" / "anthropic.env",
        Path.home() / "antigravity" / "burnless" / ".env",
    ):
        try:
            if not p.exists():
                continue
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if line.startswith("ANTHROPIC_API_KEY="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        return val
        except OSError:
            continue
    return None


def load_claude_oauth_token() -> str | None:
    """Load Claude Code OAuth token from macOS Keychain.

    Supports both formats:
    - a raw token string
    - a JSON envelope containing {"claudeAiOauth":{"accessToken":"..."}}
    """
    try:
        import subprocess

        r = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode != 0:
            return None
        raw = (r.stdout or "").strip()
        if not raw:
            return None
        if raw.lstrip().startswith("{"):
            try:
                obj = json.loads(raw)
                tok = obj.get("claudeAiOauth", {}).get("accessToken")
                return tok if isinstance(tok, str) and tok.strip() else None
            except Exception:
                return None
        return raw
    except Exception:
        return None


def _post_messages(*, headers: dict[str, str], timeout_s: int = 10) -> dict[str, Any]:
    from . import config
    url = "https://api.anthropic.com/v1/messages"
    body = json.dumps(
        {
            "model": config.HAIKU_MODEL,
            "max_tokens": 1,
            "system": "ping",
            "messages": [{"role": "user", "content": "ping"}],
        }
    ).encode("utf-8")
    req = urllib.request.Request(url=url, data=body, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    req.add_header("content-type", "application/json")
    req.add_header("anthropic-version", "2023-06-01")

    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        # We don't care about the body; just ensure request succeeded so headers are meaningful.
        _ = resp.read(1024)
        return dict(resp.headers.items())


def fetch_unified_usage(*, prefer_oauth: bool = True) -> UnifiedUsage | None:
    """Fetch unified usage headers.

    Returns None if headers are not available under current auth.
    """
    headers: dict[str, str] = {}
    if prefer_oauth:
        tok = load_claude_oauth_token()
        if tok:
            headers["authorization"] = f"Bearer {tok}"
    if "authorization" not in headers:
        api_key = _load_api_key_from_env_or_files()
        if not api_key:
            return None
        headers["x-api-key"] = api_key

    try:
        h = _post_messages(headers=headers)
    except Exception:
        return None

    def _get_float(name: str) -> float | None:
        v = h.get(name)
        if v is None:
            return None
        try:
            return float(v)
        except Exception:
            return None

    u = UnifiedUsage(
        u5h=_get_float("anthropic-ratelimit-unified-5h-utilization"),
        r5h=h.get("anthropic-ratelimit-unified-5h-reset"),
        u7d=_get_float("anthropic-ratelimit-unified-7d-utilization"),
        r7d=h.get("anthropic-ratelimit-unified-7d-reset"),
        fallback=h.get("anthropic-ratelimit-unified-fallback"),
        fallback_pct=_get_float("anthropic-ratelimit-unified-fallback-percentage"),
        overage_status=h.get("anthropic-ratelimit-unified-overage-status"),
        overage_reason=h.get("anthropic-ratelimit-unified-overage-disabled-reason"),
    )
    if u.u5h is None and u.u7d is None and u.fallback is None and u.overage_status is None:
        return None
    return u


class UsagePoller:
    """TTL + backoff wrapper around fetch_unified_usage()."""

    def __init__(self, *, ttl_s: int = 60):
        self._ttl_s = max(10, int(ttl_s))
        self._last_ts = 0.0
        self._last: UnifiedUsage | None = None
        self._failures = 0

    def get(self) -> UnifiedUsage | None:
        now = time.time()
        backoff = min(600, (2**self._failures) * self._ttl_s) if self._failures else self._ttl_s
        if now - self._last_ts < backoff:
            return self._last
        self._last_ts = now
        u = fetch_unified_usage()
        if u is None:
            self._failures += 1
            return self._last
        self._failures = 0
        self._last = u
        return u

