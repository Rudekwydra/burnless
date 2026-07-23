# Meant to be `source`d by the Codex event scripts (burnless_epoch_*.sh) in
# this directory, not executed directly. Provides parsing for Codex's
# documented common stdin fields (`session_id`, `cwd`) plus sid validation.

# codex_read_stdin(): reads stdin ONCE into $stdin_data (same variable name
# the Claude scripts use, so callers don't need new plumbing).
codex_read_stdin() {
  stdin_data=$(cat)
}

# codex_resolve_sid_cwd(): parse the documented `session_id` and `cwd` fields
# and print "SID<TAB>CWD" for callers to split. Sid validation remains a
# separate step via codex_validate_sid().
codex_resolve_sid_cwd() {
  local out
  out=$(INPUT_JSON="$stdin_data" "${PYTHON_BIN:-python3}" - <<'PY'
import json
import os

payload_raw = os.environ.get("INPUT_JSON", "{}")
try:
    payload = json.loads(payload_raw)
except Exception:
    payload = {}
if not isinstance(payload, dict):
    payload = {}

sid_value = payload.get("session_id")
sid = sid_value.strip() if isinstance(sid_value, str) else ""
cwd_value = payload.get("cwd")
cwd = cwd_value.strip() if isinstance(cwd_value, str) else ""

print(f"{sid}\t{cwd}")
PY
)
  local sid cwd
  IFS=$'\t' read -r sid cwd <<<"$out"
  # Defensive fallback for malformed payloads; command hooks normally receive cwd.
  [[ -z "$cwd" ]] && cwd="$PWD"
  printf '%s\t%s\n' "$sid" "$cwd"
}

# codex_validate_sid(): given $SID $CWD $BB, confirms the candidate sid is a
# real Codex session by asking `burnless epoch extract-exchange --host codex`
# to resolve it. This CLI-oracle call is preferred over a raw
# `python3 -c "import burnless"` because import reliability from a bare
# `python3 -c` outside the installed package's env is unconfirmed; shelling
# through the CLI entrypoint the hook already depends on (`$BB`) avoids that
# question entirely and reuses infrastructure already proven end-to-end by
# G2 (resolve_path is called internally by extract-exchange). A non-empty,
# non-error result IS proof the transcript path resolved.
codex_validate_sid() {
  local sid="$1" cwd="$2" bb="$3"
  [[ -z "$sid" ]] && return 1
  local result
  result=$("$bb" epoch extract-exchange --host codex --host-session-id "$sid" --cwd "$cwd" 2>/dev/null)
  [[ -z "$result" ]] && return 1
  return 0
}

# codex_dump_payload(): retained as a safe no-op compatibility shim. The schema
# is now documented, so hooks no longer persist raw payloads on validation
# failure.
codex_dump_payload() {
  return 0
}

# Codex's stdin schema has no PID field. codex_host_pid(): stable lineage id
# from the nearest codex ancestor pid. Same
# ancestor-walk as the Claude scripts' host_pid(), matching `codex*` instead
# of `claude*|node*` in the process name (this function has nothing
# host-specific in its walk logic, only in the comm match).
codex_host_pid() {
  local pid=$$ i comm
  for i in 1 2 3 4 5 6 7 8; do
    pid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d '[:space:]')
    [[ -z "$pid" || "$pid" == "0" || "$pid" == "1" ]] && break
    comm=$(ps -o comm= -p "$pid" 2>/dev/null)
    case "${comm##*/}" in
      codex*) printf 'host-%s' "$pid"; return 0;;
    esac
  done
  return 1
}
