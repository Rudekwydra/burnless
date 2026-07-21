# Meant to be `source`d by the Codex event scripts (burnless_epoch_*.sh) in
# this directory, not executed directly. Provides the shared, defensive
# stdin-payload parsing + sid validation used by every Codex hook, since the
# real Codex hook payload schema is UNVERIFIED (no official-doc access, no
# local evidence of SessionStart's shape at authoring time) — see README.md.

# codex_read_stdin(): reads stdin ONCE into $stdin_data (same variable name
# the Claude scripts use, so callers don't need new plumbing).
codex_read_stdin() {
  stdin_data=$(cat)
}

# codex_resolve_sid_cwd(): given $stdin_data and $PYTHON_BIN already set,
# tries candidate key names for session id and cwd (decision 1 in the G1
# spec) and prints "SID<TAB>CWD" for the caller to split. This is pure
# candidate-picking — it does NOT validate the sid against the resolve_path
# oracle; that happens in bash after, via codex_validate_sid().
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

SID_KEYS = ("session_id", "id", "thread_id", "conversation_id", "rollout_id")
CWD_KEYS = ("cwd", "workspace", "working_directory", "workspace_root")

sid = ""
for key in SID_KEYS:
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        sid = value.strip()
        break

cwd = ""
for key in CWD_KEYS:
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        cwd = value.strip()
        break

print(f"{sid}\t{cwd}")
PY
)
  local sid cwd
  IFS=$'\t' read -r sid cwd <<<"$out"
  # $PWD fallback (shell env, not JSON) when no candidate cwd field matched.
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

# codex_dump_payload(): raw-payload capture for manual schema discovery.
# Only called when no candidate sid/cwd validates. Stays on by default this
# wave (greenfield, zero cost) — see README.md for removal plan.
codex_dump_payload() {
  local dump_dir="$HOME/.burnless/codex_hook_payloads"
  mkdir -p "$dump_dir"
  printf '%s' "${stdin_data:0:4000}" >"$dump_dir/$(date +%s).json"
}

# codex_host_pid(): stable lineage id — nearest codex ancestor pid. Same
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
