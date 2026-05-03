from __future__ import annotations
from pathlib import Path
from . import metrics as metrics_mod
from . import state as state_mod


def fmt_int(n: int) -> str:
    return f"{n:,}"


def render_status(state: dict, m: dict) -> str:
    last = state.get("last_status") or "—"
    nxt = state.get("next") or "—"
    project = state.get("project") or "Project"
    bt = fmt_int(int(m.get("burnless_tokens", 0)))
    avoided = m.get("token_burn_avoided_percent", 0)
    lines = [
        "Burnless active",
        "",
        f"Project: {project}",
        f"Last:    {last}",
        f"Next:    {nxt}",
        "",
        f"{bt} burnless tokens",
        f"Token Burn avoided: {avoided}%",
    ]
    return "\n".join(lines)


def render_metrics(m: dict, *, show_cost: bool = True) -> str:
    bt = fmt_int(int(m.get("burnless_tokens", 0)))
    lines = [
        f"{bt} burnless tokens",
        f"Repeated briefings avoided: {m.get('repeated_briefings_avoided', 0)}",
        f"Dead logs isolated:         {m.get('dead_logs_isolated', 0)}",
        f"Expensive calls avoided:    {m.get('expensive_model_calls_avoided', 0)}",
    ]
    if show_cost:
        cost = m.get("estimated_cost_avoided_usd", 0)
        lines.append(f"Estimated cost avoided:     ${cost:,.2f}")
    return "\n".join(lines)


def render_audit(entries: list[dict]) -> str:
    if not entries:
        return "(no audit entries yet — run a delegation to populate)"
    out = []
    for e in entries:
        ts = e.get("ts", "")[:19].replace("T", " ")
        amt = fmt_int(int(e.get("amount", 0)))
        src = e.get("source", "?")
        did = e.get("delegation_id") or "-"
        reason = e.get("reason", "")
        out.append(f"{ts}  +{amt:>10}  {src:<24}  {did:<6}  {reason}")
    return "\n".join(out)


def render_footer(m: dict) -> str:
    bt = fmt_int(int(m.get("burnless_tokens", 0)))
    return f"\n{bt} burnless tokens"
