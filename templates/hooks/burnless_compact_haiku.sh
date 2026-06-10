#!/usr/bin/env bash
# Burnless layer-1 semantic compactor — Claude Code UserPromptSubmit hook.
#
# Reads hook input JSON from stdin, extracts user prompt, delegates compaction
# to burnless.telegram_compact (provider-aware: ollama-local / anthropic).
# Returns Claude Code hook output JSON with additionalContext.
#
# Fail-open: any error in compaction → return empty additionalContext (no-op).
# Timeout: 10 seconds total.

set -u
set -o pipefail

# --- read hook input from stdin (Claude Code passes JSON) ---
HOOK_INPUT="$(cat 2>/dev/null || true)"
USER_PROMPT="$(printf '%s' "$HOOK_INPUT" | python3 -c "import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('user_prompt', '') or d.get('prompt', '') or '')
except Exception:
    print('')
" 2>/dev/null)"

if [ -z "$USER_PROMPT" ]; then
  # No prompt to compact — no-op
  echo '{}'
  exit 0
fi

# --- guard: only compact if user opted in via env or marker file ---
if [ ! -f "$HOME/.burnless/compactor_enabled" ] && [ "${BURNLESS_COMPACTOR:-0}" != "1" ]; then
  echo '{}'
  exit 0
fi

# --- guard: skip very short prompts (compaction overhead > gain) ---
if [ "${#USER_PROMPT}" -lt 40 ]; then
  echo '{}'
  exit 0
fi

# --- compact via provider-aware Python module ---
TELEGRAM="$(printf '%s' "$USER_PROMPT" | timeout 10 python3 -m burnless.telegram_compact 2>/dev/null)"

if [ -z "$TELEGRAM" ]; then
  # Compaction failed — no-op (fail-open)
  echo '{}'
  exit 0
fi

# --- emit Claude Code hook output ---
python3 -c "
import json, sys
telegram = '''$TELEGRAM'''
out = {
    'hookSpecificOutput': {
        'hookEventName': 'UserPromptSubmit',
        'additionalContext': f'[BURNLESS TELEGRAM (haiku-compacted user intent)]\n{telegram}\n\nUse o telegram acima para entender a intenção. Original do user permanece visível abaixo para clarificações.'
    }
}
print(json.dumps(out))
"
exit 0
