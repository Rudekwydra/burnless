#!/bin/bash
stdin_data=$(cat)
SID=$(echo "$stdin_data" | /usr/bin/jq -r '.session_id // empty' 2>/dev/null)
CWD=$(echo "$stdin_data" | /usr/bin/jq -r '.cwd // empty' 2>/dev/null)
[[ -z "$SID" || -z "$CWD" ]] && exit 0
export PATH="$HOME/.local/bin:$PATH"
export BURNLESS_EPOCH_V2=1
BB="$(command -v burnless || echo "$HOME/.local/bin/burnless")"
CHAIN=$("$BB" epoch resume --cwd "$CWD" --chat-id "$SID" --workspace "$HOME/antigravity" 2>/dev/null)
[[ -z "$CHAIN" ]] && exit 0
/usr/bin/jq -n --arg c "$CHAIN" '{hookSpecificOutput:{hookEventName:"SessionStart",additionalContext:("## Rolling memory (carry-forward)\n\n"+$c)}}'
exit 0
