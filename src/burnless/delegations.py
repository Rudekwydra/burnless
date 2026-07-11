from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime, timezone

from .codec.decoder import normalize_worker_envelope


DELEGATION_TEMPLATE = """\
# Delegation {id}

- **created_at:** {ts}
- **agent:** {agent_name} ({tier})
- **routed_by:** {routed_by}
- **status:** pending

## Goal

{goal}

## Task

{task}

## Success criteria

{success}

## Report kind

{kind_hint}
"""


def render_delegation(
    *,
    delegation_id: str,
    goal: str,
    task: str,
    success: str,
    kind_hint: str,
    agent_name: str,
    tier: str,
    routed_by: str,
) -> str:
    return DELEGATION_TEMPLATE.format(
        id=delegation_id,
        ts=datetime.now(timezone.utc).isoformat(),
        goal=goal,
        task=task,
        success=success,
        kind_hint=kind_hint,
        agent_name=agent_name,
        tier=tier,
        routed_by=routed_by or "default-bronze",
    )


def _extract_text_from_jsonl_stream(stdout: str) -> str:
    """If stdout is a claude -p stream-json JSONL, extract the result text field."""
    import json as _json
    text_parts: list[str] = []
    result_text: str | None = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = _json.loads(line)
        except _json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        # top-level result event carries the full assistant response
        if obj.get("type") == "result" and isinstance(obj.get("result"), str):
            result_text = obj["result"]
        # stream_event content_block_delta carries incremental text
        elif obj.get("type") == "stream_event":
            ev = obj.get("event") or {}
            delta = ev.get("delta") or {}
            if delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
                text_parts.append(delta["text"])
    if result_text is not None:
        return result_text
    if text_parts:
        return "".join(text_parts)
    return stdout


def extract_result_json(stdout: str) -> dict | None:
    """Find the last fenced ```json block in stdout and parse it. Best-effort."""
    import re as _re
    if not stdout:
        return None
    stdout = _extract_text_from_jsonl_stream(stdout)
    stdout = _re.sub(r"<\|?channel\|?>", "", stdout)
    marker = "```json"
    end_marker = "```"
    # Collect all ```json block positions (newest-first scan)
    positions = []
    search_from = 0
    while True:
        pos = stdout.find(marker, search_from)
        if pos == -1:
            break
        positions.append(pos)
        search_from = pos + len(marker)
    if not positions:
        return _try_trailing_json(stdout)
    # Try blocks from last to first; return the first that parses to a dict with "status"
    for pos in reversed(positions):
        rest = stdout[pos + len(marker):]
        close = rest.find(end_marker)
        payload = rest[:close] if close != -1 else rest
        try:
            parsed = json.loads(payload.strip())
            if isinstance(parsed, dict) and "status" in parsed:
                return normalize_worker_envelope(parsed)
        except json.JSONDecodeError:
            continue
    # No block with "status" found; try trailing bare json
    return _try_trailing_json(stdout)


def _try_trailing_json(stdout: str) -> dict | None:
    s = stdout.strip()
    if not s.endswith("}"):
        return None
    # walk backward to matching open brace
    depth = 0
    for i in range(len(s) - 1, -1, -1):
        c = s[i]
        if c == "}":
            depth += 1
        elif c == "{":
            depth -= 1
            if depth == 0:
                try:
                    return normalize_worker_envelope(json.loads(s[i:]))
                except json.JSONDecodeError:
                    return None
    return None


def write_delegation(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_log(path: Path, run_result: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (
        f"# agent: {run_result.get('agent')}\n"
        f"# command: {' '.join(run_result.get('command', []))}\n"
        f"# kind: {run_result.get('kind')}\n"
        f"# returncode: {run_result.get('returncode')}\n"
        f"# duration_s: {run_result.get('duration_s')}\n"
        f"# started_at: {run_result.get('started_at')}\n"
        f"# ended_at: {run_result.get('ended_at')}\n"
        "\n--- STDOUT ---\n"
        f"{run_result.get('stdout', '')}\n"
        "\n--- STDERR ---\n"
        f"{run_result.get('stderr', '')}\n"
    )
    path.write_text(body, encoding="utf-8")


def _json_safe(obj):
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    return obj


def write_summary(path: Path, summary: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(_json_safe(summary), f, indent=2, ensure_ascii=False)
