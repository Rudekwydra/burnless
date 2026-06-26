from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field
from typing import Mapping

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


# --- Tier ranks ---------------------------------------------------------------
# diamond is the explicit top lane: it is never a natural route (``route`` never
# returns it), so any ``--tier diamond`` request is always an upgrade and always
# passes through the escalation policy. Ranked above gold for that reason.
TIER_RANK = {"bronze": 1, "silver": 2, "gold": 3, "diamond": 4}

# Words that raise the blast radius / importance of a task. Used as scored
# signals, not as routing keywords (they do not by themselves pick a tier).
RISK_SIGNALS = (
    "security", "seguranca", "seguran\u00e7a", "public api", "api publica",
    "api p\u00fablica", "release", "deploy", "irreversible", "irrevers\u00edvel",
    "destructive", "destrutivo", "migration", "migra\u00e7\u00e3o", "secret",
    "credential", "credencial", "prod", "production", "produ\u00e7\u00e3o",
)

_ESCALATION_POLICIES = ("off", "explain", "block", "confirm")


@dataclass
class Signal:
    """One scored routing signal contributing to the route confidence."""

    kind: str   # keyword | path | files | risk
    value: str
    weight: float


@dataclass
class RouteDecision:
    """Outcome of routing a spec under the tier escalation policy.

    Serializes to a ``route_decision`` event-ledger entry via ``to_event``.
    """

    natural_tier: str
    requested_tier: str | None
    effective_tier: str
    action: str          # allowed | blocked | confirmed | downgraded
    confidence: float
    signals: list = field(default_factory=list)
    policy_source: str = "default"
    reason: str = ""
    matched_keyword: str = ""

    def to_event(self, delegation_id: str | None = None) -> dict:
        return {
            "type": "route_decision",
            "delegation_id": delegation_id,
            "natural_tier": self.natural_tier,
            "requested_tier": self.requested_tier,
            "effective_tier": self.effective_tier,
            "action": self.action,
            "confidence": self.confidence,
            "signals": [asdict(s) for s in self.signals],
            "policy_source": self.policy_source,
            "reason": self.reason,
        }


def resolve_escalation_policy(
    routing_cfg: dict, env: Mapping | None = None
) -> tuple[str, str]:
    """Resolve the active tier escalation policy and its source.

    Precedence: ``BURNLESS_HARDCORE`` env (-> block) > explicit
    ``routing.escalation_policy`` > legacy ``routing.hardcore_filter`` > off.
    Returns ``(policy, policy_source)`` where policy is one of off|explain|
    block|confirm. The legacy key stays honored for one release.
    """
    env = os.environ if env is None else env
    if env.get("BURNLESS_HARDCORE") in ("1", "true", "yes"):
        return "block", "env:BURNLESS_HARDCORE"
    pol = (routing_cfg or {}).get("escalation_policy")
    if pol in _ESCALATION_POLICIES:
        return pol, "config:routing.escalation_policy"
    if (routing_cfg or {}).get("hardcore_filter"):
        return "block", "config:routing.hardcore_filter"
    return "off", "default"


def score_route(
    text: str, routing_rules: dict
) -> tuple[str, list, float]:
    """Score a spec into (natural_tier, signals, confidence).

    Reuses the first-match ``route`` for the natural tier, then layers
    importance signals (path references, files touched, risk words) to produce a
    0..1 confidence. Keyword-only routing stays the backbone; the score adds the
    headroom-style importance estimate without any proxy.
    """
    natural, kw = route(text, routing_rules)
    haystack = (text or "").lower()
    signals: list = []
    if kw and kw not in ("path", ""):
        weight = {"gold": 0.5, "silver": 0.3, "bronze": 0.2}.get(natural, 0.2)
        signals.append(Signal("keyword", kw, weight))
    if PATH_HINT_RE.search(text or ""):
        signals.append(Signal("path", "path-reference", 0.2))
    n_paths = len(re.findall(r"(?:~?/|/Users/|\./|\.\./)\S+", text or ""))
    if n_paths >= 1:
        signals.append(Signal("files", f"{n_paths} path(s)", round(min(0.05 * n_paths, 0.3), 3)))
    risk_hits = sorted({w for w in RISK_SIGNALS if w in haystack})
    if risk_hits:
        signals.append(Signal("risk", ",".join(risk_hits[:4]), round(min(0.15 * len(risk_hits), 0.4), 3)))
    confidence = round(min(sum(s.weight for s in signals), 1.0), 3)
    return natural, signals, confidence


def decide_route(
    text: str,
    requested_tier: str | None,
    routing_cfg: dict,
    env: Mapping | None = None,
) -> RouteDecision:
    """Combine the scored natural route, an optional requested tier, and the
    escalation policy into a single ``RouteDecision``.

    No override -> allowed at the natural tier. A requested tier at or below the
    natural rank -> allowed/downgraded. A requested upgrade is gated by the
    policy: off -> allowed, explain -> allowed, confirm -> confirmed, block ->
    blocked (effective tier falls back to the natural route).
    """
    natural, signals, confidence = score_route(text, routing_cfg)
    policy, policy_source = resolve_escalation_policy(routing_cfg, env)
    matched_kw = next((s.value for s in signals if s.kind == "keyword"), "")
    requested = requested_tier or None

    if not requested:
        return RouteDecision(
            natural, None, natural, "allowed", confidence, signals,
            policy_source, "no tier override; natural route used", matched_kw,
        )

    req_rank = TIER_RANK.get(requested, 0)
    nat_rank = TIER_RANK.get(natural, 0)
    if req_rank <= nat_rank:
        action = "downgraded" if req_rank < nat_rank else "allowed"
        return RouteDecision(
            natural, requested, requested, action, confidence, signals,
            policy_source, "requested tier at or below natural route", matched_kw,
        )

    # requested upgrade above the natural route
    if policy == "block":
        return RouteDecision(
            natural, requested, natural, "blocked", confidence, signals,
            policy_source, "requested tier above natural route without --force",
            matched_kw or "default",
        )
    if policy == "confirm":
        return RouteDecision(
            natural, requested, requested, "confirmed", confidence, signals,
            policy_source, "upgrade requires confirmation", matched_kw or "default",
        )
    reason = "escalation policy off; upgrade allowed" if policy == "off" else "upgrade allowed; escalation explained"
    return RouteDecision(
        natural, requested, requested, "allowed", confidence, signals,
        policy_source, reason, matched_kw or "default",
    )
