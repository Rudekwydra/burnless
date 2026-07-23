# Meant to be `source`d by the Codex event scripts (burnless_epoch_*.sh) in
# this directory, not executed directly. Provides parsing for Codex's
# documented common stdin fields (`session_id`, `cwd`, `transcript_path`) plus
# sid validation.

# codex_read_stdin(): reads stdin ONCE into $stdin_data (same variable name
# the Claude scripts use, so callers don't need new plumbing).
codex_read_stdin() {
  stdin_data=$(cat)
}

# codex_resolve_sid_cwd(): parse the documented `session_id`, `cwd`, and
# `transcript_path` fields and print them separated by ASCII unit separators
# for callers to split without collapsing an empty field. Sid validation
# remains a separate step via codex_validate_sid().
codex_resolve_sid_cwd() {
  INPUT_JSON="$stdin_data" "${PYTHON_BIN:-python3}" - <<'PY'
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
if not cwd:
    cwd = os.getcwd()
transcript_value = payload.get("transcript_path")
transcript = transcript_value.strip() if isinstance(transcript_value, str) else ""

print("\x1f".join((sid, cwd, transcript)))
PY
}

# codex_validate_sid(): given $SID $CWD $BB and optional $TRANSCRIPT_PATH,
# confirms the candidate sid is a real Codex session by asking `burnless epoch
# extract-exchange --host codex` to resolve it. This CLI-oracle call is
# preferred over a raw
# `python3 -c "import burnless"` because import reliability from a bare
# `python3 -c` outside the installed package's env is unconfirmed; shelling
# through the CLI entrypoint the hook already depends on (`$BB`) avoids that
# question entirely and reuses infrastructure already proven end-to-end by
# G2 (resolve_path is called internally by extract-exchange). A non-empty,
# non-error result IS proof the transcript path resolved.
codex_validate_sid() {
  local sid="$1" cwd="$2" bb="$3" transcript="${4:-}"
  [[ -z "$sid" ]] && return 1
  local transcript_args=()
  [[ -n "$transcript" ]] && transcript_args=(--transcript "$transcript")
  local result
  result=$("$bb" epoch extract-exchange --host codex --host-session-id "$sid" --cwd "$cwd" "${transcript_args[@]}" 2>/dev/null)
  [[ -z "$result" ]] && return 1
  return 0
}

# codex_dump_payload(): persist the raw hook payload only when a caller's sid
# validation fails, so silent capture failures leave an actionable diagnostic.
codex_dump_payload() {
  local dump_dir="$HOME/.burnless/codex_hook_payloads"
  local dump_path payload
  payload="${stdin_data:-}"
  [[ -z "$payload" ]] && payload='{}'
  mkdir -p "$dump_dir" 2>/dev/null || return 0
  dump_path="$dump_dir/payload-$(date -u +%Y%m%dT%H%M%SZ)-$$.json"
  (umask 077; printf '%s\n' "$payload" >"$dump_path") 2>/dev/null || true
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
