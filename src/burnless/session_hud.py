from __future__ import annotations

import os


def _basename(path) -> str:
    if not path:
        return "-"
    s = str(path).rstrip("/")
    base = os.path.basename(s)
    return base or s or "-"


def render_hud(state: dict, *, style: str = "compact") -> str:
    """Render the session HUD as ASCII text.

    style: "off" -> "", "compact" -> one line, "verbose" -> multi-line block.
    Tolerates missing keys.
    """
    state = state or {}
    if style == "off":
        return ""

    project = _basename(state.get("project"))
    mode = state.get("mode") or "unknown"
    last_status = state.get("last_status")
    last_status = last_status if last_status else "-"

    savings = state.get("savings")
    saved_tokens = None
    if isinstance(savings, dict):
        saved_tokens = savings.get("saved_tokens")

    if style == "verbose":
        scope_hash = state.get("scope_hash") or "-"
        turns = state.get("turns")
        turns = turns if turns is not None else "-"
        saved = saved_tokens if saved_tokens is not None else "-"
        lines = [
            f"project: {project}",
            f"mode: {mode}",
            f"last_status: {last_status}",
            f"saved_tokens: {saved}",
            f"scope_hash: {scope_hash}",
            f"turns: {turns}",
        ]
        return "\n".join(lines)

    # compact (default)
    parts = [
        f"project={project}",
        f"mode={mode}",
        f"last={last_status}",
    ]
    if saved_tokens is not None:
        parts.append(f"saved={saved_tokens}")
    return "burnless | " + " | ".join(parts)


def render_explain(sections: dict) -> str:
    """Render the explain block. One labeled line per section, fixed order."""
    sections = sections or {}
    order = [
        ("active_mode", "Active mode"),
        ("last_hook_injection", "Last hook injection"),
        ("last_compaction_decision", "Last compaction decision"),
        ("last_route_decision", "Last route decision"),
        ("last_retrieval", "Last retrieval"),
        ("last_delegation_status", "Last delegation status"),
        ("last_warm_status", "Warm session"),
    ]
    lines = []
    for key, label in order:
        value = sections.get(key)
        if value is None:
            lines.append(f"{label}: (none recorded)")
        else:
            lines.append(f"{label}: {value}")
    return "\n".join(lines)
