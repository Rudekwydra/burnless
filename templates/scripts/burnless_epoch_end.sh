#!/bin/bash
[[ -n "$BURNLESS_NO_EPOCH" ]] && exit 0
export PATH="$HOME/.local/bin:$PATH"
BB="$(command -v burnless || echo "$HOME/.local/bin/burnless")"
stdin_data=$(cat)
PYTHON_BIN="${PYTHON_BIN:-python3}"
WORKSPACE_ROOT="${BURNLESS_WORKSPACE_ROOT:-${BURNLESS_WORKSPACE:-$HOME/antigravity/burnless}}"
json_field() {
  INPUT_JSON="$stdin_data" "$PYTHON_BIN" - "$1" <<'PY'
import json
import os
import sys

payload = json.loads(os.environ.get("INPUT_JSON", "{}"))
field = sys.argv[1]
if field == "session_id":
    print(payload.get("session_id") or "")
elif field == "cwd":
    print(payload.get("cwd") or "")
elif field == "transcript_path":
    print(payload.get("transcript_path") or "")
elif field == "process_instance_id":
    print(payload.get("process_instance_id") or "")
elif field == "source":
    # SessionEnd payloads carry `reason` ("clear" | "logout" | ...); accept both.
    print(payload.get("source") or payload.get("reason") or "")
PY
}
# Stable lineage id: nearest claude/node ancestor pid. Survives /clear (same
# host process, new session id) and distinguishes concurrent windows.
host_pid() {
  local pid=$$ i comm
  for i in 1 2 3 4 5 6 7 8; do
    pid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d '[:space:]')
    [[ -z "$pid" || "$pid" == "0" || "$pid" == "1" ]] && break
    comm=$(ps -o comm= -p "$pid" 2>/dev/null)
    case "${comm##*/}" in
      claude*|node*) printf 'host-%s' "$pid"; return 0;;
    esac
  done
  return 1
}
SID=$(json_field session_id)
CWD=$(json_field cwd)
TP=$(json_field transcript_path)
PID=$(json_field process_instance_id)
[[ -z "$PID" ]] && PID=$(host_pid)
[[ -z "$PID" ]] && PID="$SID"
SOURCE=$(json_field source)
[[ "$SOURCE" != "clear" ]] && exit 0
[[ -z "$SID" || -z "$CWD" || -z "$TP" ]] && exit 0
ROOT=$("$BB" epoch resolve-root --cwd "$CWD" --workspace "$WORKSPACE_ROOT" --transcript "$TP" 2>/dev/null)
[[ -z "$ROOT" ]] && exit 0
[[ -f "$ROOT/.burnless/epochs.off" ]] && exit 0
EXTRACTED=$("$BB" epoch extract-exchange --transcript "$TP" --host claude --host-session-id "$SID" --process-instance-id "$PID" --cwd "$CWD" --source clear 2>/dev/null)
[[ -z "$EXTRACTED" ]] && exit 0
RECORD=$(printf '%s' "$EXTRACTED" | "$BB" epoch journal-append --root "$ROOT" 2>/dev/null)
[[ -z "$RECORD" ]] && exit 0
printf '%s' "$RECORD" | "$BB" epoch handoff-write --root "$ROOT" --host claude --host-session-id "$SID" --process-instance-id "$PID" >/dev/null 2>&1
{
  printf '%s' "$RECORD" | "$BB" epoch compact-pending --root "$ROOT" --host claude --host-session-id "$SID" --process-instance-id "$PID" >/dev/null 2>&1
} &
exit 0
