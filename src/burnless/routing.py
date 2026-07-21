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
class RouteContext:
    """Optional structured task context — the third force in ``decide_route``.

    ``None`` (the default everywhere) means "no structured context supplied":
    ``routing.policies`` never matches, and ``decide_route`` behaves exactly as
    it did before this field existed. Callers that know the task's shape
    (``burnless route --task-kind ... --impact ...``, or an internal caller)
    pass this explicitly to let a declarative ``routing.policies`` rule impose
    a tier floor from context rather than keyword text.
    """

    task_kind: str = "implement"       # read | classify | create | implement | architect | audit
    impact: str = "internal"           # internal | public | client | production | irreversible
    tools_required: bool = True
    reversibility: str = "reversible"  # reversible | hard_to_reverse | irreversible
    uncertainty: float = 0.0
    explicit_tier: str | None = None
    project_policy_source: str = "default"


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
    # Set only when a ``routing.policies`` rule raised the effective floor
    # above the natural route. Kept as a separate field (not folded into
    # ``policy_source``) so existing callers that read ``policy_source``
    # expecting an escalation-policy source string (``env:BURNLESS_HARDCORE``,
    # ``config:routing.escalation_policy``, ``config:routing.hardcore_filter``,
    # ``default``) keep getting exactly that, unambiguously.
    policy_floor_id: str | None = None

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
            "policy_floor_id": self.policy_floor_id,
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


_POLICY_WHEN_KEYS = {"task_kind", "impact", "tools_required", "reversibility"}


def validate_routing_policies(policies: list | None) -> list[str]:
    """Validate a ``routing.policies`` list. Returns human-readable error
    strings (empty list = valid). Never raises on malformed input — a broken
    config always comes back as error strings, never an exception, so the
    caller decides what to do with it (``decide_route`` turns non-empty
    results into a ``ValueError`` at a single, predictable point).
    """
    if not policies:
        return []
    if not isinstance(policies, list):
        return ["routing.policies must be a list"]

    errors: list[str] = []
    seen_ids: dict[str, int] = {}
    for idx, p in enumerate(policies):
        if not isinstance(p, dict):
            errors.append(f"routing.policies[{idx}] must be a dict")
            continue

        pid = p.get("id")
        if not pid or not isinstance(pid, str):
            errors.append(f"routing.policies[{idx}] missing required non-empty 'id'")
        elif pid in seen_ids:
            errors.append(
                f"routing.policies duplicate id '{pid}' at indices {seen_ids[pid]} and {idx}"
            )
        else:
            seen_ids[pid] = idx

        when = p.get("when")
        if not isinstance(when, dict):
            errors.append(f"routing.policies[{idx}] (id={pid!r}) 'when' must be a dict")
        else:
            for key in when:
                if key not in _POLICY_WHEN_KEYS:
                    errors.append(
                        f"routing.policies[{idx}] (id={pid!r}) unknown 'when' key '{key}'"
                    )

        min_tier = p.get("min_tier")
        if min_tier not in TIER_RANK:
            errors.append(
                f"routing.policies[{idx}] (id={pid!r}) invalid min_tier {min_tier!r}; "
                f"must be one of {sorted(TIER_RANK)}"
            )
    return errors


def resolve_policy_floor(
    context: "RouteContext | None", routing_cfg: dict
) -> tuple[str | None, str | None, dict | None]:
    """Resolve the highest-ranked ``routing.policies`` rule matching ``context``.

    Returns ``(min_tier, policy_id, matched_when)`` — all ``None`` when
    ``context`` is ``None``, no policies are configured, or none match. A
    policy matches when every key in its ``when`` dict equals the
    corresponding ``RouteContext`` field exactly. Ties (equal ``min_tier``
    rank) keep the first match in list order. Assumes ``policies`` is already
    validated (see ``validate_routing_policies``) but does not crash on a
    merely non-matching entry.
    """
    if context is None:
        return None, None, None
    policies = (routing_cfg or {}).get("policies")
    if not policies:
        return None, None, None

    best_rank = -1
    best: tuple[str | None, str | None, dict | None] = (None, None, None)
    for p in policies:
        if not isinstance(p, dict):
            continue
        when = p.get("when")
        if not isinstance(when, dict):
            continue
        if any(getattr(context, key, None) != value for key, value in when.items()):
            continue
        min_tier = p.get("min_tier")
        rank = TIER_RANK.get(min_tier)
        if rank is None:
            continue
        if rank > best_rank:
            best_rank = rank
            best = (min_tier, p.get("id"), when)
    return best


def decide_route(
    text: str,
    requested_tier: str | None,
    routing_cfg: dict,
    env: Mapping | None = None,
    context: "RouteContext | None" = None,
) -> RouteDecision:
    """Combine the scored natural route, an optional ``routing.policies`` tier
    floor from ``context``, an optional requested tier, and the escalation
    policy into a single ``RouteDecision``.

    Precedence: policy floor (from ``context``) raises the effective floor
    above the natural route when a policy matches; the escalation policy
    (``BURNLESS_HARDCORE`` / ``block``/``confirm``) only ever gates a genuine
    user-requested upgrade ABOVE both the natural route and the policy floor —
    it can never block an escalation a policy floor already requires. With
    ``context=None`` (the default), the floor is always ``0`` and this
    collapses exactly to the pre-policy-floor behavior.
    """
    if (routing_cfg or {}).get("policies"):
        errors = validate_routing_policies(routing_cfg["policies"])
        if errors:
            raise ValueError("invalid routing.policies: " + "; ".join(errors))

    natural, signals, confidence = score_route(text, routing_cfg)
    policy, policy_source = resolve_escalation_policy(routing_cfg, env)
    policy_floor_tier, policy_id, _matched_when = resolve_policy_floor(context, routing_cfg)

    natural_rank = TIER_RANK[natural]
    floor_rank = TIER_RANK.get(policy_floor_tier, 0) if policy_floor_tier else 0
    effective_floor_rank = max(natural_rank, floor_rank)
    effective_floor_tier = (
        policy_floor_tier
        if floor_rank == effective_floor_rank and floor_rank > natural_rank
        else natural
    )
    floor_raised = effective_floor_tier != natural
    floor_id = policy_id if floor_raised else None

    matched_kw = next((s.value for s in signals if s.kind == "keyword"), "")
    requested = requested_tier or None

    if not requested:
        effective_tier = effective_floor_tier
        reason = (
            f"policy floor '{policy_id}' requires this tier"
            if floor_raised
            else "no tier override; natural route used"
        )
        return RouteDecision(
            natural, None, effective_tier, "allowed", confidence, signals,
            policy_source, reason, matched_kw, policy_floor_id=floor_id,
        )

    req_rank = TIER_RANK.get(requested, 0)

    if req_rank <= effective_floor_rank:
        # user's ask is fully covered by (or below) natural route + policy
        # floor -- always allowed, never hits the escalation-policy gate.
        #
        # A downgrade below the natural route alone is honored as requested
        # (legacy behavior: the user is always free to ask for less than the
        # natural route). Only an actual policy floor is an absolute minimum
        # that clamps a too-low request back up -- never the natural route by
        # itself, which is a suggestion, not a floor.
        if req_rank < floor_rank:
            action = "downgraded"
            effective_tier = policy_floor_tier
        elif req_rank < natural_rank:
            action = "downgraded"
            effective_tier = requested
        else:
            action = "allowed"
            effective_tier = requested
        reason = (
            f"policy floor '{policy_id}' already requires this tier"
            if floor_raised
            else "requested tier at or below natural route"
        )
        return RouteDecision(
            natural, requested, effective_tier, action, confidence, signals,
            policy_source, reason, matched_kw, policy_floor_id=floor_id,
        )

    # req_rank > effective_floor_rank: a genuine user-initiated escalation
    # beyond both the natural route AND any policy floor -- the only case the
    # escalation policy (BURNLESS_HARDCORE / block/confirm/off) gets to gate.
    if policy == "block":
        return RouteDecision(
            natural, requested, effective_floor_tier, "blocked", confidence, signals,
            policy_source, "requested tier above natural route without --force",
            matched_kw or "default", policy_floor_id=floor_id,
        )
    if policy == "confirm":
        return RouteDecision(
            natural, requested, requested, "confirmed", confidence, signals,
            policy_source, "upgrade requires confirmation", matched_kw or "default",
            policy_floor_id=floor_id,
        )
    reason = "escalation policy off; upgrade allowed" if policy == "off" else "upgrade allowed; escalation explained"
    return RouteDecision(
        natural, requested, requested, "allowed", confidence, signals,
        policy_source, reason, matched_kw or "default", policy_floor_id=floor_id,
    )


def format_route_explain(decision: "RouteDecision", agent_name: str = "", agent_command: str = "") -> str:
    """Human-readable full route decision for ``burnless route --explain``.

    Prints natural/requested/effective tier, confidence, scored signals, the
    policy source, the action, and an executable next command.
    """
    lines = ["route decision:"]
    lines.append(f"   natural tier:   {decision.natural_tier}")
    lines.append(f"   requested tier: {decision.requested_tier or '(none)'}")
    lines.append(f"   effective tier: {decision.effective_tier}")
    if agent_name:
        suffix = f"  ({agent_command})" if agent_command else ""
        lines.append(f"   agent:          {agent_name}{suffix}")
    lines.append(f"   confidence:     {decision.confidence}")
    if decision.policy_floor_id:
        lines.append(
            f"   policy floor:   {decision.effective_tier} "
            f"(id={decision.policy_floor_id}, matched: {decision.reason})"
        )
    if decision.signals:
        sig = ", ".join(f"{s.kind}:{s.value}={s.weight}" for s in decision.signals)
    else:
        sig = "(none)"
    lines.append(f"   signals:        {sig}")
    lines.append(f"   policy source:  {decision.policy_source}")
    lines.append(f"   action:         {decision.action}")
    if decision.action == "blocked":
        nxt = f'burnless do --tier {decision.requested_tier} --force "<spec>"'
    elif decision.requested_tier:
        nxt = f'burnless do --tier {decision.effective_tier} "<spec>"'
    else:
        nxt = 'burnless do "<spec>"'
    lines.append(f"   next command:   {nxt}")
    return "\n".join(lines)
