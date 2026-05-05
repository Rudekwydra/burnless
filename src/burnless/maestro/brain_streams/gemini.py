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
        import google.genai as genai
    except ImportError:
        raise ImportError("pip install google-genai to use Gemini Brain adapter")

    system_str = "\n\n".join(
        block.get("text", "") for block in system if block.get("type") == "text"
    )

    gemini_messages = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        gemini_role = "model" if role == "assistant" else "user"
        if isinstance(content, list):
            parts = [{"text": part.get("text", "")} for part in content if part.get("type") == "text"]
        else:
            parts = [{"text": str(content)}]
        gemini_messages.append({"role": gemini_role, "parts": parts})

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    gemini_client = genai.Client(api_key=api_key)

    stream = gemini_client.models.generate_content_stream(
        model=model,
        contents=gemini_messages,
        config={"system_instruction": system_str},
    )

    thought_emitted = False
    for chunk in stream:
        usage_meta = getattr(chunk, "usage_metadata", None)
        if usage_meta is not None:
            cached = int(getattr(usage_meta, "cached_content_token_count", 0) or 0)
            yield NormalizedEvent(
                kind="usage",
                usage={
                    "input_tokens": int(getattr(usage_meta, "prompt_token_count", 0) or 0),
                    "output_tokens": int(getattr(usage_meta, "candidates_token_count", 0) or 0),
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": cached,
                    "cache_inferred": True,
                },
            )

        candidates = getattr(chunk, "candidates", None) or []
        if candidates:
            content_obj = getattr(candidates[0], "content", None)
            parts = getattr(content_obj, "parts", None) or []
            for part in parts:
                if getattr(part, "thought", False) and not thought_emitted:
                    yield NormalizedEvent(kind="think_delta", text="[gemini thought signature]")
                    thought_emitted = True

        text = getattr(chunk, "text", None)
        if text:
            yield NormalizedEvent(kind="text_delta", text=text)

    yield NormalizedEvent(kind="done")
