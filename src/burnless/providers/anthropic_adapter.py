"""Anthropic (claude CLI) ask adapter — M1b.

Wraps the existing `pure_ask.build_ask_command` / `subprocess.run` transport
behind the `AskAdapter` protocol. Provider/model/auth/cache-mode resolution is
delegated to `coreconfig.resolver`; this adapter does not re-derive any of it.
"""
from __future__ import annotations

import dataclasses
import json
import subprocess
import tempfile
import time

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


class AnthropicAdapter:
    def resolve(self, request: AskRequest, cfg: dict) -> ResolvedAskTarget:
        agent = resolver.resolve_agent(request.tier, cfg)
        model = request.model if request.model else resolver.resolve_model(request.tier, cfg)
        cache_mode = resolver.resolve_cache_mode(agent, cfg)
        partial = ResolvedAskTarget(
            effective_tier=request.tier,
            requested_tier=request.tier,
            provider="anthropic",
            model=model,
            auth=agent.auth,
            effort=request.effort,
            cache_mode=cache_mode.name,
            adapter_key="anthropic",
            budget=BudgetPlan(
                max_input_tokens=request.max_input_tokens,
                max_output_tokens=request.max_output_tokens,
                max_total_tokens=request.max_total_tokens,
                max_budget_usd=request.max_budget_usd,
                policy=request.budget_policy,
            ),
            capabilities=ProviderCapabilities(),
        )
        caps = self.capabilities(partial)
        return dataclasses.replace(partial, capabilities=caps)

    def explain(self, target: ResolvedAskTarget) -> dict:
        return {
            "provider": target.provider,
            "model": target.model,
            "effective_tier": target.effective_tier,
            "adapter_key": target.adapter_key,
            "cache_mode": target.cache_mode,
            "capabilities": dataclasses.asdict(target.capabilities),
        }

    def invoke_text(self, request: AskRequest, target: ResolvedAskTarget) -> ProviderResult:
        cmd = pure_ask.build_ask_command(
            target.model,
            output_format=request.output_format,
            system=request.system,
            max_budget_usd=request.max_budget_usd,
            effort=request.effort,
        )
        start = time.monotonic()
        try:
            result = subprocess.run(
                cmd,
                input=request.prompt,
                capture_output=True,
                text=True,
                timeout=request.timeout_s,
                cwd=tempfile.gettempdir(),
            )
        except subprocess.TimeoutExpired as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            return ProviderResult(returncode=1, timed_out=True, stderr=str(exc), duration_ms=duration_ms)
        duration_ms = int((time.monotonic() - start) * 1000)
        return ProviderResult(
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_ms=duration_ms,
        )

    def parse_usage(self, result: ProviderResult, target: ResolvedAskTarget) -> UsageRecord:
        if result.stdout:
            try:
                data = json.loads(result.stdout)
            except (json.JSONDecodeError, TypeError):
                data = None
            if isinstance(data, dict):
                usage = data.get("usage")
                if isinstance(usage, dict):
                    return UsageRecord(
                        input_tokens=usage.get("input_tokens", 0) or 0,
                        output_tokens=usage.get("output_tokens", 0) or 0,
                        cache_read_tokens=usage.get("cache_read_input_tokens", 0) or 0,
                        cache_write_tokens=usage.get("cache_creation_input_tokens", 0) or 0,
                        basis="provider_reported",
                    )
        return UsageRecord(basis="estimate")

    def capabilities(self, target: ResolvedAskTarget) -> ProviderCapabilities:
        return ProviderCapabilities(
            observable_token_usage=True,
            observable_cache_usage=True,
            hard_max_output=False,
            hard_spend_cap=True,
            supported_efforts=("low", "medium", "high", "xhigh", "max"),
            prefix_cache=True,
            streaming=True,
            json_output=True,
            reliable_cancel=False,
        )

    def cancel(self) -> bool:
        return False
