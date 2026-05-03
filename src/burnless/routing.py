from __future__ import annotations

TIER_PRIORITY = ["diamond", "gold", "silver", "bronze"]
BUILTIN_DIAMOND_HINTS = ("compression", "simulator")


def route(text: str, routing_rules: dict[str, list[str]], default_tier: str = "bronze") -> tuple[str, str]:
    """Return (tier, matched_keyword). Default tier wins when nothing matches.

    First-match-wins by tier priority (diamond > gold > silver > bronze).
    Bronze-default is the user's stated preference: cheapest agent unless
    something forces an upgrade.
    """
    if not text:
        return default_tier, ""
    haystack = text.lower()
    for kw in BUILTIN_DIAMOND_HINTS:
        if kw in haystack:
            return "diamond", kw
    for tier in TIER_PRIORITY:
        for kw in routing_rules.get(tier, []):
            if kw.lower() in haystack:
                return tier, kw
    return default_tier, ""


def explain_route(text: str, routing_rules: dict[str, list[str]]) -> dict:
    tier, kw = route(text, routing_rules)
    return {"tier": tier, "matched_keyword": kw or None}


# Compression dial → tier modulation.
# Heavily compressed context tolerates a smaller model; preserved context
# benefits from a larger one. Diamond is exempt (code execution must keep its
# own sandbox).
_DEMOTE_ONE = {"gold": "silver", "silver": "bronze", "bronze": "bronze", "diamond": "diamond"}
_PROMOTE_ONE = {"bronze": "silver", "silver": "silver", "gold": "gold", "diamond": "diamond"}


def modulate_by_compression(tier: str, matched_kw: str, compression_mode: str) -> tuple[str, str]:
    """Adjust tier by compression dial. Returns (final_tier, reason).

    `aggressive` → demote one step (skipping diamond).
    `safe`       → promote bronze→silver only when no explicit keyword matched.
    `balanced`   → unchanged.
    """
    if tier == "diamond":
        return tier, ""
    mode = (compression_mode or "balanced").lower()
    if mode == "aggressive":
        new_tier = _DEMOTE_ONE.get(tier, tier)
        if new_tier != tier:
            return new_tier, f"compression=aggressive demoted {tier}→{new_tier}"
    elif mode == "safe":
        # Only promote when match was a default fallback (no keyword hit).
        # Avoids inflating cost when the user clearly wrote a bronze task.
        if not matched_kw and tier == "bronze":
            return "silver", "compression=safe promoted bronze→silver (default match)"
    return tier, ""
