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


def build_menu_view(cfg: dict, default_cfg: dict, providers: dict, session_overrides: dict | None = None) -> str:
    """Full non-interactive menu text: table + provider status + change hints."""
    table = render_models_table(cfg, default_cfg, session_overrides)
    prov = "  ".join(f"{name} {'OK' if ok else 'x'}" for name, ok in providers.items())
    hints = (
        "change for one run : burnless do --<tier> provider:model   (e.g. --silver ollama:gemma4-e4b)\n"
        "persist as default : burnless models set <tier> provider:model --default"
    )
    return f"burnless . models\n\n{table}\n\nproviders: {prov}\n\n{hints}"


def worker_menu_options(providers: dict) -> list:
    """Pickable worker options with availability, given detected providers."""
    opts = []
    for model in ("opus", "sonnet", "haiku"):
        opts.append({"provider": "anthropic", "model": model, "spec": f"anthropic:{model}",
                     "available": bool(providers.get("anthropic")), "custom": False})
    opts.append({"provider": "codex", "model": "gpt-5.2", "spec": "codex:gpt-5.2",
                 "available": bool(providers.get("codex")), "custom": False})
    opts.append({"provider": "gemini", "model": "gemini-2.5-pro", "spec": "gemini:gemini-2.5-pro",
                 "available": bool(providers.get("gemini")), "custom": False})
    opts.append({"provider": "ollama", "model": "(type a model)", "spec": "ollama:",
                 "available": bool(providers.get("ollama")), "custom": True})
    return opts


def run_interactive(cfg: dict, default_cfg: dict, providers: dict, *,
                    input_fn=input, output_fn=print, persist_fn=None) -> dict | None:
    """Numbered picker: choose tier -> choose worker -> this-run vs make-default.
    I/O is injected for testability. persist_fn(tier, spec) is called on make-default.
    Returns a dict describing the action, or None if cancelled."""
    output_fn(render_models_table(cfg, default_cfg))
    tiers = ["diamond", "gold", "silver", "bronze"]
    output_fn("\nPick a tier to change:")
    for i, t in enumerate(tiers, 1):
        output_fn(f"  {i}) {t}")
    raw = (input_fn("tier [1-4, q]: ") or "").strip().lower()
    if raw in ("q", ""):
        return None
    try:
        tier = tiers[int(raw) - 1]
    except (ValueError, IndexError):
        output_fn("invalid choice"); return None
    opts = worker_menu_options(providers)
    output_fn(f"\nPick a worker for {tier}:")
    for i, o in enumerate(opts, 1):
        flag = "" if o["available"] else "  (not installed)"
        output_fn(f"  {i}) {o['provider']}:{o['model']}{flag}")
    raw = (input_fn(f"worker [1-{len(opts)}, q]: ") or "").strip().lower()
    if raw in ("q", ""):
        return None
    try:
        chosen = opts[int(raw) - 1]
    except (ValueError, IndexError):
        output_fn("invalid choice"); return None
    spec = chosen["spec"]
    if chosen["custom"]:
        model = (input_fn("ollama model name: ") or "").strip()
        if not model:
            return None
        spec = "ollama:" + model
    scope = (input_fn("apply: [1] this run  [2] make default  [q]: ") or "").strip().lower()
    if scope == "2":
        if persist_fn:
            persist_fn(tier, spec)
        output_fn(f"default updated: {tier} = {spec}")
        return {"action": "default", "tier": tier, "spec": spec}
    if scope == "1":
        output_fn(f"for one run: burnless do --{tier} {spec} \"<task>\"")
        return {"action": "oneshot", "tier": tier, "spec": spec}
    return None
