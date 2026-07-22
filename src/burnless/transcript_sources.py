from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

_logger = logging.getLogger(__name__)

_IGNORED_RESTORE_MARKERS = (
    "[BURNLESS RESTORE]",
    "## Trocas ainda não consolidadas",
    "[BURNLESS SEED]",
)

_CODEX_NOISE_PREFIXES = ("<environment_context>", "<user_instructions>")
_CODEX_TEXT_BLOCK_TYPES = ("input_text", "output_text")
_CODEX_KNOWN_TOP_TYPES = {
    "response_item",
    "session_meta",
    "compacted",
    "event_msg",
    "world_state",
    "turn_context",
}


def resolve_path(host: str, sid: str, cwd: str | None) -> Path | None:
    if host == "claude":
        return _resolve_claude(sid, cwd)
    if host == "codex":
        return _resolve_codex(sid)
    raise ValueError(f"unknown host: {host!r}")


def _resolve_claude(sid: str, cwd: str | None) -> Path | None:
    projects_root = Path.home() / ".claude" / "projects"
    if cwd:
        slug = cwd.replace("/", "-").replace(".", "-")
        candidate = projects_root / slug / f"{sid}.jsonl"
        if candidate.exists():
            return candidate
    if not projects_root.exists():
        return None
    matches = list(projects_root.glob(f"*/{sid}.jsonl"))
    return matches[0] if matches else None


def _resolve_codex(sid: str) -> Path | None:
    sessions_root = Path.home() / ".codex" / "sessions"
    base_date = _codex_uuid7_date(sid)

    if base_date is not None and sessions_root.exists():
        for delta in (0, -1, 1):
            day = base_date + timedelta(days=delta)
            day_dir = sessions_root / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.day:02d}"
            if not day_dir.exists():
                continue
            for candidate in sorted(day_dir.glob(f"rollout-*-{sid}.jsonl")):
                if _codex_session_id_matches(candidate, sid):
                    return candidate

    if sessions_root.exists():
        for candidate in sorted(sessions_root.rglob(f"*-{sid}.jsonl")):
            if _codex_session_id_matches(candidate, sid):
                return candidate

    return None


def _codex_uuid7_date(sid: str):
    hexstr = sid.replace("-", "")[:12]
    try:
        epoch_ms = int(hexstr, 16)
        return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).date()
    except (ValueError, OSError, OverflowError):
        return None


def _codex_session_id_matches(path: Path, sid: str) -> bool:
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            first_line = f.readline()
    except OSError:
        return False
    first_line = first_line.strip()
    if not first_line:
        return False
    try:
        obj = json.loads(first_line)
    except json.JSONDecodeError:
        return False
    if not isinstance(obj, dict) or obj.get("type") != "session_meta":
        return False
    payload = obj.get("payload")
    if not isinstance(payload, dict):
        return False
    return payload.get("session_id") == sid


def iter_turns(host: str, path: Path) -> Iterator[dict]:
    if host not in ("claude", "codex"):
        raise ValueError(f"unknown host: {host!r}")
    f = path.open("r", encoding="utf-8", errors="ignore")
    if host == "claude":
        return _iter_turns_claude(f)
    return _iter_turns_codex(f)


def _claude_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
                continue
            if block.get("type") == "tool_use":
                continue
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
        return "\n".join(parts)
    return ""


def _claude_turn_from_obj(obj: dict, line_no: int) -> dict:
    """One decoded claude JSONL record -> turn dict. Single source of the
    claude record shape; iter_turns and parse_turn_line both use it."""
    message = obj.get("message") if isinstance(obj.get("message"), dict) else {}
    role = str(message.get("role") or obj.get("role") or obj.get("type") or "").strip().lower()
    content = message.get("content")
    text = _claude_content_text(content)
    turn = dict(obj)
    turn["_line_no"] = line_no
    turn["role"] = role
    turn["text"] = text
    return turn


def _iter_turns_claude(f) -> Iterator[dict]:
    with f:
        for line_no, line in enumerate(f):
            text_line = line.strip()
            if not text_line:
                continue
            try:
                obj = json.loads(text_line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            yield _claude_turn_from_obj(obj, line_no)


def _codex_content_text(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") in _CODEX_TEXT_BLOCK_TYPES:
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


def _is_codex_noise(text: str) -> bool:
    stripped = (text or "").lstrip()
    return stripped.startswith(_CODEX_NOISE_PREFIXES)


def _codex_turn_from_obj(obj: dict, strict: bool = False) -> tuple[dict | None, str | None]:
    """One decoded codex rollout record -> (turn, skip_reason). Single source
    of the codex record shape; iter_turns and parse_turn_line both use it."""
    top_type = obj.get("type")
    if top_type not in _CODEX_KNOWN_TOP_TYPES:
        if strict:
            raise ValueError(f"unknown codex record type: {top_type!r}")
        return None, f"unknown_type:{top_type}"
    if top_type != "response_item":
        return None, f"top_type:{top_type}"
    payload = obj.get("payload")
    if not isinstance(payload, dict):
        return None, "malformed_payload"
    if payload.get("type") != "message":
        return None, f"payload_type:{payload.get('type')}"
    role = payload.get("role")
    if role not in ("user", "assistant"):
        return None, f"role:{role}"
    text = _codex_content_text(payload.get("content"))
    if not text.strip():
        return None, "empty_text"
    if role == "user" and _is_codex_noise(text):
        return None, "boot_noise"
    return {"role": role, "text": text}, None


def _iter_turns_codex(f) -> Iterator[dict]:
    strict = os.environ.get("BURNLESS_TRANSCRIPT_STRICT") == "1"
    skip_counts: dict[str, int] = {}

    def _skip(key: str) -> None:
        skip_counts[key] = skip_counts.get(key, 0) + 1

    with f:
        for line in f:
            text_line = line.strip()
            if not text_line:
                continue
            try:
                obj = json.loads(text_line)
            except json.JSONDecodeError:
                _skip("malformed_json")
                continue
            if not isinstance(obj, dict):
                _skip("non_dict")
                continue

            turn, skip_key = _codex_turn_from_obj(obj, strict=strict)
            if skip_key is not None:
                _skip(skip_key)
                continue
            if turn is not None:
                yield turn

    if skip_counts:
        _logger.debug("iter_turns(codex): %d lines skipped: %s", sum(skip_counts.values()), skip_counts)


def _is_restore_noise(text: str) -> bool:
    blob = text or ""
    return any(marker in blob for marker in _IGNORED_RESTORE_MARKERS)


def parse_turn_line(host: str, line: str, line_no: int = 0) -> dict | None:
    """Decode ONE transcript line into a turn dict (or None). Same decoding as
    iter_turns, exposed line-at-a-time for incremental readers (chat --follow).
    Malformed/partial lines return None, never raise (fail-open like iter_turns)."""
    if host not in ("claude", "codex"):
        raise ValueError(f"unknown host: {host!r}")
    text_line = (line or "").strip()
    if not text_line:
        return None
    try:
        obj = json.loads(text_line)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    if host == "claude":
        return _claude_turn_from_obj(obj, line_no)
    turn, _skip = _codex_turn_from_obj(obj)
    return turn


def turn_meta(host: str, turn: dict) -> dict:
    """Presentation metadata for a parsed turn: {ts, tool_names, is_sidechain}.
    Host transcript-format knowledge stays HERE (paridade invariante 14) —
    viewers consume, they never re-parse records themselves."""
    if host == "claude":
        message = turn.get("message") if isinstance(turn.get("message"), dict) else {}
        tool_names: list[str] = []
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "tool_use":
                    tool_names.append(str(block.get("name") or "tool"))
                elif btype == "tool_result":
                    tool_names.append("result")
        return {
            "ts": str(turn.get("timestamp") or ""),
            "tool_names": tool_names,
            "is_sidechain": bool(turn.get("isSidechain") or message.get("isSidechain")),
        }
    return {
        "ts": str(turn.get("ts") or ""),
        "tool_names": list(turn.get("tool_names") or []),
        "is_sidechain": False,
    }
