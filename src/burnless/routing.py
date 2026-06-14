from __future__ import annotations

import re

TIER_PRIORITY = ["gold", "silver", "bronze"]
BUILTIN_SILVER_HINTS = (
    "compression",
    "simulator",
    "repositorio",
    "repositório",
    "projeto",
    "pasta",
    "diretorio",
    "diretório",
    "memoria",
    "memória",
    "anotacoes",
    "anotações",
)
PATH_HINT_RE = re.compile(r"(^|\s)(~?/|/Users/|\./|\.\./)[^\s]+")


def route(text: str, routing_rules: dict[str, list[str]], default_tier: str = "bronze") -> tuple[str, str]:
    """Return (tier, matched_keyword). Default tier wins when nothing matches.

    First-match-wins by tier priority (gold > silver > bronze).
    Bronze-default is the user's stated preference: cheapest agent unless
    something forces an upgrade.
    """
    if not text:
        return default_tier, ""
    haystack = text.lower()
    for kw in routing_rules.get("gold", []):
        if kw.lower() in haystack:
            return "gold", kw
    if PATH_HINT_RE.search(text):
        return "silver", "path"
    for kw in BUILTIN_SILVER_HINTS:
        if kw in haystack:
            return "silver", kw
    for tier in ("silver", "bronze"):
        for kw in routing_rules.get(tier, []):
            if kw.lower() in haystack:
                return tier, kw
    return default_tier, ""


def explain_route(text: str, routing_rules: dict[str, list[str]]) -> dict:
    tier, kw = route(text, routing_rules)
    return {"tier": tier, "matched_keyword": kw or None}
