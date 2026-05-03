from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_HISTORY_TURNS = 20


def load_history(path: Path, limit: int = DEFAULT_HISTORY_TURNS) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records[-limit:]


def append_turn(path: Path, **fields: Any) -> None:
    record = {
        "ts": fields.pop("ts", datetime.now(timezone.utc).isoformat()),
        "role": fields.pop("role"),
        "raw": fields.pop("raw", ""),
        "capsule": fields.pop("capsule", ""),
        "think": fields.pop("think", ""),
        "delegates": fields.pop("delegates", []),
    }
    record.update(fields)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def to_messages_array(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for rec in history:
        role = rec.get("role")
        capsule = (rec.get("capsule") or "").strip()
        if role not in {"user", "assistant"} or not capsule:
            continue
        messages.append({"role": role, "content": capsule})
    return messages
