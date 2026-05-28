from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable, Iterator

import anthropic

from ..maestro_adapters import MaestroAdapter, current_anthropic_adapter
from ..codec.glossary_loader import load_glossary
from .streams import NormalizedEvent

DEFAULT_BRAIN_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 6000
THINKING_BUDGET_TOKENS = 4000


def run_maestro_turn(
    *,
    user_capsule: str,
    history_messages: list[dict[str, Any]],
    project_root: Path,
    model: str = DEFAULT_BRAIN_MODEL,
    client: anthropic.Anthropic | None = None,
    on_think_delta: Callable[[str], None] | None = None,
    adapter: MaestroAdapter | None = None,
) -> dict[str, Any]:
    if adapter is None:
        adapter = current_anthropic_adapter(model)
    client = client or anthropic.Anthropic()
    system = build_system_blocks(project_root=project_root, history_messages=history_messages)
    # Copy history defensively (mutating the caller's list would break
    # cross-turn invariants) and copy each message dict shallowly so the
    # cache_control mutation in _apply_history_cache_breakpoint does not
    # leak into the caller's session state.
    messages = [dict(m) for m in history_messages]
    _apply_history_cache_breakpoint(messages, ttl="5m")
    messages.append({"role": "user", "content": user_capsule})

    text_parts: list[str] = []
    think_parts: list[str] = []
    usage: dict[str, int] = {}

    stream = _create_stream(adapter, client=client, model=model, system=system, messages=messages)
    for event in stream:
        if event.kind == "think_delta":
            think_parts.append(event.text)
            if event.text and on_think_delta is not None:
                on_think_delta(event.text)
        elif event.kind == "text_delta":
            text_parts.append(event.text)
        elif event.kind == "usage":
            _merge_usage(usage, event.usage or {})

    body = "".join(text_parts)
    think_text = "".join(think_parts).strip() or _extract_block(body, "THINK")
    capsule_text = _extract_block(body, "CAPSULE")
    delegate_text = _extract_block(body, "DELEGATE")
    if not capsule_text:
        capsule_text = body.strip()
    capsule_text = _normalize_capsule_lines(capsule_text)
    return {
        "think_text": think_text,
        "capsule_text": capsule_text.strip(),
        "delegate_lines": _delegate_lines(delegate_text),
        "usage": usage,
        "raw_text": body,
    }


def _create_stream(
    adapter: MaestroAdapter,
    *,
    client: anthropic.Anthropic,
    model: str,
    system: list[dict[str, Any]],
    messages: list[dict[str, Any]],
) -> Iterator[NormalizedEvent]:
    from .streams.anthropic import create_stream as _anthropic_stream

    if adapter.kind == "anthropic":
        thinking_kw: dict[str, Any] = {"type": "adaptive"} if adapter.supports_thinking else {}
        return _anthropic_stream(
            client, model=model, system=system, messages=messages, thinking_kw=thinking_kw
        )
    if adapter.kind == "openai":
        from .streams.openai import create_stream as _openai_stream

        return _openai_stream(None, model=model, system=system, messages=messages)
    if adapter.kind == "gemini":
        from .streams.gemini import create_stream as _gemini_stream

        return _gemini_stream(None, model=model, system=system, messages=messages)
    if adapter.kind == "openrouter":
        from .streams.openrouter import create_stream as _openrouter_stream

        return _openrouter_stream(None, model=model, system=system, messages=messages)
    raise NotImplementedError(
        f"Brain stream for adapter kind={adapter.kind!r} is not implemented"
    )


def build_system_blocks(
    *, project_root: Path, history_messages: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    role_path = project_root / "_design" / "maestro_v1" / "brain_role.md"
    role_text = role_path.read_text(encoding="utf-8")
    blocks = [
        _block(load_glossary(project_root), ttl="1h"),
        _block(role_text, ttl="1h"),
    ]
    recent = _recent_capsules_text(history_messages)
    if recent:
        blocks.append(_block(recent, ttl="5m"))
    return blocks


def _block(text: str, *, ttl: str | None = None) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "text", "text": text}
    if ttl:
        block["cache_control"] = {"type": "ephemeral", "ttl": ttl}
    return block


def _apply_history_cache_breakpoint(
    messages: list[dict[str, Any]], *, ttl: str = "5m"
) -> list[dict[str, Any]]:
    """Mark the tail of accumulated history as a cache breakpoint.

    Anthropic allows up to 4 cache_control breakpoints per request. The
    maestro's system array already uses 3 (glossary, role, recent_capsules).
    We spend the 4th on the LAST block of the LAST history message — so
    every turn after this point reads the history from cache (10% input
    price) instead of re-billing it at full rate.

    The current turn's user_capsule (appended AFTER this call) is NOT
    cached: it's volatile by definition.

    `messages` is mutated in place AND returned for clarity.
    """
    if not messages:
        return messages
    last = messages[-1]
    content = last.get("content")
    # Normalize string content into a single text block so we can attach
    # cache_control. Anthropic accepts either form for input messages.
    if isinstance(content, str):
        last["content"] = [{"type": "text", "text": content}]
    elif not isinstance(content, list) or not content:
        # Unknown shape — leave it alone rather than corrupt the request.
        return messages
    blocks = last["content"]
    # Find the last text-ish block to anchor the breakpoint.
    for block in reversed(blocks):
        if isinstance(block, dict) and block.get("type") in ("text", "tool_result"):
            block["cache_control"] = {"type": "ephemeral", "ttl": ttl}
            break
    return messages


def _recent_capsules_text(history_messages: list[dict[str, Any]]) -> str:
    if not history_messages:
        return ""
    rendered: list[str] = ["[recent_capsules]"]
    for msg in history_messages[-20:]:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        rendered.append(f"{role}: {content}")
    return "\n".join(rendered)


def _extract_block(text: str, name: str) -> str:
    pattern = rf"\[{name}\](.*?)\[/{name}\]"
    match = re.search(pattern, text, flags=re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _delegate_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _normalize_capsule_lines(text: str) -> str:
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = " ".join(raw_line.strip().split())
        if not line:
            continue
        prefix_match = re.match(r"^(.{0,32}?::\s+)", line)
        prefix = prefix_match.group(1) if prefix_match else "gld :: "
        while len(line) > 80:
            cut = line.rfind(" ", 0, 80)
            if cut <= len(prefix):
                cut = 80
            lines.append(line[:cut].rstrip())
            line = prefix + line[cut:].strip()
        lines.append(line)
    return "\n".join(lines)


def _merge_usage(dst: dict[str, int], src: dict[str, int]) -> None:
    for key, value in src.items():
        dst[key] = max(dst.get(key, 0), value)
