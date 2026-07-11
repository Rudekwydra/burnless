from __future__ import annotations

import shlex
import subprocess
import tempfile


DEFAULT_ASK_SYSTEM = (
    "You are a plain text-completion function. Answer only in the requested "
    "format. Do not use tools, do not take actions, do not write files."
)

_DISALLOWED_TOOLS = [
    "Bash", "Edit", "Write", "Read", "Glob", "Grep", "Task",
    "WebFetch", "WebSearch", "NotebookEdit", "TodoWrite",
]


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


def build_ask_command(model: str, output_format: str = "text", system: str | None = None, max_budget_usd: float | None = None) -> list[str]:
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
    return cmd


def run_ask(
    tier: str,
    prompt: str,
    cfg: dict,
    system: str | None = None,
    output_format: str = "text",
    timeout: int = 120,
    model: str | None = None,
    max_budget_usd: float | None = None,
) -> tuple[int, str, str]:
    """Run a pure completion call. Returns (returncode, stdout, stderr).

    Runs with cwd=a neutral temp dir (not the project root) so even if a
    future claude CLI version changes what --exclude-dynamic-system-prompt-sections
    covers, there is no CLAUDE.md file present to discover.

    When model is provided (not None and not empty), uses it directly without
    calling resolve_ask_model. Otherwise, resolves model from tier/config.
    """
    resolved_model = model if model else resolve_ask_model(tier, cfg)
    cmd = build_ask_command(resolved_model, output_format=output_format, system=system, max_budget_usd=max_budget_usd)
    result = subprocess.run(
        cmd,
        input=prompt,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=tempfile.gettempdir(),
    )
    return result.returncode, result.stdout, result.stderr
