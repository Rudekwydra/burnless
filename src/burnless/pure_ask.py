from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
import urllib.request

from .providers.contracts import AskResult, UsageRecord


DEFAULT_ASK_SYSTEM = (
    "You are a plain text-completion function. Answer only in the requested "
    "format. Do not use tools, do not take actions, do not write files."
)

_DISALLOWED_TOOLS = [
    "Bash", "Edit", "Write", "Read", "Glob", "Grep", "Task",
    "WebFetch", "WebSearch", "NotebookEdit", "TodoWrite",
]


def resolve_ask_provider(tier: str, cfg: dict) -> str:
    """Resolve tier -> provider name.

    Returns the provider for a tier, defaulting to 'anthropic'.
    """
    return ((cfg.get("agents") or {}).get(tier) or {}).get("provider", "anthropic")


def resolve_ask_model(tier: str, cfg: dict) -> str:
    """Resolve tier -> model name for a pure `ask` call.

    Raises ValueError if the tier's provider is ollama/ollama-local (not
    supported by the claude-CLI pure-call path in this version).
    """
    agent_cfg = (cfg.get("agents") or {}).get(tier) or {}
    provider = agent_cfg.get("provider", "anthropic")
    if provider in ("ollama", "ollama-local"):
        raise ValueError(
            f"burnless ask: tier '{tier}' is provider={provider!r} — "
            "pure ask does not support local ollama yet, pick a tier mapped "
            "to the claude CLI (anthropic provider)"
        )
    model = agent_cfg.get("model")
    if model:
        return str(model)
    command = agent_cfg.get("command", "")
    parts = shlex.split(command) if command else []
    for i, tok in enumerate(parts):
        if tok == "--model" and i + 1 < len(parts):
            return parts[i + 1]
    raise ValueError(f"burnless ask: could not resolve a model for tier '{tier}'")


def run_ask_ollama(model: str, prompt: str, system: str | None = None, timeout: int = 120, host: str | None = None) -> tuple[int, str, str]:
    """Run a pure completion via local ollama/llamacpp HTTP API.

    Returns (returncode, stdout, stderr).
    - returncode 0 on success, 1 on any exception
    - stdout is the completion content or empty string on error
    - stderr is the exception message or empty string on success
    """
    try:
        api_mode = os.environ.get("BURNLESS_LOCAL_API", "ollama").lower()
        messages = [
            {"role": "system", "content": system or DEFAULT_ASK_SYSTEM},
            {"role": "user", "content": prompt},
        ]

        if api_mode == "llamacpp":
            local_host = os.environ.get("BURNLESS_LOCAL_HOST", "http://localhost:11435")
            endpoint = "/v1/chat/completions"
            payload = {
                "model": model or "local",
                "messages": messages,
                "stream": False,
                "temperature": 0.2,
            }
        else:
            local_host = host or os.environ.get("BURNLESS_OLLAMA_HOST", "http://localhost:11434")
            endpoint = "/api/chat"
            payload = {
                "model": model,
                "messages": messages,
                "stream": False,
                "think": False,
                "keep_alive": os.environ.get("BURNLESS_OLLAMA_KEEPALIVE", "30m"),
                "options": {
                    "temperature": 0.2,
                    "num_ctx": int(os.environ.get("BURNLESS_OLLAMA_NUM_CTX", "32768")),
                },
            }

        url = local_host.rstrip("/") + endpoint
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))

        if api_mode == "llamacpp":
            content = resp_data.get("choices", [{}])[0].get("message", {}).get("content", "")
        else:
            content = resp_data.get("message", {}).get("content", "")

        return 0, content.strip(), ""
    except Exception as e:
        return 1, "", str(e)


def build_ask_command(
    model: str,
    output_format: str = "text",
    system: str | None = None,
    max_budget_usd: float | None = None,
    effort: str | None = None,
) -> list[str]:
    """Build the pure-completion `claude -p` command — no tools, no CLAUDE.md.

    NEVER add --permission-mode/--allowedTools here — this must stay the
    inverse of the agentic worker command (see DOCTRINE.md "Spec Authoring").
    """
    cmd = [
        "claude", "-p",
        "--model", model,
        "--output-format", output_format,
        "--system-prompt", system or DEFAULT_ASK_SYSTEM,
        "--disallowedTools", *_DISALLOWED_TOOLS,
        "--exclude-dynamic-system-prompt-sections",
    ]
    if max_budget_usd is not None:
        cmd += ["--max-budget-usd", str(max_budget_usd)]
    if effort is not None:
        cmd += ["--effort", effort]
    return cmd


def _run_ask_codex(
    tier: str,
    prompt: str,
    cfg: dict,
    system: str | None,
    output_format: str,
    timeout: int,
    model: str | None,
    max_budget_usd: float | None,
    effort: str | None,
) -> tuple[int, str, str]:
    """Build a minimal AskRequest/ResolvedAskTarget and invoke CodexAdapter.

    Deferred import: pure_ask.providers.{anthropic,ollama}_adapter import this
    module back (`from .. import pure_ask`) to reuse build_ask_command /
    run_ask_ollama, so importing the providers package at module load time
    here would cycle. By the time run_ask() actually calls this helper, this
    module has already finished importing — the lazy import just resolves the
    already-loaded module from sys.modules.
    """
    from .providers.codex_adapter import CodexAdapter
    from .providers.contracts import AskRequest

    request = AskRequest(
        prompt=prompt,
        tier=tier,
        provider="codex",
        model=model,
        system=system,
        effort=effort,
        output_format=output_format,
        timeout_s=timeout,
        max_budget_usd=max_budget_usd,
    )
    adapter = CodexAdapter()
    target = adapter.resolve(request, cfg)
    result = adapter.invoke_text(request, target)
    return result.returncode, result.stdout, result.stderr


def run_ask(
    tier: str,
    prompt: str,
    cfg: dict,
    system: str | None = None,
    output_format: str = "text",
    timeout: int = 120,
    model: str | None = None,
    max_budget_usd: float | None = None,
    effort: str | None = None,
    provider: str | None = None,
) -> tuple[int, str, str]:
    """Run a pure completion call. Returns (returncode, stdout, stderr).

    Runs with cwd=a neutral temp dir (not the project root) so even if a
    future claude CLI version changes what --exclude-dynamic-system-prompt-sections
    covers, there is no CLAUDE.md file present to discover.

    When model is provided (not None and not empty), uses it directly without
    calling resolve_ask_model. Otherwise, resolves model from tier/config.

    When model is not provided, first checks the tier's provider: if ollama or
    ollama-local, routes to run_ask_ollama instead of claude CLI.

    `provider` disambiguates an explicit `model` across transports (an
    explicit model alone does not say which CLI/API it belongs to). When
    `provider` is None or "anthropic", the claude-CLI path is used — this
    preserves the historic default. Without an explicit model, the tier's
    configured provider always decides the transport; unsupported providers
    raise instead of silently falling back to the claude CLI.
    """
    if model:
        if provider in ("ollama", "ollama-local"):
            return run_ask_ollama(model, prompt, system=system, timeout=timeout)
        if provider == "codex":
            return _run_ask_codex(
                tier, prompt, cfg, system, output_format, timeout, model, max_budget_usd, effort
            )
        # provider is None or "anthropic": use claude-CLI path
        cmd = build_ask_command(
            model,
            output_format=output_format,
            system=system,
            max_budget_usd=max_budget_usd,
            effort=effort,
        )
        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=tempfile.gettempdir(),
        )
        return result.returncode, result.stdout, result.stderr

    # No explicit model: resolve provider from tier config
    resolved_provider = resolve_ask_provider(tier, cfg)
    if resolved_provider in ("ollama", "ollama-local"):
        local_model = ((cfg.get("agents") or {}).get(tier) or {}).get("model")
        if not local_model:
            raise ValueError(f"burnless ask: could not resolve a local model for tier '{tier}'")
        return run_ask_ollama(local_model, prompt, system=system, timeout=timeout)
    elif resolved_provider == "anthropic":
        resolved_model = resolve_ask_model(tier, cfg)
        cmd = build_ask_command(
            resolved_model,
            output_format=output_format,
            system=system,
            max_budget_usd=max_budget_usd,
            effort=effort,
        )
        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=tempfile.gettempdir(),
        )
        return result.returncode, result.stdout, result.stderr
    elif resolved_provider == "codex":
        return _run_ask_codex(
            tier, prompt, cfg, system, output_format, timeout, model, max_budget_usd, effort
        )
    else:
        raise ValueError(
            f"burnless ask: unsupported provider {resolved_provider!r} for tier {tier!r} — "
            "no adapter registered"
        )


def normalize_ask_error(
    returncode: int,
    stdout: str,
    stderr: str,
    timed_out: bool = False,
    signal: int | None = None,
) -> tuple[str | None, str | None]:
    """Turn a raw (returncode, stdout, stderr) transport result into a
    normalized (error_kind, error_message) pair. rc=0 -> (None, None); a bare
    rc!=0 with empty stderr becomes an explicit "empty_error" instead of a
    silent CLI failure (doc Sol sec 8 item 7)."""
    if returncode == 0:
        return None, None
    if timed_out:
        return "timeout", f"provider call timed out (rc={returncode})"
    if signal is not None:
        return "signal", f"provider process killed by signal {signal} (rc={returncode})"
    if stderr.strip():
        return "provider_error", stderr.strip()
    return (
        "empty_error",
        f"provider exited rc={returncode} with no stderr/stdout — likely a silent CLI failure",
    )


def build_ask_envelope(
    *,
    request_id: str,
    requested_tier: str | None,
    effective_tier: str,
    provider: str,
    model: str,
    effort: str | None,
    route_source: str,
    route_reason: str,
    route_signals: tuple[str, ...],
    returncode: int,
    stdout: str,
    stderr: str,
    timed_out: bool = False,
    signal: int | None = None,
    duration_ms: int = 0,
    usage: "UsageRecord | None" = None,
    cache_mode: str = "none",
    prefix_hash: str | None = None,
    dry_run: bool = False,
    warnings: tuple[str, ...] = (),
) -> dict:
    """Build the burnless.ask/v1 envelope (doc Sol sec 8) from a raw provider
    transport result. Never receives/holds the prompt; content/error_message
    are the only text derived from stdout/stderr, and only one of the two is
    populated depending on status."""
    error_kind, error_message = normalize_ask_error(returncode, stdout, stderr, timed_out, signal)
    status = "ok" if error_kind is None else "error"
    content = stdout.strip() if status == "ok" else None

    result = AskResult(
        request_id=request_id,
        status=status,
        content=content,
        requested_tier=requested_tier,
        effective_tier=effective_tier,
        provider=provider,
        model=model,
        effort=effort,
        route_source=route_source,
        route_reason=route_reason,
        route_signals=route_signals,
        usage=usage or UsageRecord(),
        duration_ms=duration_ms,
        cache_mode=cache_mode,
        prefix_hash=prefix_hash,
        dry_run=dry_run,
        error_kind=error_kind,
        error_message=error_message,
        warnings=warnings,
    )

    return {
        "schema": result.schema,
        "request_id": result.request_id,
        "status": result.status,
        "content": result.content,
        "requested_tier": result.requested_tier,
        "effective_tier": result.effective_tier,
        "provider": result.provider,
        "model": result.model,
        "effort": result.effort,
        "route": {
            "source": result.route_source,
            "reason": result.route_reason,
            "signals": list(result.route_signals),
        },
        "usage": {
            "input_tokens": result.usage.input_tokens,
            "output_tokens": result.usage.output_tokens,
            "cache_read_tokens": result.usage.cache_read_tokens,
            "cache_write_tokens": result.usage.cache_write_tokens,
            "basis": result.usage.basis,
        },
        "cost": {
            "usd": result.usage.cost_usd,
            "basis": result.usage.cost_basis,
        },
        "duration_ms": result.duration_ms,
        "cache_mode": result.cache_mode,
        "prefix_hash": result.prefix_hash,
        "dry_run": result.dry_run,
        "error_kind": result.error_kind,
        "error_message": result.error_message,
        "warnings": list(result.warnings),
    }
