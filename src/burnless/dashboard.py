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
    lines = [
        "Burnless active",
        "",
        f"Project: {project}",
        f"Last:    {last}",
        f"Next:    {nxt}",
        "",
        f"{bt} burnless tokens",
    ]
    return "\n".join(lines)


def render_metrics(m: dict, *, show_cost: bool = True) -> str:
    bt = fmt_int(int(m.get("burnless_tokens", 0)))
    by = m.get("by_source", {}) or {}
    lines = [
        f"{bt} burnless tokens (sum of all sources)",
        "",
        "By source:",
        f"  capsule_compression          {fmt_int(int(by.get('capsule_compression', 0))):>12}   (encoder: raw msg → capsule)",
        f"  output_decompression_avoided {fmt_int(int(by.get('output_decompression_avoided', 0))):>12}   (decoder: Maestro capsule → expanded prose, floor)",
        f"  repeated_context_avoided     {fmt_int(int(by.get('repeated_context_avoided', 0))):>12}   (Maestro: cache-read tokens not paid at full input price)",
        f"  expensive_model_avoided      {fmt_int(int(by.get('expensive_model_avoided', 0))):>12}   (tier routing: cheap worker handled task gold-tier would have)",
        f"  raw_logs_isolated            {fmt_int(int(by.get('raw_logs_isolated', 0))):>12}   (worker stdout never replayed into Maestro)",
        f"  compact_state                {fmt_int(int(by.get('compact_state', 0))):>12}   (state representation overhead saved)",
        f"  keepalive_cache_renewed      {fmt_int(int(by.get('keepalive_cache_renewed', 0))):>12}   (cache-read confirmed by keepalive ping)",
        "",
        "Counters:",
        f"  encoder_calls   {m.get('encoder_calls', 0)}   (Maestro only)",
        f"  decoder_calls   {m.get('decoder_calls', 0)}   (Maestro only)",
        f"  maestro_calls   {m.get('brain_calls', 0)}   (Maestro only)",
        f"  maestro cache_read_tokens     {fmt_int(int(m.get('brain_cache_read_tokens', 0)))}",
        f"  maestro cache_creation_tokens {fmt_int(int(m.get('brain_cache_creation_tokens', 0)))}",
        f"  maestro input_tokens (uncached) {fmt_int(int(m.get('brain_input_tokens', 0)))}",
        f"  maestro output_tokens         {fmt_int(int(m.get('brain_output_tokens', 0)))}",
        "",
        "Legacy delegate/run path:",
        f"  legacy_run_calls        {fmt_int(int(m.get('legacy_run_calls', 0)))}",
        f"  legacy_compress_calls   {fmt_int(int(m.get('legacy_compress_calls', 0)))}",
        f"  legacy_decompress_calls {fmt_int(int(m.get('legacy_decompress_calls', 0)))}",
        "",
        "Legacy aggregates:",
        f"  Repeated briefings avoided: {m.get('repeated_briefings_avoided', 0)}",
        f"  Dead logs isolated:         {m.get('dead_logs_isolated', 0)}",
        f"  Expensive calls avoided:    {m.get('expensive_model_calls_avoided', 0)}",
    ]
    ratio_count = int(m.get("compression_ratio_observed_count", 0))
    if ratio_count > 0:
        ratio_sum = float(m.get("compression_ratio_observed_sum", 0.0))
        avg = ratio_sum / ratio_count
        lines.append("")
        lines.append("Observed compression (local codec, audited):")
        lines.append(f"  avg ratio              {avg:.2f}×")
        lines.append(f"  samples                {ratio_count}")
    # Free savings breakdown: make the product mechanics visible.
    try:
        from . import savings_formula
        s = savings_formula.compute_free(m)
        if s.total > 0:
            lines.append("")
            lines.append("Free savings breakdown:")
            lines.append(f"  input compression      {s.input_compression:,.0f}   (txt → compact prompt)")
            lines.append(f"  Maestro history/cache  {s.maestro_history:,.0f}   (linear capsules + warm prefix)")
            lines.append(f"  worker one-shot        {s.worker_oneshot:,.0f}   (worker logs not replayed)")
            lines.append(f"  tier routing           {s.tier_routing:,.0f}   (cheap worker vs gold)")
            if s.other:
                lines.append(f"  other                  {s.other:,.0f}")
            lines.append(f"  total                  {s.total:,.0f}")
    except Exception:
        pass
    if show_cost:
        cost = m.get("estimated_cost_avoided_usd", 0)
        lines.append(f"  Estimated cost avoided:     ${cost:,.4f} (rough — uses single $15/MTok rate)")
    lines.extend([
        "",
        "Note: numbers are conservative floors. Free saves by compressing input,",
        "keeping Maestro history linear, running workers one-shot without prior",
        "chat context, and keeping the stable prefix warm when the provider reports cache hits.",
    ])
    return "\n".join(lines)


def render_session_diff(diff: dict | None) -> str:
    if not diff:
        return "(no session snapshots yet — call `burnless metrics --snapshot start` and `--snapshot end`)"
    lines = [
        f"Session: {diff.get('from_label')} → {diff.get('to_label')}",
        f"  {diff.get('from_ts')} → {diff.get('to_ts')}",
        "",
        f"Δ burnless_tokens:        {fmt_int(diff.get('delta_burnless_tokens', 0)):>12}",
        f"Δ encoder_calls:          {diff.get('delta_encoder_calls', 0):>12}",
        f"Δ decoder_calls:          {diff.get('delta_decoder_calls', 0):>12}",
        f"Δ maestro_calls:          {diff.get('delta_brain_calls', 0):>12}",
        f"Δ maestro cache_read:     {fmt_int(diff.get('delta_brain_cache_read', 0)):>12}",
        f"Δ maestro cache_creation: {fmt_int(diff.get('delta_brain_cache_creation', 0)):>12}",
        f"Δ legacy_run_calls:       {diff.get('delta_legacy_run_calls', 0):>12}",
        "",
        "By source delta:",
    ]
    by_delta = diff.get("delta_by_source", {}) or {}
    for k in sorted(by_delta.keys()):
        v = by_delta[k]
        if v:
            lines.append(f"  {k:<32} {fmt_int(v):>12}")
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
        basis = e.get("basis") or "?"
        reason = e.get("reason", "")
        out.append(f"{ts}  +{amt:>10}  {src:<24}  {basis:<10}  {did:<6}  {reason}")
    return "\n".join(out)


def render_footer(m: dict) -> str:
    bt = fmt_int(int(m.get("burnless_tokens", 0)))
    return f"\n{bt} burnless tokens"


def render_economy(r, *, show_cost: bool = True) -> str:
    """Render EconomyReport as human-readable string.

    Per-bucket line: name <tokens>tok $<usd> (note)
    Followed by TOTAL line and assumptions.
    """
    lines = []
    for b in r.buckets:
        tok_str = fmt_int(int(b.tokens))
        usd_str = f"{b.usd:.2f}"
        note_part = f"  ({b.note})" if b.note else ""
        lines.append(f"{b.name:<40} {tok_str:>12}tok  ${usd_str:>10}{note_part}")
    lines.append("")
    tot_tok_str = fmt_int(int(r.total_tokens))
    tot_usd_str = f"{r.total_usd:.2f}"
    lines.append(f"{'TOTAL':<40} {tot_tok_str:>12}tok  ${tot_usd_str:>10}")
    lines.append("")
    lines.append("assumptions: " + "; ".join(r.assumptions))
    return "\n".join(lines)


render = render_metrics
