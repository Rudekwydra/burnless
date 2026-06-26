from burnless import routing


def test_absolute_path_routes_to_silver_without_config_keyword():
    tier, matched = routing.route(
        "olha o projeto /Users/roberto/antigravity/app_paty",
        {"gold": [], "silver": [], "bronze": []},
    )

    assert tier == "silver"
    assert matched == "path"


def test_project_memory_review_routes_to_silver():
    tier, matched = routing.route(
        "veja se tudo foi feito conforme anotacoes da memoria",
        {"gold": [], "silver": [], "bronze": []},
    )

    assert tier == "silver"
    assert matched == "memoria"


def test_repository_lookup_routes_to_silver():
    tier, matched = routing.route(
        "encontra o repositorio no disco",
        {"gold": [], "silver": [], "bronze": []},
    )

    assert tier == "silver"
    assert matched == "repositorio"


def test_explicit_gold_keyword_wins_over_path_hint():
    tier, matched = routing.route(
        "faça uma revisão de arquitetura em /Users/roberto/app",
        {"gold": ["arquitetura"], "silver": [], "bronze": []},
    )

    assert tier == "gold"
    assert matched == "arquitetura"


def test_format_escalation_block_en():
    from burnless.routing import format_escalation_block
    msg = format_escalation_block("en", "gold", "silver", "architecture", "config:routing.hardcore_filter")
    assert "tier escalation policy" in msg
    assert "\U0001f6a8" not in msg  # old alarm emoji gone
    assert "gold" in msg and "silver" in msg
    assert "--force" in msg
    assert "config:routing.hardcore_filter" in msg


def test_format_escalation_block_pt():
    from burnless.routing import format_escalation_block
    msg = format_escalation_block("pt-BR", "gold", "bronze", "default", "env:BURNLESS_HARDCORE")
    assert "escalonamento de tier" in msg
    assert "\U0001f6a8" not in msg
    assert "--force" in msg
    assert "env:BURNLESS_HARDCORE" in msg


def test_resolve_escalation_policy_precedence():
    from burnless.routing import resolve_escalation_policy
    assert resolve_escalation_policy({}, env={}) == ("off", "default")
    assert resolve_escalation_policy({"hardcore_filter": True}, env={}) == ("block", "config:routing.hardcore_filter")
    assert resolve_escalation_policy({"escalation_policy": "explain"}, env={}) == ("explain", "config:routing.escalation_policy")
    # explicit escalation_policy wins over legacy hardcore_filter
    assert resolve_escalation_policy({"escalation_policy": "off", "hardcore_filter": True}, env={}) == ("off", "config:routing.escalation_policy")
    # env wins over everything
    assert resolve_escalation_policy({"escalation_policy": "off"}, env={"BURNLESS_HARDCORE": "1"}) == ("block", "env:BURNLESS_HARDCORE")


def test_score_route_signals_and_confidence():
    from burnless.routing import score_route
    rules = {"gold": ["architecture"], "silver": [], "bronze": []}
    natural, signals, conf = score_route(
        "architecture review with security risk for /Users/roberto/x.py", rules
    )
    assert natural == "gold"
    kinds = {s.kind for s in signals}
    assert "keyword" in kinds and "risk" in kinds and "files" in kinds
    assert 0.0 < conf <= 1.0


def test_decide_route_no_override_allowed():
    from burnless.routing import decide_route
    d = decide_route("just summarize this", None, {"bronze": ["summarize"]})
    assert d.action == "allowed"
    assert d.requested_tier is None
    assert d.effective_tier == d.natural_tier


def test_decide_route_block_policy_blocks_upgrade():
    from burnless.routing import decide_route
    rules = {"bronze": ["summarize"], "hardcore_filter": True}
    d = decide_route("summarize this log", "gold", rules, env={})
    assert d.natural_tier == "bronze"
    assert d.action == "blocked"
    assert d.effective_tier == "bronze"   # falls back to natural route
    assert d.policy_source == "config:routing.hardcore_filter"


def test_decide_route_off_policy_allows_upgrade():
    from burnless.routing import decide_route
    d = decide_route("summarize this log", "gold", {"bronze": ["summarize"]}, env={})
    assert d.action == "allowed"
    assert d.effective_tier == "gold"


def test_decide_route_downgrade_allowed():
    from burnless.routing import decide_route
    rules = {"gold": ["architecture"], "hardcore_filter": True}
    d = decide_route("architecture decision", "bronze", rules)
    assert d.natural_tier == "gold"
    assert d.action == "downgraded"
    assert d.effective_tier == "bronze"


def test_decide_route_diamond_always_gated():
    from burnless.routing import decide_route, TIER_RANK
    assert TIER_RANK["diamond"] > TIER_RANK["gold"]
    rules = {"gold": ["architecture"], "hardcore_filter": True}
    d = decide_route("architecture decision", "diamond", rules)
    # diamond outranks the gold natural route -> upgrade -> blocked under policy
    assert d.action == "blocked"
    assert d.requested_tier == "diamond"


def test_route_decision_to_event_shape():
    from burnless.routing import decide_route
    rules = {"bronze": ["summarize"], "hardcore_filter": True}
    ev = decide_route("summarize this log", "gold", rules, env={}).to_event("d123")
    assert ev["type"] == "route_decision"
    assert ev["delegation_id"] == "d123"
    assert ev["natural_tier"] == "bronze"
    assert ev["requested_tier"] == "gold"
    assert ev["effective_tier"] == "bronze"
    assert ev["action"] == "blocked"
    assert isinstance(ev["signals"], list)
    assert ev["policy_source"] == "config:routing.hardcore_filter"


def test_format_route_explain_blocked_next_command():
    from burnless.routing import decide_route, format_route_explain
    rules = {"bronze": ["summarize"], "hardcore_filter": True}
    d = decide_route("summarize this log", "gold", rules, env={})
    out = format_route_explain(d, "haiku", "claude")
    assert "natural tier:   bronze" in out
    assert "requested tier: gold" in out
    assert "effective tier: bronze" in out
    assert "action:         blocked" in out
    assert "policy source:  config:routing.hardcore_filter" in out
    assert 'burnless do --tier gold --force "<spec>"' in out
    assert "agent:          haiku" in out


def test_format_route_explain_no_override():
    from burnless.routing import decide_route, format_route_explain
    d = decide_route("just summarize", None, {"bronze": ["summarize"]}, env={})
    out = format_route_explain(d)
    assert "requested tier: (none)" in out
    assert 'burnless do "<spec>"' in out
    assert "confidence:" in out
