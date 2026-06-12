#!/usr/bin/env bash
# Burnless Claude Code SessionStart hook.
# Reads stdin JSON, checks pending_seed.md pointer, emits seed JSON if fresh.
# FAIL-OPEN on all errors; emits nothing on failure.
set -uo pipefail

INPUT="$(cat)" 2>/dev/null || exit 0
PYTHON_BIN="${PYTHON_BIN:-python3}"
POINTER_FILE="$HOME/.burnless/state/pending_seed.md"

# Check pointer, staleness, and emit seed if valid.
INPUT_JSON="$INPUT" POINTER_FILE="$POINTER_FILE" "$PYTHON_BIN" - <<'PY'
import json
import os
import sys
import time
from pathlib import Path

try:
    payload = json.loads(os.environ.get("INPUT_JSON", "{}"))
except json.JSONDecodeError:
    sys.exit(0)

pointer_file = os.environ.get("POINTER_FILE", "")
if not pointer_file:
    sys.exit(0)

p = Path(pointer_file)

# If pointer doesn't exist, emit nothing.
if not p.exists():
    sys.exit(0)

# Check staleness: if mtime > 24h (86400s), remove and exit.
try:
    mtime = os.path.getmtime(str(p))
    now = time.time()
    age_secs = now - mtime
    if age_secs > 86400:
        try:
            p.unlink()
        except OSError:
            pass
        sys.exit(0)
except (OSError, ValueError):
    sys.exit(0)

# If pointer exists and is fresh, read content.
try:
    raw = p.read_text(encoding="utf-8").strip()
except OSError:
    sys.exit(0)

# If empty, emit nothing.
if not raw:
    sys.exit(0)

# Parse scope marker from first line.
lines = raw.split('\n')
target = None
MARKER_PREFIX = '<!-- burnless-seed-target: '
MARKER_SUFFIX = ' -->'
if lines and lines[0].startswith(MARKER_PREFIX) and lines[0].endswith(MARKER_SUFFIX):
    target = lines[0][len(MARKER_PREFIX):-len(MARKER_SUFFIX)].strip()
    content = '\n'.join(lines[1:]).strip()
else:
    content = raw

# Scope check: if marker present and cwd doesn't match, leave for correct project.
cwd = payload.get('cwd', '')
if target is not None:
    if cwd != target and not cwd.startswith(target):
        sys.exit(0)

# If content empty after stripping marker, emit nothing.
if not content:
    sys.exit(0)

# Emit seed JSON with prefix, capped at ~4000 chars.
try:
    seed_msg = "[BURNLESS SEED] sessao iniciada leve a partir da capsule rolante.\n\n"
    final_content = seed_msg + content

    # Cap at ~4000 chars.
    if len(final_content) > 4000:
        final_content = final_content[:3950] + "\n…[truncated]"

    output = json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": final_content,
        }
    }, ensure_ascii=False)
    print(output)
    # Consume-once: delete pointer so subsequent sessions don't re-inject.
    try:
        p.unlink()
    except OSError:
        pass
except Exception:
    sys.exit(0)
PY
