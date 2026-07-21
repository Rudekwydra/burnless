"""Ollama/llamacpp local ask adapter — M1b.

Wraps `pure_ask.run_ask_ollama` (the existing local HTTP transport) behind the
`AskAdapter` protocol. No cost accounting is possible locally — usage is
always reported with basis="estimate".
"""
from __future__ import annotations

import dataclasses
import hashlib

from .. import pure_ask
from ..coreconfig import resolver
from .contracts import (
    AskRequest,
    BudgetPlan,
    ProviderCapabilities,
    ProviderResult,
    ResolvedAskTarget,
    UsageRecord,
)


class OllamaAdapter:
    def resolve(
        self, request: AskRequest, cfg: dict, *, prefix_content: str | None = None
    ) -> ResolvedAskTarget:
        agent = resolver.resolve_agent(request.tier, cfg)
        model = request.model if request.model else resolver.resolve_model(request.tier, cfg)
        cache_mode = resolver.resolve_cache_mode(agent, cfg)
        prefix_hash = None
        if prefix_content:
            prefix_hash = "sha256:" + hashlib.sha256(prefix_content.encode("utf-8")).hexdigest()
        partial = ResolvedAskTarget(
            effective_tier=request.tier,
            requested_tier=request.tier,
            provider="ollama",
            model=model,
            auth=agent.auth,
            effort=request.effort,
            cache_mode=cache_mode.name,
            adapter_key="ollama",
            budget=BudgetPlan(
                max_input_tokens=request.max_input_tokens,
                max_output_tokens=request.max_output_tokens,
                max_total_tokens=request.max_total_tokens,
                max_budget_usd=request.max_budget_usd,
                policy=request.budget_policy,
            ),
            capabilities=ProviderCapabilities(),
            prefix_hash=prefix_hash,
        )
        caps = self.capabilities(partial)
        budget = pure_ask.compute_budget_plan(request, model, caps)
        redacted = f"http POST local model={model} stream=false"
        return dataclasses.replace(partial, capabilities=caps, budget=budget, redacted_command=redacted)

    def explain(self, target: ResolvedAskTarget) -> dict:
        result = {
            "provider": target.provider,
            "model": target.model,
            "effective_tier": target.effective_tier,
            "adapter_key": target.adapter_key,
            "cache_mode": target.cache_mode,
            "capabilities": dataclasses.asdict(target.capabilities),
        }
        if target.prefix_hash is not None:
            result["prefix_cache_status"] = "supported" if target.capabilities.prefix_cache else "unsupported"
        return result

    def invoke_text(
        self, request: AskRequest, target: ResolvedAskTarget, *, prefix_content: str | None = None
    ) -> ProviderResult:
        effective_system = request.system or pure_ask.DEFAULT_ASK_SYSTEM
        if prefix_content:
            effective_system = f"{effective_system}\n\n{prefix_content}"
        rc, stdout, stderr = pure_ask.run_ask_ollama(
            target.model,
            request.prompt,
            system=effective_system,
            timeout=request.timeout_s,
        )
        return ProviderResult(returncode=rc, stdout=stdout, stderr=stderr)

    def parse_usage(self, result: ProviderResult, target: ResolvedAskTarget) -> UsageRecord:
        return UsageRecord(basis="estimate")

    def capabilities(self, target: ResolvedAskTarget) -> ProviderCapabilities:
        return ProviderCapabilities(
            observable_token_usage=False,
            observable_cache_usage=False,
            hard_max_output=False,
            hard_spend_cap=False,
            supported_efforts=(),
            prefix_cache=False,
            streaming=False,
            json_output=True,
            reliable_cancel=False,
        )

    def cancel(self) -> bool:
        return False
