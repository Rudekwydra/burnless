#!/usr/bin/env bash
# Burnless Claude Code SessionStart hook.
# Reads stdin JSON, finds most recent rollover capsule, emits seed JSON.
# FAIL-OPEN on all errors; emits nothing on failure.
set -uo pipefail

INPUT="$(cat)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
STATE_DIR="$HOME/.burnless/state"

# Parse input JSON defensively and emit seed if capsule exists.
INPUT_JSON="$INPUT" "$PYTHON_BIN" - <<'PY'
import json
import os
import sys
from pathlib import Path

try:
    payload = json.loads(os.environ.get("INPUT_JSON", "{}"))
except json.JSONDecodeError:
    sys.exit(0)

state_dir = Path(os.path.expanduser("~")) / ".burnless" / "state"

# Find most recent consolidated rollover capsule.
consolidated = state_dir / "rollover-consolidated.md"
capsule_file = None
capsule_content = ""

if consolidated.exists():
    try:
        capsule_content = consolidated.read_text(encoding="utf-8")
        if capsule_content.strip():
            capsule_file = consolidated
    except OSError:
        pass

# If consolidated doesn't exist or is empty, find newest session-*.rollover.md
if not capsule_file:
    try:
        candidates = list(state_dir.glob("session-*.rollover.md"))
        if candidates:
            newest = max(candidates, key=lambda p: p.stat().st_mtime)
            try:
                content = newest.read_text(encoding="utf-8")
                if content.strip():
                    capsule_file = newest
                    capsule_content = content
            except OSError:
                pass
    except (OSError, ValueError):
        pass

# If we have a capsule, emit JSON with seed message prepended.
if capsule_file and capsule_content.strip():
    try:
        seed_msg = "[BURNLESS SEED] sessao iniciada leve a partir da capsule rolante.\n\n"
        final_content = seed_msg + capsule_content
        output = json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": final_content,
            }
        }, ensure_ascii=False)
        print(output)
    except Exception:
        sys.exit(0)
else:
    sys.exit(0)
PY
