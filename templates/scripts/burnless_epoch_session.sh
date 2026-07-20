#!/bin/bash
# Burnless restore (rolling memory carry-forward)
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
elif field == "process_instance_id":
    print(payload.get("process_instance_id") or "")
elif field == "transcript_path":
    print(payload.get("transcript_path") or "")
elif field == "source":
    print(payload.get("source") or "")
PY
}
log_hook_error() {
  local label="$1" message="$2"
  [[ -z "$message" ]] && return 0
  printf '%s' "$message" | "$BB" epoch hook-error --root "$ROOT" --hook "$label" --host claude --host-session-id "$SID" --process-instance-id "$PID" --source clear --transcript "$TP" >/dev/null 2>&1 || true
}
log_pilot_event() {
  [[ -z "$BURNLESS_PILOT_RUN_ID" ]] && return 0
  printf '%s' "$stdin_data" | "$BB" pilot-event --root "$ROOT" --run-id "$BURNLESS_PILOT_RUN_ID" --event session_start --host claude --host-session-id "$SID" --process-instance-id "$PID" --source clear --cwd "$CWD" >/dev/null 2>&1 || true
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
[[ -z "$SID" || -z "$CWD" ]] && exit 0
export PATH="$HOME/.local/bin:$PATH"
BB="$(command -v burnless || echo "$HOME/.local/bin/burnless")"
ROOT_ERR=$(mktemp)
ROOT=$("$BB" epoch resolve-root --cwd "$CWD" --workspace "$WORKSPACE_ROOT" --orphan-fallback 2>"$ROOT_ERR")
if [[ -z "$ROOT" ]]; then
  log_hook_error "resolve-root" "$(cat "$ROOT_ERR" 2>/dev/null)"
  echo "[burnless] restore SKIPPED — cwd=$CWD resolves to no burnless project; cd into the project or run burnless init. Freshest handoff on disk may live elsewhere."
  rm -f "$ROOT_ERR"
  exit 0
fi
rm -f "$ROOT_ERR"
[[ -f "$ROOT/.burnless/epochs.off" ]] && exit 0
# Visibility banner (every session start, any source): when memory is bound to
# the ORPHAN store (cwd outside any burnless project), say so on turn 1 —
# never discover it only after a /clear.
case "$ROOT" in
  "$HOME/.burnless/orphans/"*)
    echo "[burnless] rolling-memory em MODO ORFAO para este cwd ($CWD): memoria vive em $ROOT (global, sobrevive a /clear normalmente). Para promover a memoria pro projeto, rode 'burnless init' neste diretorio."
    ;;
esac
[[ "$SOURCE" != "clear" ]] && exit 0
log_pilot_event
RESTORE_ERR=$(mktemp)
# Budget resolves from config (epochs.restore_budget_tokens, default 4000);
# pass --budget-tokens only when explicitly configured via env override.
BUDGET_ARGS=()
[[ -n "${BURNLESS_RESTORE_BUDGET_TOKENS:-}" ]] && BUDGET_ARGS=(--budget-tokens "$BURNLESS_RESTORE_BUDGET_TOKENS")
RESTORE=$("$BB" epoch restore --root "$ROOT" --host claude --process-instance-id "$PID" --new-session-id "$SID" --source clear --transcript "$TP" "${BUDGET_ARGS[@]}" 2>"$RESTORE_ERR")
if [[ -z "$RESTORE" ]]; then
  log_hook_error "restore" "$(cat "$RESTORE_ERR" 2>/dev/null)"
  rm -f "$RESTORE_ERR"
  exit 0
fi
rm -f "$RESTORE_ERR"
printf '%s\n' "$RESTORE"
exit 0
