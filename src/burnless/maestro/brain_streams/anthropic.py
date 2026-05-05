from __future__ import annotations

from typing import TYPE_CHECKING, Any, Iterator

from . import NormalizedEvent

if TYPE_CHECKING:
    import anthropic as _anthropic

_MAX_TOKENS = 6000


def create_stream(
    client: _anthropic.Anthropic,
    *,
    model: str,
    system: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    thinking_kw: dict[str, Any],
) -> Iterator[NormalizedEvent]:
    params: dict[str, Any] = {
        "model": model,
        "max_tokens": _MAX_TOKENS,
        "system": system,
        "messages": messages,
        "stream": True,
        "extra_headers": {"anthropic-beta": "extended-cache-ttl-2025-04-11"},
    }
    if thinking_kw:
        params["thinking"] = thinking_kw
        params["output_config"] = {"effort": "high"}

    raw_stream = client.messages.create(**params)
    for event in raw_stream:
        event_type = getattr(event, "type", None)
        delta = getattr(event, "delta", None)
        delta_type = getattr(delta, "type", None)

        if delta_type == "thinking_delta" or event_type == "thinking_delta":
            chunk = getattr(delta, "thinking", "") or getattr(event, "thinking", "")
            if chunk:
                yield NormalizedEvent(kind="think_delta", text=chunk)
        elif delta_type == "text_delta" or event_type == "text_delta":
            text = getattr(delta, "text", "") or getattr(event, "text", "")
            yield NormalizedEvent(kind="text_delta", text=text)
        elif event_type == "message_start":
            msg_usage = _event_usage(getattr(event, "message", None))
            if msg_usage:
                yield NormalizedEvent(kind="usage", usage=msg_usage)

        direct_usage = _event_usage(event)
        if direct_usage:
            yield NormalizedEvent(kind="usage", usage=direct_usage)

    yield NormalizedEvent(kind="done")


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
