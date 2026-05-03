from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

import anthropic

from ..codec.glossary_loader import load_glossary

DEFAULT_BRAIN_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 6000
THINKING_BUDGET_TOKENS = 4000


def run_brain_turn(
    *,
    user_capsule: str,
    history_messages: list[dict[str, Any]],
    project_root: Path,
    model: str = DEFAULT_BRAIN_MODEL,
    client: anthropic.Anthropic | None = None,
    on_think_delta: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    client = client or anthropic.Anthropic()
    system = build_system_blocks(project_root=project_root, history_messages=history_messages)
    messages = list(history_messages)
    messages.append({"role": "user", "content": user_capsule})

    text_parts: list[str] = []
    think_parts: list[str] = []
    usage: dict[str, int] = {}

    stream = _create_stream(client, model=model, system=system, messages=messages)
    for event in stream:
        _merge_usage(usage, _event_usage(event))
        delta = getattr(event, "delta", None)
        delta_type = getattr(delta, "type", None)
        event_type = getattr(event, "type", None)
        if delta_type == "thinking_delta" or event_type == "thinking_delta":
            chunk = getattr(delta, "thinking", "") or getattr(event, "thinking", "")
            think_parts.append(chunk)
            if chunk and on_think_delta is not None:
                on_think_delta(chunk)
        elif delta_type == "text_delta" or event_type == "text_delta":
            text_parts.append(getattr(delta, "text", "") or getattr(event, "text", ""))
        elif event_type == "message_start":
            _merge_usage(usage, _event_usage(getattr(event, "message", None)))

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
    client: anthropic.Anthropic,
    *,
    model: str,
    system: list[dict[str, Any]],
    messages: list[dict[str, Any]],
) -> Any:
    params: dict[str, Any] = {
        "model": model,
        "max_tokens": DEFAULT_MAX_TOKENS,
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "high"},
        "system": system,
        "messages": messages,
        "stream": True,
        "extra_headers": {"anthropic-beta": "extended-cache-ttl-2025-04-11"},
    }
    return client.messages.create(**params)


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


def _event_usage(event: Any) -> dict[str, int]:
    usage_obj = getattr(event, "usage", None)
    if usage_obj is None:
        return {}
    out: dict[str, int] = {}
    for key in (
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    ):
        value = getattr(usage_obj, key, None)
        if value is not None:
            out[key] = int(value or 0)
    return out


def _merge_usage(dst: dict[str, int], src: dict[str, int]) -> None:
    for key, value in src.items():
        dst[key] = max(dst.get(key, 0), value)
