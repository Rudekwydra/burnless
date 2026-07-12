"""Live savings footer: token count + cost breakdown per turn.

Captures original vs compressed prompt, calculates tokens saved, renders footer,
logs metrics to ~/.burnless/turns.jsonl.
"""

from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass
import sys

try:
    import tiktoken
except ImportError:
    tiktoken = None

from .pricing import rate as P, MODEL_PRICES
from . import i18n as i18n_mod


def pricing_family_for_model(model_name: str) -> str:
    """Map model name to pricing family.

    Handles claude-*, gpt-*, hf.co/*, ollama, gemma, qwen, llama, etc.
    Returns one of: haiku, sonnet, opus, fable, gemma.
    Falls back to sonnet if family not in MODEL_PRICES.
    """
    if not model_name:
    	return "sonnet"

    m = model_name.lower()

    # Direct family names
    for family in ("haiku", "sonnet", "opus", "fable"):
    	if family in m:
    		return family if family in MODEL_PRICES else "sonnet"

    # Local/open-source inference: hf.co/, ollama, gemma, qwen, llama
    if any(prefix in m for prefix in ("hf.co/", "ollama", "gemma", "qwen", "llama")):
    	return "gemma"  # Local, $0 cost

    # gpt / codex fallback to sonnet pricing
    if "gpt" in m or "codex" in m:
    	return "gpt" if "gpt" in MODEL_PRICES else "sonnet"

    # Unknown: default to sonnet
    return "sonnet"


@dataclass
class TurnMetrics:
    """Per-turn metrics: tokens & USD for real vs counterfactual."""

    turn_num: int
    original_tokens: int
    compressed_tokens: int
    saved_tokens: int
    saved_pct: float
    real_usd: float
    burnless_usd: float
    saved_usd: float
    model: str = "opus"


def count_tokens(text: str, model: str = "cl100k_base") -> int:
    """Count tokens using tiktoken. Falls back to char-based estimate if unavailable."""
    if not tiktoken:
        return max(1, len(text) // 4)
    try:
        enc = tiktoken.get_encoding(model)
        return len(enc.encode(text))
    except Exception:
        return max(1, len(text) // 4)


def calculate_turn_metrics(
    human_prompt: str,
    compressed_prompt: str,
    model: str = "opus",
    turn_num: int = 1,
) -> TurnMetrics:
    """Calculate token counts + costs for a single turn."""

    original_tokens = count_tokens(human_prompt)
    compressed_tokens = count_tokens(compressed_prompt)
    saved_tokens = max(0, original_tokens - compressed_tokens)
    saved_pct = (saved_tokens / original_tokens * 100) if original_tokens > 0 else 0.0

    rate_in = P(model, "input")
    rate_out = P(model, "output")

    real_usd = original_tokens * rate_in
    burnless_usd = compressed_tokens * rate_in
    saved_usd = real_usd - burnless_usd

    return TurnMetrics(
        turn_num=turn_num,
        original_tokens=original_tokens,
        compressed_tokens=compressed_tokens,
        saved_tokens=saved_tokens,
        saved_pct=saved_pct,
        real_usd=real_usd,
        burnless_usd=burnless_usd,
        saved_usd=saved_usd,
        model=model,
    )


def metrics_from_savings(savings: dict, model: str, turn_num: int) -> "TurnMetrics":
    """Footer metrics from the capsule compression (raw worker output tokens kept
    in context without burnless, vs the compact capsule burnless keeps)."""
    original_tokens = int(savings.get("raw_tokens", 0) or 0)
    compressed_tokens = int(savings.get("capsule_tokens", 0) or 0)
    saved_tokens = max(0, original_tokens - compressed_tokens)
    saved_pct = (saved_tokens / original_tokens * 100) if original_tokens > 0 else 0.0

    rate_in = P(model, "input")

    real_usd = original_tokens * rate_in
    burnless_usd = compressed_tokens * rate_in
    saved_usd = real_usd - burnless_usd

    return TurnMetrics(
        turn_num=turn_num,
        original_tokens=original_tokens,
        compressed_tokens=compressed_tokens,
        saved_tokens=saved_tokens,
        saved_pct=saved_pct,
        real_usd=real_usd,
        burnless_usd=burnless_usd,
        saved_usd=saved_usd,
        model=model,
    )


def format_tokens(count: int) -> str:
    """Format token count: 1000+ → Xk, else literal."""
    if count >= 1000:
        return f"{count // 1000}k"
    else:
        return str(count)


def render_footer(metrics: TurnMetrics) -> str:
    """Render single-line footer: 'Real: 50k tokens ($2.50) | Burnless: 12k tokens ($0.60) | Saved: 38k (76%)'"""
    real_tokens_str = format_tokens(metrics.original_tokens)
    burnless_tokens_str = format_tokens(metrics.compressed_tokens)
    saved_tokens_str = format_tokens(metrics.saved_tokens)

    return (
        f"Real: {real_tokens_str} tokens (${metrics.real_usd:.3f}) | "
        f"Burnless: {burnless_tokens_str} tokens (${metrics.burnless_usd:.3f}) | "
        f"Saved: {saved_tokens_str} ({metrics.saved_pct:.0f}%)"
    )


def render_footer_v2(metrics: TurnMetrics, *, did: str, tier: str, worker_model: str, lang: str = "pt-BR") -> str:
    """Render honest footer: worker tokens → context tokens, input avoided, worker family & cost.

    Example: ⚡ d123 silver·haiku · worker 252k tok brutos → 254 no contexto (992×) · input evitado est. $0.76
    Local worker: · input evitado worker local $0
    """
    family = pricing_family_for_model(worker_model)
    rate_in = P(family, "input")

    orig_fmt = format_tokens(metrics.original_tokens)
    comp_fmt = format_tokens(metrics.compressed_tokens)

    # Ratio: original / compressed, rounded to integer
    if metrics.compressed_tokens > 0:
        ratio = metrics.original_tokens / metrics.compressed_tokens
        ratio_fmt = f"({ratio:.0f}×)"
    else:
        ratio_fmt = ""

    # Cost: original_tokens × input rate of worker's family
    avoided_cost = metrics.original_tokens * rate_in

    # Format cost: "est. $X.XX" for pricing families, "worker local $0" for gemma
    if family == "gemma":
        cost_part = i18n_mod.msg("footer_input_avoided_local", lang)
    else:
        cost_part = i18n_mod.msg("footer_input_avoided_est", lang, avoided_cost=avoided_cost)

    footer_template = i18n_mod.msg("footer_worker_tokens", lang, orig_fmt=orig_fmt, comp_fmt=comp_fmt, ratio_fmt=ratio_fmt, cost_part=cost_part)

    return (
        f"⚡ {did} {tier}·{family} · "
        f"{footer_template}"
    )


def render_praise(metrics: TurnMetrics, threshold: float, lang: str = "pt-BR") -> str:
    """Metric-gated praise line. Returns "" unless the compression ratio crosses threshold.

    Fires rarely by design: only when original/compressed >= threshold (e.g. 1000).
    The sentence always carries the measured number — praise anchored in fact.
    """
    if not threshold or threshold <= 0:
        return ""
    if metrics.compressed_tokens <= 0 or metrics.original_tokens <= 0:
        return ""
    ratio = metrics.original_tokens / metrics.compressed_tokens
    if ratio < threshold:
        return ""
    orig_fmt = format_tokens(metrics.original_tokens)
    comp_fmt = format_tokens(metrics.compressed_tokens)
    return i18n_mod.msg("praise_tokens_compressed", lang, ratio=ratio, orig_fmt=orig_fmt, comp_fmt=comp_fmt)


def log_turn_metrics(metrics: TurnMetrics, burnless_root: Path | None = None) -> None:
    """Append turn metrics to ~/.burnless/turns.jsonl."""

    if burnless_root is None:
        burnless_root = Path.home() / ".burnless"

    log_path = burnless_root / "turns.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "turn_num": metrics.turn_num,
        "original_tokens": metrics.original_tokens,
        "compressed_tokens": metrics.compressed_tokens,
        "saved_tokens": metrics.saved_tokens,
        "saved_pct": round(metrics.saved_pct, 2),
        "real_usd": round(metrics.real_usd, 4),
        "burnless_usd": round(metrics.burnless_usd, 4),
        "saved_usd": round(metrics.saved_usd, 4),
        "model": metrics.model,
    }

    try:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[savings-footer] failed to log metrics: {e}", file=sys.stderr)
