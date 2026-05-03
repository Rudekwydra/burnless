from __future__ import annotations

from pathlib import Path
from typing import Any

import anthropic

from .glossary_loader import load_glossary

DEFAULT_DECODER_MODEL = "claude-haiku-4-5"

STYLE_GUIDE = """
capsule: "gld :: OK status app/auth d010 d011 summarized"
output: "Auth status reviewed. D010 and D011 look fine in the summary."

capsule: "gld del→T51 slv imp app/auth :: schema+router+prompts, build val"
output: "Opening T51 to implement the auth flow and validate the build."

capsule: "gld :: need to pick between app/F1 and app/F2 ?"
output: "Need you to choose: continue with F1 or F2?"

capsule: "gld :: PART build failed, ref:exec/T44"
output: "Made partial progress, but the build failed. Detail in T44."

capsule: "gld :: raw:had an idea received"
output: "Got it. Send the idea and I'll organize the next step."
""".strip()


def decode(
    capsule: str,
    *,
    project_root: Path | None = None,
    model: str = DEFAULT_DECODER_MODEL,
    client: anthropic.Anthropic | None = None,
) -> str:
    capsule = (capsule or "").strip()
    if not capsule:
        return ""
    client = client or anthropic.Anthropic()
    prompt = "\n\n".join(
        [
            "Convert Burnless capsules into natural prose in the user's language.",
            (
                "Tone: friendly, direct, no fluff. Respond in 1 to 4 sentences. "
                "No headings, markdown, bullets, emoji or meta-commentary. "
                "Do not explain that conversion, glossary, capsule or protocol happened."
            ),
            "[GLOSSARY]",
            load_glossary(project_root),
            "[STYLE_GUIDE]",
            STYLE_GUIDE,
            "[CAPSULE]",
            capsule,
            "[OUTPUT]",
        ]
    )
    try:
        response = client.messages.create(
            model=model,
            max_tokens=700,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception:
        return capsule
    text = _response_text(response)
    return text or capsule


def _response_text(response: Any) -> str:
    parts: list[str] = []
    for block in getattr(response, "content", []):
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "\n".join(parts).strip()
