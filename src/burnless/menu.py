"""Harness-agnostic config-menu core: render the tier->worker table and detect
available providers. No TUI loop here — burnless menu (CLI) and harness hooks
call these pure functions so the menu works under any maestro (Claude/codex/gemini)."""
from __future__ import annotations
import shutil
import urllib.request


def provider_of(agent: dict) -> str:
    """Provider name for an agent dict: explicit 'provider' field wins, else infer
    from the command string (codex/gemini/claude). Defaults to 'anthropic'."""
    p = agent.get("provider")
    if p:
        return p
    cmd = (agent.get("command") or "").lower()
    if "codex" in cmd:
        return "codex"
    if "gemini" in cmd:
        return "gemini"
    if "ollama" in cmd:
        return "ollama-local"
    return "anthropic"


def _ollama_up(host: str = "http://localhost:11434") -> bool:
    try:
        with urllib.request.urlopen(host.rstrip("/") + "/api/tags", timeout=1) as r:
            return r.status == 200
    except Exception:
        return False


def detect_providers() -> dict:
    """Which provider backends are usable on this machine right now."""
    return {
        "anthropic": shutil.which("claude") is not None,
        "codex": shutil.which("codex") is not None,
        "gemini": shutil.which("gemini") is not None,
        "ollama": _ollama_up(),
    }


def source_marker(tier: str, cfg: dict, default_cfg: dict, session_overrides: dict | None = None) -> str:
    """Where this tier's current worker comes from: (session) > (default) > (global)."""
    if session_overrides and tier in session_overrides:
        return "(session)"
    cur = (cfg.get("agents", {}).get(tier) or {}).get("name")
    dflt = (default_cfg.get("agents", {}).get(tier) or {}).get("name")
    if cur is not None and cur == dflt:
        return "(default)"
    return "(global)"


def render_models_table(cfg: dict, default_cfg: dict, session_overrides: dict | None = None) -> str:
    """Multi-line table: tier | provider | model | source-marker, for diamond/gold/silver/bronze."""
    lines = [f"{'tier':<9}{'provider':<16}{'model':<28}source"]
    agents = cfg.get("agents", {})
    for tier in ("diamond", "gold", "silver", "bronze"):
        a = agents.get(tier)
        if not a:
            continue
        prov = provider_of(a)
        model = a.get("name", "?")
        marker = source_marker(tier, cfg, default_cfg, session_overrides)
        lines.append(f"{tier:<9}{prov:<16}{model:<28}{marker}")
    return "\n".join(lines)
