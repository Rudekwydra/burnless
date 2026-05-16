"""Detect which LLM CLI providers are installed on this machine.

Used by `burnless init` to pick agent commands that will actually work
out of the box, rather than writing a default that requires manual editing.
"""
from __future__ import annotations
import shutil
from typing import TypedDict


class Detected(TypedDict):
    claude: str | None
    codex: str | None
    ollama: str | None


def detect_providers() -> Detected:
    """Return absolute paths to claude/codex/ollama binaries if found on PATH, else None."""
    return {
        "claude": shutil.which("claude"),
        "codex": shutil.which("codex"),
        "ollama": shutil.which("ollama"),
    }


def _claude_cmd(model: str, claude_path: str) -> str:
    return (
        f"{claude_path} -p --model {model} --permission-mode bypassPermissions "
        f"--allowedTools Read,Edit,Write,Bash,Glob,Grep,LS "
        f"--output-format stream-json --verbose --include-partial-messages"
    )


def _codex_cmd(codex_path: str, *, model: str | None = None, extra: str = "") -> str:
    parts = [f"{codex_path} exec --skip-git-repo-check --sandbox danger-full-access"]
    if model:
        parts.append(f"-m {model}")
    if extra:
        parts.append(extra)
    return " ".join(parts)


def build_agents(detected: Detected) -> dict:
    """Build the `agents:` block of config.yaml based on what is installed.

    Returns a dict matching DEFAULT_CONFIG["agents"] shape, with the
    appropriate commands wired up.
    """
    claude = detected.get("claude")
    codex = detected.get("codex")

    if claude and codex:
        return _both(claude, codex)
    if claude:
        return _claude_only(claude)
    if codex:
        return _codex_only(codex)
    return _neither()


def _both(claude: str, codex: str) -> dict:
    return {
        "gold": {
            "name": "claude-opus",
            "command": _claude_cmd("opus", claude),
            "role": "strategy_architecture_code_review",
            "use_for": ["architecture", "complex_reasoning", "high_level_planning"],
        },
        "silver": {
            "name": "codex-gpt-5.2",
            "command": _codex_cmd(codex),
            "role": "everyday_execution_filesystem",
            "use_for": ["docs", "prd", "prompts", "specs", "code", "implementation"],
            "providers": [
                {
                    "name": "codex-gpt-5.2",
                    "command": _codex_cmd(codex),
                    "provider": "codex",
                },
                {
                    "name": "claude-sonnet-4-6",
                    "command": _claude_cmd("claude-sonnet-4-6", claude),
                    "provider": "anthropic",
                },
            ],
        },
        "bronze": {
            "name": "claude-haiku-4-5",
            "command": _claude_cmd("claude-haiku-4-5-20251001", claude),
            "role": "summaries_classification_readonly",
            "use_for": ["summarize", "classify", "clean_logs"],
        },
    }


def _claude_only(claude: str) -> dict:
    return {
        "gold": {
            "name": "claude-opus",
            "command": _claude_cmd("opus", claude),
            "role": "strategy_architecture_code_review",
            "use_for": ["architecture", "complex_reasoning", "high_level_planning"],
        },
        "silver": {
            "name": "claude-sonnet-4-6",
            "command": _claude_cmd("claude-sonnet-4-6", claude),
            "role": "everyday_execution_filesystem",
            "use_for": ["docs", "prd", "prompts", "specs", "code", "implementation"],
        },
        "bronze": {
            "name": "claude-haiku-4-5",
            "command": _claude_cmd("claude-haiku-4-5-20251001", claude),
            "role": "summaries_classification_readonly",
            "use_for": ["summarize", "classify", "clean_logs"],
        },
    }


def _codex_only(codex: str) -> dict:
    return {
        "gold": {
            "name": "codex-gpt-5.4",
            "command": _codex_cmd(codex, model="gpt-5.4"),
            "role": "strategy_architecture_code_review",
            "use_for": ["architecture", "complex_reasoning", "high_level_planning"],
        },
        "silver": {
            "name": "codex-gpt-5.2",
            "command": _codex_cmd(codex),
            "role": "everyday_execution_filesystem",
            "use_for": ["docs", "prd", "prompts", "specs", "code", "implementation"],
        },
        "bronze": {
            "name": "codex-gpt-5.4-mini-low",
            "command": _codex_cmd(codex, model="gpt-5.4-mini", extra="-c model_reasoning_effort=low"),
            "role": "summaries_classification_readonly",
            "use_for": ["summarize", "classify", "clean_logs"],
        },
    }


def _neither() -> dict:
    return {
        "gold": {
            "name": "opus",
            "command": "claude --model opus -p --output-format stream-json --verbose --include-partial-messages",
            "role": "strategy_architecture",
            "use_for": ["architecture", "complex_reasoning", "high_level_planning"],
        },
        "silver": {
            "name": "sonnet",
            "command": "claude --model sonnet -p --output-format stream-json --verbose --include-partial-messages",
            "role": "documentation_structuring",
            "use_for": ["docs", "prd", "prompts", "specs"],
        },
        "bronze": {
            "name": "haiku",
            "command": "claude --model haiku -p --output-format stream-json --verbose --include-partial-messages",
            "role": "summaries_classification",
            "use_for": ["summarize", "classify", "clean_logs"],
        },
    }


def describe(detected: Detected) -> str:
    """Human-readable summary of detection result + chosen setup."""
    claude = detected.get("claude")
    codex = detected.get("codex")
    if claude and codex:
        return (
            f"Detected: claude={claude}, codex={codex}\n"
            f"Setup: gold=claude-opus, silver=codex-gpt-5.2 (primary) + "
            f"claude-sonnet-4-6 (fallback), bronze=claude-haiku-4-5"
        )
    if claude:
        return (
            f"Detected: claude={claude}\n"
            f"Setup: gold=claude-opus, silver=claude-sonnet-4-6, bronze=claude-haiku-4-5\n"
            f"(install codex for multi-provider silver fallback)"
        )
    if codex:
        return (
            f"Detected: codex={codex}\n"
            f"Setup: gold=codex-gpt-5.4, silver=codex-gpt-5.2, "
            f"bronze=codex-gpt-5.4-mini-low\n"
            f"(install Claude Code for higher quality on architecture/refactor)"
        )
    return (
        "WARNING: neither claude nor codex found on PATH.\n"
        "Wrote a generic claude-only config — install Claude Code "
        "(https://claude.ai/code) or Codex CLI before delegating, "
        "or edit .burnless/config.yaml manually."
    )
