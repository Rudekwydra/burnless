from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace
from pathlib import Path
from typing import Iterable, Optional, Protocol
import os
import shutil
import subprocess
import json


@dataclass(frozen=True)
class HostInstallation:
    name: str
    command: str
    path: str | None
    version: str | None
    available: bool


@dataclass(frozen=True)
class HostCapabilities:
    can_clear: bool = False
    can_resume: bool = False
    supports_hooks: bool = False
    supports_usage: bool = False
    transcript_access: bool = False
    rollout_access: bool = False
    trust: str = "unknown"
    reset_strategy: str = "respawn"


@dataclass(frozen=True)
class HostSession:
    host: str
    host_session_id: str | None = None
    process_instance_id: str | None = None
    cwd: str | None = None
    transcript_ref: str | None = None


@dataclass(frozen=True)
class ContextUsage:
    current: int | None
    limit: int | None
    confidence: str = "unknown"


@dataclass(frozen=True)
class PilotEvent:
    host: str
    host_session_id: str | None
    process_instance_id: str | None
    event: str
    source: str | None = None
    cwd: str | None = None
    transcript_ref: str | None = None
    user_text: str | None = None
    assistant_text: str | None = None
    usage: dict | None = None
    ts: str | None = None


class HostAdapter(Protocol):
    name: str

    def detect(self) -> HostInstallation: ...
    def capabilities(self) -> HostCapabilities: ...
    def build_interactive_argv(self, root: Path, model: str | None = None, extra_args: Iterable[str] = ()) -> list[str]: ...
    def build_fresh_argv(self, root: Path, model: str | None = None, extra_args: Iterable[str] = ()) -> list[str]: ...
    def normalize_hook_event(self, payload: dict) -> PilotEvent: ...
    def locate_session(self, run_id: str) -> HostSession: ...
    def context_usage(self, session: HostSession) -> ContextUsage: ...
    def is_turn_idle(self, session: HostSession) -> bool: ...


def _version_for(command: str) -> str | None:
    path = shutil.which(command)
    if not path:
        return None
    try:
        proc = subprocess.run([path, "--version"], capture_output=True, text=True, check=False)
        out = (proc.stdout or proc.stderr or "").strip().splitlines()
        return out[0] if out else None
    except Exception:
        return None


def discover_hosts() -> list[HostInstallation]:
    from .hosts.claude import ClaudeAdapter
    from .hosts.codex import CodexAdapter

    adapters = [ClaudeAdapter(), CodexAdapter()]
    return [adapter.detect() for adapter in adapters]


def build_report(host: str | None = None, *, root: Path | None = None, env_host: str | None = None, run_id: str | None = None) -> dict:
    adapter = resolve_host_adapter(host, root=root, env_host=env_host)
    installation = adapter.detect()
    capabilities = adapter.capabilities()
    session = adapter.locate_session(run_id or "pilot")
    if root is not None and getattr(session, "cwd", None) is None:
        session = replace(session, cwd=str(root))
    usage = adapter.context_usage(session)
    run_state = None
    if root is not None and run_id:
        try:
            from .events import summarize_run_events

            run_state = summarize_run_events(root, run_id)
        except Exception:
            run_state = None
    return {
        "host": installation.name,
        "installation": installation,
        "capabilities": capabilities,
        "session": session,
        "usage": usage,
        "run_state": run_state,
    }


def _config_host(root: Path | None) -> str | None:
    if root is None:
        return None
    try:
        from .. import config as config_mod

        cfg = config_mod.load((root / ".burnless" / "config.yaml") if root.name != ".burnless" else (root / "config.yaml"))
        host_cfg = cfg.get("pilot", {}) if isinstance(cfg, dict) else {}
        host = host_cfg.get("host")
        return str(host) if host else None
    except Exception:
        return None


def resolve_host_adapter(host: str | None = None, *, root: Path | None = None, env_host: str | None = None):
    from .hosts.claude import ClaudeAdapter
    from .hosts.codex import CodexAdapter

    mapping = {
        "claude": ClaudeAdapter(),
        "codex": CodexAdapter(),
    }
    choice = host or env_host or _config_host(root) or "auto"
    if choice != "auto":
        return mapping[choice]

    installs = [(adapter.detect().available, adapter) for adapter in mapping.values()]
    for available, adapter in installs:
        if available:
            return adapter
    return ClaudeAdapter()
