"""M1a contract test — importable, no network, no subprocess.

Proves the 7 ask control-plane dataclasses and the AskAdapter protocol import,
instantiate with their minimal required fields, and that a dummy adapter
structurally satisfies the 6 fixed method names.
"""
from __future__ import annotations

import dataclasses

from burnless.providers.contracts import (
    AskAdapter,
    AskRequest,
    AskResult,
    BudgetPlan,
    ProviderCapabilities,
    ProviderResult,
    ResolvedAskTarget,
    UsageRecord,
)


def test_seven_dataclasses_are_frozen():
    for cls in (
        AskRequest,
        ResolvedAskTarget,
        ProviderCapabilities,
        BudgetPlan,
        ProviderResult,
        AskResult,
        UsageRecord,
    ):
        assert dataclasses.is_dataclass(cls)
        assert cls.__dataclass_params__.frozen is True


def test_minimal_instantiation():
    # AskRequest requires only the prompt.
    req = AskRequest(prompt="review architecture")
    assert req.prompt == "review architecture"
    assert req.output_format == "text"
    assert req.envelope_format == "text"
    assert req.dry_run is False

    # ResolvedAskTarget requires tier/provider/model; nests defaults.
    target = ResolvedAskTarget(
        effective_tier="gold",
        provider="anthropic",
        model="claude-opus-4-8",
    )
    assert target.cache_mode == "none"
    assert isinstance(target.capabilities, ProviderCapabilities)
    assert isinstance(target.budget, BudgetPlan)
    assert target.route_source == "default"

    # All-default value objects.
    caps = ProviderCapabilities()
    assert caps.observable_token_usage is False
    assert caps.supported_efforts == ()

    plan = BudgetPlan()
    assert plan.enforcement == "soft_only"

    # ProviderResult requires only a returncode.
    raw = ProviderResult(returncode=0)
    assert raw.stdout == ""
    assert raw.timed_out is False

    usage = UsageRecord()
    assert usage.basis == "estimate"
    assert usage.cost_basis == "pricing_table"

    result = AskResult()
    assert result.schema == "burnless.ask/v1"
    assert isinstance(result.usage, UsageRecord)
    assert result.warnings == ()


def test_frozen_is_immutable():
    req = AskRequest(prompt="x")
    try:
        req.prompt = "y"  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        pass
    else:
        raise AssertionError("AskRequest should be frozen/immutable")


class _DummyAdapter:
    """Structural implementation of the AskAdapter protocol."""

    def resolve(self, request: AskRequest, cfg: dict) -> ResolvedAskTarget:
        return ResolvedAskTarget(
            effective_tier=request.tier or "gold",
            provider="anthropic",
            model=request.model or "claude-opus-4-8",
        )

    def explain(self, target: ResolvedAskTarget) -> dict:
        return {"provider": target.provider, "model": target.model}

    def invoke_text(self, request: AskRequest, target: ResolvedAskTarget) -> ProviderResult:
        return ProviderResult(returncode=0, stdout="")

    def parse_usage(self, result: ProviderResult, target: ResolvedAskTarget) -> UsageRecord:
        return UsageRecord()

    def capabilities(self, target: ResolvedAskTarget) -> ProviderCapabilities:
        return ProviderCapabilities()

    def cancel(self) -> bool:
        return True


def test_adapter_has_six_fixed_methods():
    adapter = _DummyAdapter()
    for name in ("resolve", "explain", "invoke_text", "parse_usage", "capabilities", "cancel"):
        assert callable(getattr(adapter, name)), name

    # runtime_checkable Protocol: the dummy satisfies AskAdapter structurally.
    assert isinstance(adapter, AskAdapter)


def test_adapter_roundtrip_is_pure():
    adapter = _DummyAdapter()
    req = AskRequest(prompt="hi", tier="gold")
    target = adapter.resolve(req, {})
    assert isinstance(target, ResolvedAskTarget)
    raw = adapter.invoke_text(req, target)
    assert isinstance(raw, ProviderResult)
    assert isinstance(adapter.parse_usage(raw, target), UsageRecord)
    assert isinstance(adapter.capabilities(target), ProviderCapabilities)
    assert isinstance(adapter.explain(target), dict)
    assert adapter.cancel() is True
