#!/bin/bash
# Codex Stop hook — the guaranteed/primary anchor event this wave.
[[ -n "$BURNLESS_NO_EPOCH" ]] && exit 0
export PATH="$HOME/.local/bin:$PATH"
BB="$(command -v burnless || echo "$HOME/.local/bin/burnless")"
PYTHON_BIN="${PYTHON_BIN:-python3}"
WORKSPACE_ROOT="${BURNLESS_WORKSPACE_ROOT:-${BURNLESS_WORKSPACE:-$HOME/antigravity}}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./codex_payload.sh
source "$SCRIPT_DIR/codex_payload.sh"

codex_read_stdin
IFS=$'\x1f' read -r SID CWD TRANSCRIPT_PATH <<<"$(codex_resolve_sid_cwd)"
TRANSCRIPT_ARGS=()
[[ -n "$TRANSCRIPT_PATH" ]] && TRANSCRIPT_ARGS=(--transcript "$TRANSCRIPT_PATH")
PID=$(codex_host_pid)
[[ -z "$PID" ]] && PID="$SID"

if ! codex_validate_sid "$SID" "$CWD" "$BB" "$TRANSCRIPT_PATH"; then
  codex_dump_payload
  exit 0
fi

ROOT=$("$BB" epoch resolve-root --cwd "$CWD" --workspace "$WORKSPACE_ROOT" --orphan-fallback "${TRANSCRIPT_ARGS[@]}" 2>/dev/null)
[[ -z "$ROOT" ]] && exit 0
[[ -f "$ROOT/.burnless/epochs.off" ]] && exit 0

# When Codex omits transcript_path, the empty argument array preserves the
# existing transcript_sources.resolve_path fallback.
EXTRACTED=$("$BB" epoch extract-exchange --host codex --host-session-id "$SID" --process-instance-id "$PID" --cwd "$CWD" --source stop "${TRANSCRIPT_ARGS[@]}" 2>/dev/null)
[[ -z "$EXTRACTED" ]] && exit 0

mkdir -p "$ROOT/.burnless/epochs/_rolling"
RECORD=$(printf '%s' "$EXTRACTED" | "$BB" epoch journal-append --root "$ROOT" "${TRANSCRIPT_ARGS[@]}" 2>/dev/null)
[[ -z "$RECORD" ]] && exit 0

{
  printf '%s' "$RECORD" | "$BB" epoch compact-pending --root "$ROOT" --host codex --host-session-id "$SID" --process-instance-id "$PID" --source stop "${TRANSCRIPT_ARGS[@]}" >/dev/null 2>&1
} </dev/null >/dev/null 2>&1 &
disown 2>/dev/null || true
exit 0
