import argparse

from burnless import cli
from burnless import config as config_mod
from burnless.routing import (
    RouteContext,
    decide_route,
    format_route_explain,
    resolve_policy_floor,
    score_route,
    validate_routing_policies,
)


class TestValidateRoutingPolicies:
    def test_valid_list_returns_no_errors(self):
        policies = [
            {"id": "a", "when": {"task_kind": "classify"}, "min_tier": "gold"},
            {"id": "b", "when": {"impact": "public"}, "min_tier": "silver"},
        ]
        assert validate_routing_policies(policies) == []

    def test_duplicate_id_reports_one_error_mentioning_both(self):
        policies = [
            {"id": "dup", "when": {"task_kind": "classify"}, "min_tier": "gold"},
            {"id": "dup", "when": {"impact": "public"}, "min_tier": "silver"},
        ]
        errors = validate_routing_policies(policies)
        assert len(errors) == 1
        assert "dup" in errors[0]
        assert "0" in errors[0] and "1" in errors[0]

    def test_unknown_when_key_reports_error_naming_key(self):
        policies = [
            {"id": "a", "when": {"bogus_field": "x"}, "min_tier": "gold"},
        ]
        errors = validate_routing_policies(policies)
        assert len(errors) == 1
        assert "bogus_field" in errors[0]

    def test_bad_min_tier_reports_error(self):
        policies = [
            {"id": "a", "when": {"task_kind": "classify"}, "min_tier": "platinum"},
        ]
        errors = validate_routing_policies(policies)
        assert len(errors) == 1
        assert "platinum" in errors[0]

    def test_policies_not_a_list_reports_error_no_crash(self):
        errors = validate_routing_policies({"id": "a"})
        assert len(errors) == 1

    def test_empty_or_missing_returns_no_errors(self):
        assert validate_routing_policies([]) == []
        assert validate_routing_policies(None) == []


class TestResolvePolicyFloor:
    def test_context_none_returns_all_none(self):
        cfg = {"policies": [{"id": "a", "when": {"task_kind": "classify"}, "min_tier": "gold"}]}
        assert resolve_policy_floor(None, cfg) == (None, None, None)

    def test_no_policies_in_cfg_returns_all_none(self):
        ctx = RouteContext(task_kind="classify")
        assert resolve_policy_floor(ctx, {}) == (None, None, None)

    def test_single_matching_policy_returns_its_tier_and_id(self):
        ctx = RouteContext(task_kind="classify", impact="public")
        cfg = {
            "policies": [
                {
                    "id": "public_editorial_gate",
                    "when": {"task_kind": "classify", "impact": "public"},
                    "min_tier": "gold",
                }
            ]
        }
        min_tier, policy_id, matched_when = resolve_policy_floor(ctx, cfg)
        assert min_tier == "gold"
        assert policy_id == "public_editorial_gate"
        assert matched_when == {"task_kind": "classify", "impact": "public"}

    def test_two_matching_policies_higher_rank_wins(self):
        ctx = RouteContext(task_kind="classify", impact="public")
        cfg = {
            "policies": [
                {"id": "low", "when": {"task_kind": "classify"}, "min_tier": "silver"},
                {"id": "high", "when": {"impact": "public"}, "min_tier": "gold"},
            ]
        }
        min_tier, policy_id, _ = resolve_policy_floor(ctx, cfg)
        assert min_tier == "gold"
        assert policy_id == "high"

    def test_non_matching_when_returns_all_none(self):
        ctx = RouteContext(task_kind="classify", impact="internal")
        cfg = {
            "policies": [
                {
                    "id": "public_editorial_gate",
                    "when": {"task_kind": "classify", "impact": "public"},
                    "min_tier": "gold",
                }
            ]
        }
        assert resolve_policy_floor(ctx, cfg) == (None, None, None)


class TestDecideRoutePolicyFloor:
    def test_classify_internal_no_context_routes_bronze(self):
        d = decide_route("classificar tags internas", None, {})
        assert d.effective_tier == "bronze"

    def test_classify_public_with_policy_floor_wins_over_hardcore(self):
        cfg = {
            "policies": [
                {
                    "id": "public_editorial_gate",
                    "when": {"task_kind": "classify", "impact": "public"},
                    "min_tier": "gold",
                }
            ]
        }
        ctx = RouteContext(task_kind="classify", impact="public")

        d = decide_route("classificar copy para publicação", None, cfg, context=ctx)
        assert d.effective_tier == "gold"

        d_hardcore = decide_route(
            "classificar copy para publicação", None, cfg, env={"BURNLESS_HARDCORE": "1"}, context=ctx
        )
        assert d_hardcore.effective_tier == "gold"
        assert d_hardcore.action != "blocked"

    def test_path_plus_implementa_routes_silver_no_regression(self):
        d = decide_route("/Users/roberto/antigravity/burnless implementa isso", None, {})
        assert d.effective_tier == "silver"

    def test_policy_floor_wins_over_lower_requested_tier(self):
        cfg = {
            "policies": [
                {
                    "id": "public_editorial_gate",
                    "when": {"task_kind": "classify", "impact": "public"},
                    "min_tier": "gold",
                }
            ]
        }
        ctx = RouteContext(task_kind="classify", impact="public")
        d = decide_route(
            "classificar copy para publicação",
            "bronze",
            cfg,
            env={"BURNLESS_HARDCORE": "1"},
            context=ctx,
        )
        assert d.effective_tier == "gold"
        assert d.action == "downgraded"

    def test_diamond_never_appears_as_natural_tier(self):
        for text in [
            "classificar copy para publicação",
            "faz um deploy em produção",
            "so summarize this",
            "",
        ]:
            natural, _signals, _confidence = score_route(text, {})
            assert natural != "diamond"
            d = decide_route(text, None, {})
            assert d.natural_tier != "diamond"

    def test_format_route_explain_shows_policy_floor(self):
        cfg = {
            "policies": [
                {
                    "id": "public_editorial_gate",
                    "when": {"task_kind": "classify", "impact": "public"},
                    "min_tier": "gold",
                }
            ]
        }
        ctx = RouteContext(task_kind="classify", impact="public")
        d = decide_route("classificar copy para publicação", None, cfg, context=ctx)
        out = format_route_explain(d)
        assert "policy floor:" in out
        assert "public_editorial_gate" in out


def _init_project(tmp_path, policies=None):
    burnless = tmp_path / ".burnless"
    for d in ("delegations", "logs", "temp", "capsules", "archive", "chat", "runs"):
        (burnless / d).mkdir(parents=True, exist_ok=True)
    config_path = burnless / "config.yaml"
    config_mod.write_default(config_path)
    if policies is not None:
        cfg = config_mod.load(config_path)
        cfg["routing"]["policies"] = policies
        config_mod.save(config_path, cfg)
    return burnless


def _route_args(**overrides):
    defaults = dict(
        text="hi",
        explain=True,
        tier=None,
        task_kind=None,
        impact=None,
        tools_required=None,
        reversibility=None,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


_PUBLIC_EDITORIAL_GATE = [
    {
        "id": "public_editorial_gate",
        "when": {"task_kind": "classify", "impact": "public"},
        "min_tier": "gold",
    }
]


class TestCmdRouteContext:
    def test_context_flags_trigger_policy_floor(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        _init_project(tmp_path, policies=_PUBLIC_EDITORIAL_GATE)

        rc = cli.cmd_route(_route_args(
            text="classificar copy para publicação",
            task_kind="classify",
            impact="public",
        ))

        assert rc == 0
        out = capsys.readouterr().out
        assert "policy floor:" in out
        assert "gold" in out

    def test_architect_no_tools_prints_suggestion(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        _init_project(tmp_path)

        rc = cli.cmd_route(_route_args(
            text="desenha a arquitetura de X",
            task_kind="architect",
            tools_required=False,
        ))

        assert rc == 0
        out = capsys.readouterr().out
        assert "suggestion:" in out
        assert "burnless ask" in out

    def test_plain_invocation_unchanged_no_new_lines(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        _init_project(tmp_path)

        rc = cli.cmd_route(_route_args(text="hi"))

        assert rc == 0
        out = capsys.readouterr().out
        assert "policy floor:" not in out
        assert "suggestion:" not in out

    def test_invalid_routing_policies_returns_clean_error(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        _init_project(tmp_path, policies=[
            {"id": "bad", "when": {"bogus_field": "x"}, "min_tier": "gold"},
        ])

        rc = cli.cmd_route(_route_args(text="hi", task_kind="classify"))

        assert rc != 0
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.strip()
