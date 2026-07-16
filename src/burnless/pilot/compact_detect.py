from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class CompactSummary:
    session_id: str
    summary_text: str
    type: str
    uuid: str | None
    parent_uuid: str | None
    line_index: int


def detect_compact_summaries(transcript_path: Path) -> list[CompactSummary]:
    """Scan JSONL for isCompactSummary records, robust to malformed input."""
    if not transcript_path.exists():
        return []

    summaries: list[CompactSummary] = []
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            for line_index, line in enumerate(f):
                line = line.rstrip("\n")
                if not line or not line.strip():
                    continue
                try:
                    rec: dict[str, Any] = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if rec.get("isCompactSummary") is not True:
                    continue

                session_id = str(rec.get("sessionId") or "")
                type_val = str(rec.get("type") or "")
                uuid = rec.get("uuid")
                parent_uuid = rec.get("parentUuid")
                summary_text = _extract_content(rec.get("message"))

                summaries.append(
                    CompactSummary(
                        session_id=session_id,
                        summary_text=summary_text,
                        type=type_val,
                        uuid=uuid,
                        parent_uuid=parent_uuid,
                        line_index=line_index,
                    )
                )
    except OSError:
        return []

    return summaries


def has_genuine_compact(transcript_path: Path) -> bool:
    """Return True iff at least one genuine (user-type, non-empty) compact summary exists."""
    # A native in-thread compact envelope is injected as a user record with the same sessionId;
    # non-user type is a fork/tamper shape and is NOT genuine.
    for summary in detect_compact_summaries(transcript_path):
        if (
            summary.type == "user"
            and summary.session_id
            and summary.summary_text
        ):
            return True
    return False


def _extract_content(message: Any) -> str:
    """Extract summary text from message.content (string or list of blocks)."""
    if not isinstance(message, dict):
        return ""

    content = message.get("content")
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)

    return ""
