from __future__ import annotations

import os
from typing import Any, Iterator

from . import NormalizedEvent


def create_stream(
    client: Any,
    *,
    model: str,
    system: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    thinking_kw: Any = None,
) -> Iterator[NormalizedEvent]:
    try:
        import openai
    except ImportError:
        raise ImportError("pip install openai to use OpenAI Brain adapter")

    system_str = "\n\n".join(
        block.get("text", "") for block in system if block.get("type") == "text"
    )
    combined_msgs = [{"role": "system", "content": system_str}] + list(messages)

    oai_client = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    stream = oai_client.chat.completions.create(
        model=model,
        messages=combined_msgs,
        stream=True,
        stream_options={"include_usage": True},
    )

    for chunk in stream:
        usage = getattr(chunk, "usage", None)
        if usage is not None:
            cached = 0
            details = getattr(usage, "prompt_tokens_details", None)
            if details is not None:
                cached = int(getattr(details, "cached_tokens", 0) or 0)
            yield NormalizedEvent(
                kind="usage",
                usage={
                    "input_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
                    "output_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": cached,
                    "cache_inferred": True,
                },
            )

        choices = getattr(chunk, "choices", None) or []
        if not choices:
            continue
        delta = getattr(choices[0], "delta", None)
        if delta is None:
            continue
        content = getattr(delta, "content", None)
        if content:
            yield NormalizedEvent(kind="text_delta", text=content)

    yield NormalizedEvent(kind="done")
