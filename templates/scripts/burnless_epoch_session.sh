#!/bin/bash

export PATH="$HOME/.local/bin:$PATH"
BURNLESS_BIN="$(command -v burnless || echo "$HOME/.local/bin/burnless")"

stdin_data=$(cat)
SID=$(echo "$stdin_data" | /usr/bin/jq -r '.session_id // empty' 2>/dev/null)
CWD=$(echo "$stdin_data" | /usr/bin/jq -r '.cwd // empty' 2>/dev/null)

[[ -z "$SID" || -z "$CWD" ]] && exit 0

ROOT=""
current="$CWD"
while [[ -n "$current" && "$current" != "/" ]]; do
  if [[ -f "$current/.burnless/config.yaml" ]]; then
    ROOT="$current"
    break
  fi
  current=$(dirname "$current")
done

[[ -z "$ROOT" ]] && exit 0
[[ ! -f "$ROOT/.burnless/epochs.on" ]] && exit 0

CHAIN=$("$BURNLESS_BIN" epoch read --chat-id "$SID" --root "$ROOT" 2>/dev/null)

if [[ -z "$CHAIN" && -f "$ROOT/.burnless/epochs/_rolling/seed.md" ]]; then
  CHAIN=$(cat "$ROOT/.burnless/epochs/_rolling/seed.md")
fi

[[ -z "$CHAIN" ]] && exit 0

/usr/bin/jq -n --arg c "$CHAIN" '{hookSpecificOutput:{hookEventName:"SessionStart",additionalContext:("## Rolling memory (carry-forward)\n\n"+$c)}}'

exit 0
