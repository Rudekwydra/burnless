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


def format_escalation_block(lang: str, requested: str, natural: str, signal: str, policy_source: str) -> str:
    """User-facing message when the tier escalation policy blocks a tier upgrade.

    The internal config key stays ``routing.hardcore_filter`` (and the
    ``BURNLESS_HARDCORE`` env) for one release; the user-facing concept is the
    'tier escalation policy'. The block always names the full decision and an
    executable next command, never a bare refusal.
    """
    if (lang or "").startswith("pt"):
        return (
            "\n\U0001f6a6 burnless: politica de escalonamento de tier bloqueou este upgrade.\n"
            f"   tier pedido:   {requested}\n"
            f"   rota natural:  {natural} (sinal: {signal})\n"
            f"   politica:      {policy_source}\n"
            "   motivo:        tier pedido acima da rota natural sem --force\n"
            "   pra prosseguir:\n"
            f"     burnless do --tier {requested} --force \"<spec>\"\n"
            "     (ou desligue: unset BURNLESS_HARDCORE  /  routing.hardcore_filter: false)\n"
        )
    return (
        "\n\U0001f6a6 burnless: tier escalation policy blocked this upgrade.\n"
        f"   requested tier: {requested}\n"
        f"   natural route:  {natural} (signal: {signal})\n"
        f"   policy:         {policy_source}\n"
        "   reason:         requested tier above natural route without --force\n"
        "   to proceed:\n"
        f"     burnless do --tier {requested} --force \"<spec>\"\n"
        "     (or disable: unset BURNLESS_HARDCORE  /  routing.hardcore_filter: false)\n"
    )
