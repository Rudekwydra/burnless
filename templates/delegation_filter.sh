#!/usr/bin/env bash
# Tier escalation policy: gold / silver / bronze.
# Blocks MUTATING Bash commands in the main loop so they get delegated to a worker.
# Read-only remote and privileged commands pass through: reading output is
# interpretation work, and bouncing it through a worker just costs a round trip.
# Sub-agents are detected via transcript_path and always allowed.
set -euo pipefail

INPUT=$(cat)
# --- burnless mode shim: an EXPLICIT off disables the guard (fail-closed) ---
__BL_SID=$(echo "$INPUT" | jq -r '.session_id // ""')
__BL_MODE=""
if [[ -n "$__BL_SID" && -f "$HOME/.burnless/state/session-$__BL_SID.mode" ]]; then
  __BL_MODE=$(tr -d '[:space:]' < "$HOME/.burnless/state/session-$__BL_SID.mode" 2>/dev/null || true)
fi
if [[ "${BURNLESS_OFF:-0}" == "1" || "$__BL_MODE" == "off" ]]; then
  exit 0
fi
# --- end shim ---
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // ""')
TRANSCRIPT=$(echo "$INPUT" | jq -r '.transcript_path // ""')

LOG=~/.claude/logs/delegation_filter.log
mkdir -p ~/.claude/logs

log_decision() {
  local decision="$1"
  local reason="$2"
  local snippet="${COMMAND:0:200}"
  snippet="${snippet//$'\n'/ }"
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $decision $reason :: $snippet" >> "$LOG"
}

# Sub-agent detected (transcript lives at subagents/agent-XXX.jsonl) -> allow
if [[ "$TRANSCRIPT" == *"/subagents/"* ]]; then
  log_decision ALLOW subagent
  exit 0
fi

# Trim leading whitespace, then look for the # OURO_OK override on line 1
TRIMMED="${COMMAND#"${COMMAND%%[![:space:]]*}"}"
if [[ "$TRIMMED" == "# OURO_OK"* ]]; then
  log_decision ALLOW ouro_ok
  exit 0
fi

# ============ MUTATION KEYWORDS ============
# Strings that mean the command changes state. If one shows up (directly in the
# shell or inside a remote command), the command belongs to a worker.
MUTATION_RE='(^|[[:space:]\;\&\|\`\$\(])(rm[[:space:]]+-|mv[[:space:]]|sed[[:space:]]+-i|systemctl[[:space:]]+(reload|restart|start|stop|enable|disable)|service[[:space:]]+[^[:space:]]+[[:space:]]+(restart|reload|start|stop)|chmod[[:space:]]|chown[[:space:]]|dd[[:space:]]+if|mkfs|tee[[:space:]]+/|>[[:space:]]*/etc|>>[[:space:]]*/etc|>[[:space:]]*/var|>>[[:space:]]*/var|apt(-get)?[[:space:]]+install|apt(-get)?[[:space:]]+remove|yum[[:space:]]+install|dnf[[:space:]]+install|snap[[:space:]]+install|docker[[:space:]]+(compose[[:space:]]+up|run|rm|kill|stop|start)|git[[:space:]]+push|git[[:space:]]+reset[[:space:]]+--hard|git[[:space:]]+clean|npm[[:space:]]+install|pip[[:space:]]+install|make[[:space:]]+install|crontab[[:space:]]+-e|wp[[:space:]]+(plugin|theme|core)[[:space:]]+(install|activate|deactivate|delete|update))'

# ============ HEAVY-TRANSFER COMMANDS ============
# Always a worker's job: scp/rsync/lftp (bulk transfer) and remote login with no
# inline command (an interactive session)
HEAVY=0
if [[ "$COMMAND" =~ (^|[[:space:]\;\&\|])scp[[:space:]] ]]; then HEAVY=1; fi
if [[ "$COMMAND" =~ (^|[[:space:]\;\&\|])rsync[[:space:]] ]]; then HEAVY=1; fi
if [[ "$COMMAND" =~ (^|[[:space:]\;\&\|])lftp[[:space:]] ]]; then HEAVY=1; fi

# ============ SSH ANALYSIS ============
# remote + read-only inline command -> allow
# remote with no inline command (interactive session) -> block
# remote + mutating inline command -> block
SSH_BLOCK=0
if [[ "$COMMAND" =~ (^|[[:space:]\;\&\|])ssh[[:space:]]+ ]]; then
  # Everything after the first user@host
  SSH_INNER=$(echo "$COMMAND" | grep -oE 'ssh[[:space:]]+([^@[:space:]]+@[^[:space:]]+|-[a-zA-Z]+[[:space:]]+[^[:space:]]+([[:space:]]+-[a-zA-Z]+[[:space:]]+[^[:space:]]+)*[[:space:]]+[^@[:space:]]+@[^[:space:]]+).*$' || true)
  AFTER_HOST=$(echo "$SSH_INNER" | sed -E 's/^ssh[[:space:]]+(-[^@]*[[:space:]]+)?[^@[:space:]]+@[^[:space:]]+[[:space:]]*//')
  if [[ -z "$AFTER_HOST" ]]; then
    # interactive login, no command -> block
    SSH_BLOCK=1
  elif [[ "$AFTER_HOST" =~ $MUTATION_RE ]]; then
    # remote command mutates state -> block
    SSH_BLOCK=1
  fi
  # read-only remote command falls through, allowed
fi

# ============ SUDO ANALYSIS ============
# sudo systemctl is-active|status|cat -> allow (read-only)
# sudo + any mutation keyword -> block
SUDO_BLOCK=0
if [[ "$COMMAND" =~ (^|[[:space:]\;\&\|])sudo[[:space:]]+ ]]; then
  if [[ "$COMMAND" =~ $MUTATION_RE ]]; then
    SUDO_BLOCK=1
  fi
  # sudo cat/ls and friends are read-only: allowed by omission
fi

# ============ DECISION ============
if [[ $HEAVY -eq 1 || $SSH_BLOCK -eq 1 || $SUDO_BLOCK -eq 1 ]]; then
  log_decision BLOCK "heavy=$HEAVY ssh=$SSH_BLOCK sudo=$SUDO_BLOCK"
  cat >&2 <<EOF
🚦 TIER ESCALATION POLICY: this is worker work. Blocked in the main loop.

Command: ${COMMAND:0:120}...

Why it was blocked:
  heavy transfer (scp/rsync/lftp) = $HEAVY
  remote, mutating or interactive = $SSH_BLOCK
  sudo, mutating                  = $SUDO_BLOCK

Allowed without delegating (read-only):
  - ssh host 'ls|cat|grep|find|tail|nginx -t|curl|systemctl is-active|...'
  - sudo cat/ls/nginx -t/systemctl status
  - nginx -t, curl, ping on their own

Delegate with burnless do. A subagent pulls its whole output back into your
context; a worker returns a capsule instead:
  burnless do --tier bronze "closed spec"        mechanical, reads, ops
  burnless do --tier silver "spec + acceptance"  deploy, build, remote mutations
  burnless do --tier gold   "decision/design"    architecture, irreversible

Override (rare): make the first line of the command "# OURO_OK <reason>"
Log: ~/.claude/logs/delegation_filter.log
EOF
  exit 2
fi

log_decision ALLOW clean
exit 0
