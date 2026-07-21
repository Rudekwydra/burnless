"""Codex (`codex exec` CLI/subscription) ask adapter — M1b.

Only the `codex exec` transport is implemented in this milestone. The OpenAI
API transport (agent.auth == "api") is declared but not wired — invoke_text
raises RuntimeError before any subprocess/network call so the gap is explicit
rather than silently falling back to another provider.

Also carries the Codex -> Burnless -> Codex recursion guard: a `codex exec`
child process that itself shells out to `burnless ask` inherits
BURNLESS_ASK_ACTIVE_PROVIDER=codex via env and gets refused before it can
spawn another codex process.
"""
from __future__ import annotations

import dataclasses
import os
import shlex
import shutil
import subprocess
import time

from ..coreconfig import resolver
from ..warm_session_codex import _parse_codex_usage
from .contracts import (
    AskRequest,
    BudgetPlan,
    ProviderCapabilities,
    ProviderResult,
    ResolvedAskTarget,
    UsageRecord,
)


class CodexAdapter:
    def resolve(self, request: AskRequest, cfg: dict) -> ResolvedAskTarget:
        agent = resolver.resolve_agent(request.tier, cfg)
        model = request.model if request.model else resolver.resolve_model(request.tier, cfg)
        cache_mode = resolver.resolve_cache_mode(agent, cfg)
        partial = ResolvedAskTarget(
            effective_tier=request.tier,
            requested_tier=request.tier,
            provider="codex",
            model=model,
            auth=agent.auth,
            effort=request.effort,
            cache_mode=cache_mode.name,
            adapter_key="codex",
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
        binary = shutil.which("codex") or "codex"
        cmd = [
            binary, "exec",
            "--skip-git-repo-check",
            "--sandbox", "read-only",
            "--json",
            "-m", model,
            "<prompt>",
        ]
        if request.effort:
            cmd += ["-c", f'model_reasoning_effort="{request.effort}"']
        redacted = shlex.join(cmd)
        return dataclasses.replace(partial, capabilities=caps, redacted_command=redacted)

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
        if os.environ.get("BURNLESS_ASK_ACTIVE_PROVIDER") == "codex":
            return ProviderResult(
                returncode=1,
                stderr=(
                    "burnless ask: recursion guard — a codex-driven process "
                    "already has an active burnless ask (Codex -> Burnless -> "
                    "Codex blocked)"
                ),
            )

        if target.auth == "api":
            raise RuntimeError(
                "burnless ask: codex api transport not wired yet — configure "
                "auth=subscription (codex exec) for this tier"
            )

        binary = shutil.which("codex")
        if binary is None:
            return ProviderResult(returncode=1, stderr="burnless ask: codex binary not found in PATH")

        cmd = [
            binary, "exec",
            "--skip-git-repo-check",
            "--sandbox", "read-only",
            "--json",
            "-m", target.model,
            request.prompt,
        ]
        if request.effort:
            cmd += ["-c", f'model_reasoning_effort="{request.effort}"']

        env = {**os.environ, "BURNLESS_ASK_ACTIVE_PROVIDER": "codex"}

        start = time.monotonic()
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=request.timeout_s,
                env=env,
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
        usage = _parse_codex_usage(result.stdout or "")
        if usage:
            return UsageRecord(
                input_tokens=usage.get("input_tokens", 0) or 0,
                output_tokens=usage.get("output_tokens", 0) or 0,
                cache_read_tokens=usage.get("cached_input_tokens", 0) or 0,
                basis="provider_reported",
            )
        return UsageRecord(basis="estimate")

    def capabilities(self, target: ResolvedAskTarget) -> ProviderCapabilities:
        return ProviderCapabilities(
            observable_token_usage=True,
            observable_cache_usage=False,
            hard_max_output=False,
            hard_spend_cap=False,
            supported_efforts=("low", "medium", "high"),
            prefix_cache=False,
            streaming=False,
            json_output=True,
            reliable_cancel=False,
        )

    def cancel(self) -> bool:
        return False
