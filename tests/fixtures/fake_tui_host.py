#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone


def main() -> int:
    host = os.environ.get("FAKE_TUI_HOST", "fake-host")
    session_id = os.environ.get("FAKE_TUI_SESSION_ID", "sess-1")
    run_id = os.environ.get("BURNLESS_PILOT_RUN_ID", "pilot-test")
    events_path = os.environ.get("FAKE_TUI_EVENTS_PATH")

    if events_path:
        os.makedirs(os.path.dirname(events_path), exist_ok=True)

    def emit(event: dict) -> None:
        if events_path:
            with open(events_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")

    sys.stdout.write("\x1b[?1049h")
    sys.stdout.write(f"{host} ready ({run_id})\n")
    sys.stdout.flush()

    for raw in sys.stdin:
        text = raw.rstrip("\n")
        if text == "/clear":
            emit({"event": "session_reset", "host": host, "session_id": session_id, "run_id": run_id})
            sys.stdout.write("\x1b[2J\x1b[H")
            sys.stdout.write(f"{host} reset\n")
            sys.stdout.flush()
            continue
        if text == "/exit":
            emit({"event": "turn_end", "host": host, "session_id": session_id, "run_id": run_id})
            break
        emit({
            "event": "turn",
            "host": host,
            "session_id": session_id,
            "run_id": run_id,
            "text": text,
            "usage": {"input_tokens": len(text), "output_tokens": len(text) // 2},
            "ts": datetime.now(timezone.utc).isoformat(),
        })
        sys.stdout.write(f"echo:{text}\n")
        sys.stdout.flush()

    sys.stdout.write("\x1b[?1049l")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
