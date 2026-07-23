#!/bin/bash
# Codex SessionStart hook. Payload fields follow the official hook schema.
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

# resolve-root only inspects $CWD, not $SID, so it cannot serve as a sid
# oracle (unlike the reasoning sketched for this in the spec) — run the
# explicit sid-specific validation first so a garbage sid never reaches
# `epoch restore --new-session-id`.
if ! codex_validate_sid "$SID" "$CWD" "$BB" "$TRANSCRIPT_PATH"; then
  codex_dump_payload
  exit 0
fi

ROOT=$("$BB" epoch resolve-root --cwd "$CWD" --workspace "$WORKSPACE_ROOT" --orphan-fallback "${TRANSCRIPT_ARGS[@]}" 2>/dev/null)
[[ -z "$ROOT" ]] && exit 0
[[ -f "$ROOT/.burnless/epochs.off" ]] && exit 0

# Codex has no confirmed field equivalent to Claude's `source == "clear"`
# (fresh session vs. compaction/resume are not distinguishable from what's
# been observed so far). Known simplification for this wave: always attempt
# a restore on SessionStart.
RESTORE=$("$BB" epoch restore --root "$ROOT" --host codex --process-instance-id "$PID" --new-session-id "$SID" --source clear "${TRANSCRIPT_ARGS[@]}" 2>/dev/null)
[[ -z "$RESTORE" ]] && exit 0
printf '%s\n' "$RESTORE"
exit 0
