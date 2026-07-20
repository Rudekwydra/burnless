#!/bin/bash
[[ -n "$BURNLESS_NO_EPOCH" ]] && exit 0
export PATH="$HOME/.local/bin:$PATH"
BB="$(command -v burnless || echo "$HOME/.local/bin/burnless")"
stdin_data=$(cat)
PYTHON_BIN="${PYTHON_BIN:-python3}"
WORKSPACE_ROOT="${BURNLESS_WORKSPACE_ROOT:-${BURNLESS_WORKSPACE:-$HOME/antigravity}}"
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
log_hook_error() {
  local label="$1" message="$2"
  [[ -z "$message" ]] && return 0
  printf '%s' "$message" | "$BB" epoch hook-error --root "$ROOT" --hook "$label" --host claude --host-session-id "$SID" --process-instance-id "$PID" --source clear --transcript "$TP" >/dev/null 2>&1 || true
}
log_pilot_event() {
  [[ -z "$BURNLESS_PILOT_RUN_ID" ]] && return 0
  printf '%s' "$stdin_data" | "$BB" pilot-event --root "$ROOT" --run-id "$BURNLESS_PILOT_RUN_ID" --event session_end --host claude --host-session-id "$SID" --process-instance-id "$PID" --source clear --cwd "$CWD" --transcript "$TP" >/dev/null 2>&1 || true
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
ROOT_ERR=$(mktemp)
ROOT=$("$BB" epoch resolve-root --cwd "$CWD" --workspace "$WORKSPACE_ROOT" --transcript "$TP" --orphan-fallback 2>"$ROOT_ERR")
if [[ -z "$ROOT" ]]; then
  log_hook_error "resolve-root" "$(cat "$ROOT_ERR" 2>/dev/null)"
  echo "[burnless] rolling-memory OFF for this session: no project root for cwd=$CWD" >&2
  rm -f "$ROOT_ERR"
  exit 0
fi
rm -f "$ROOT_ERR"
[[ -f "$ROOT/.burnless/epochs.off" ]] && exit 0
EXTRACT_ERR=$(mktemp)
EXTRACTED=$("$BB" epoch extract-exchange --transcript "$TP" --host claude --host-session-id "$SID" --process-instance-id "$PID" --cwd "$CWD" --source clear 2>"$EXTRACT_ERR")
if [[ -z "$EXTRACTED" ]]; then
  log_hook_error "extract-exchange" "$(cat "$EXTRACT_ERR" 2>/dev/null)"
  rm -f "$EXTRACT_ERR"
  exit 0
fi
rm -f "$EXTRACT_ERR"
JOURNAL_ERR=$(mktemp)
RECORD=$(printf '%s' "$EXTRACTED" | "$BB" epoch journal-append --root "$ROOT" 2>"$JOURNAL_ERR")
if [[ -z "$RECORD" ]]; then
  log_hook_error "journal-append" "$(cat "$JOURNAL_ERR" 2>/dev/null)"
  rm -f "$JOURNAL_ERR"
  exit 0
fi
rm -f "$JOURNAL_ERR"
Handoff_ERR=$(mktemp)
printf '%s' "$RECORD" | "$BB" epoch handoff-write --root "$ROOT" --host claude --host-session-id "$SID" --process-instance-id "$PID" 2>"$Handoff_ERR" >/dev/null
if [[ $? -ne 0 ]]; then
  log_hook_error "handoff-write" "$(cat "$Handoff_ERR" 2>/dev/null)"
fi
rm -f "$Handoff_ERR"
log_pilot_event
{
  printf '%s' "$RECORD" | "$BB" epoch compact-pending --root "$ROOT" --host claude --host-session-id "$SID" --process-instance-id "$PID" --source clear >/dev/null 2>&1
  mkdir -p "$HOME/.burnless/state"
  "$BB" epoch export --root "$ROOT" --host claude --host-session-id "$SID" >/dev/null 2>>"$HOME/.burnless/state/epoch_export.log"
} </dev/null >/dev/null 2>&1 &
disown 2>/dev/null || true
exit 0
