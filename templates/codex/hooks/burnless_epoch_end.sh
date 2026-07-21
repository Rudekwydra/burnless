#!/bin/bash
# Codex SessionEnd-equivalent hook (checkpoint/end path). NOT wired into
# hooks.json this wave (no confirmed Codex "SessionEnd"-class event to bind
# to yet) — kept as a sibling script mirroring the Claude end-of-session
# flow, ready to wire once such an event is confirmed.
[[ -n "$BURNLESS_NO_EPOCH" ]] && exit 0
export PATH="$HOME/.local/bin:$PATH"
BB="$(command -v burnless || echo "$HOME/.local/bin/burnless")"
PYTHON_BIN="${PYTHON_BIN:-python3}"
WORKSPACE_ROOT="${BURNLESS_WORKSPACE_ROOT:-${BURNLESS_WORKSPACE:-$HOME/antigravity}}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./codex_payload.sh
source "$SCRIPT_DIR/codex_payload.sh"

codex_read_stdin
IFS=$'\t' read -r SID CWD <<<"$(codex_resolve_sid_cwd)"
PID=$(codex_host_pid)
[[ -z "$PID" ]] && PID="$SID"

if ! codex_validate_sid "$SID" "$CWD" "$BB"; then
  codex_dump_payload
  exit 0
fi

ROOT=$("$BB" epoch resolve-root --cwd "$CWD" --workspace "$WORKSPACE_ROOT" --orphan-fallback 2>/dev/null)
[[ -z "$ROOT" ]] && exit 0
[[ -f "$ROOT/.burnless/epochs.off" ]] && exit 0

EXTRACTED=$("$BB" epoch extract-exchange --host codex --host-session-id "$SID" --process-instance-id "$PID" --cwd "$CWD" --source clear 2>/dev/null)
[[ -z "$EXTRACTED" ]] && exit 0

RECORD=$(printf '%s' "$EXTRACTED" | "$BB" epoch journal-append --root "$ROOT" 2>/dev/null)
[[ -z "$RECORD" ]] && exit 0

printf '%s' "$RECORD" | "$BB" epoch handoff-write --root "$ROOT" --host codex --host-session-id "$SID" --process-instance-id "$PID" >/dev/null 2>&1

{
  printf '%s' "$RECORD" | "$BB" epoch compact-pending --root "$ROOT" --host codex --host-session-id "$SID" --process-instance-id "$PID" --source clear >/dev/null 2>&1
  mkdir -p "$HOME/.burnless/state"
  "$BB" epoch export --root "$ROOT" --host codex --host-session-id "$SID" >/dev/null 2>>"$HOME/.burnless/state/epoch_export.log"
} </dev/null >/dev/null 2>&1 &
disown 2>/dev/null || true
exit 0
