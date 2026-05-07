"""Plugin Protocol v0.7 — manifest loader and hook dispatcher."""
from __future__ import annotations

import json
import logging
import subprocess
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_HOOK_TIMEOUT = 5  # seconds


@dataclass
class Plugin:
    name: str
    version: str
    protocol: str  # http | stdio | https
    endpoint: str
    auth: str
    hooks: list[str] = field(default_factory=list)


def load_plugins(burnless_root: Path) -> list[Plugin]:
    plugins_dir = burnless_root / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    plugins: list[Plugin] = []
    for manifest_path in sorted(plugins_dir.glob("*.json")):
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            plugins.append(
                Plugin(
                    name=str(data.get("name") or manifest_path.stem),
                    version=str(data.get("version") or "0.0.0"),
                    protocol=str(data.get("protocol") or "http"),
                    endpoint=str(data.get("endpoint") or ""),
                    auth=str(data.get("auth") or ""),
                    hooks=list(data.get("hooks") or []),
                )
            )
        except Exception as exc:
            logger.warning("plugin_loader: failed to load %s: %s", manifest_path, exc)
    return plugins


def call_hook(plugin: Plugin, hook_name: str, payload: dict) -> dict | None:
    if hook_name not in plugin.hooks:
        return None
    try:
        if plugin.protocol in ("http", "https"):
            return _call_http(plugin, hook_name, payload)
        if plugin.protocol == "stdio":
            return _call_stdio(plugin, hook_name, payload)
        logger.warning("plugin_loader: unknown protocol %r for plugin %r", plugin.protocol, plugin.name)
    except Exception as exc:
        logger.warning("plugin_loader: hook %s in plugin %r failed: %s", hook_name, plugin.name, exc)
    return None


def call_all_plugins(plugins: list[Plugin], hook_name: str, payload: dict) -> dict:
    merged: dict[str, Any] = {}
    for plugin in plugins:
        result = call_hook(plugin, hook_name, payload)
        if result is not None:
            merged.update(result)
    return merged


# ── Session state endpoint (H3) ──────────────────────────────────────────────

def get_session_state(burnless_root: Path) -> dict:
    """Return read-only session state for plugins that pull via H3."""
    from . import state as state_mod
    from .paths import paths_for
    p = paths_for(burnless_root)
    try:
        st = state_mod.load(p["state"])
        return {
            "topic": st.get("plan") or "",
            "recent_turns": st.get("recent_turns") or [],
        }
    except Exception:
        return {"topic": "", "recent_turns": []}


def start_session_state_server(burnless_root: Path, port: int = 7701) -> None:
    """Start a background HTTP server exposing GET /session/state for plugins."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/session/state":
                body = json.dumps(get_session_state(burnless_root)).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *_: object) -> None:  # silence default access log
            pass

    try:
        server = HTTPServer(("127.0.0.1", port), _Handler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        logger.debug("plugin_loader: session state server on port %d", port)
    except OSError:
        logger.debug("plugin_loader: could not start session state server on port %d", port)


# ── Transports ────────────────────────────────────────────────────────────────

def _call_http(plugin: Plugin, hook_name: str, payload: dict) -> dict | None:
    base = plugin.endpoint.rstrip("/")
    url = f"{base}/{hook_name}"
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if plugin.auth:
        req.add_header("Authorization", f"Bearer {plugin.auth}")
    with urllib.request.urlopen(req, timeout=_HOOK_TIMEOUT) as resp:  # noqa: S310
        return json.loads(resp.read().decode())


def _call_stdio(plugin: Plugin, hook_name: str, payload: dict) -> dict | None:
    proc = subprocess.run(
        [plugin.endpoint],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=_HOOK_TIMEOUT,
    )
    if proc.returncode != 0:
        logger.warning(
            "plugin_loader: stdio plugin %r exited %d for hook %s: %s",
            plugin.name, proc.returncode, hook_name, proc.stderr[:200],
        )
        return None
    return json.loads(proc.stdout)
