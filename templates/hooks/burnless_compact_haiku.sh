#!/usr/bin/env bash
# Burnless layer-1 semantic compactor — Claude Code UserPromptSubmit hook.
#
# Reads hook input JSON from stdin, extracts user prompt, calls Haiku via
# `claude -p` to compact into a JSON telegram envelope {i, r, m}, returns
# Claude Code hook output JSON with additionalContext.
#
# Fail-open: any error in compaction → return empty additionalContext (no-op).
# Timeout: 4 seconds total.

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

# --- compact via Haiku one-shot ---
COMPACT_PROMPT="Você é compactador telegrafo. Reescreva o input do user em JSON puro com chaves: i (intent verbo imperativo), r (refs: paths/IDs/nomes), m (markers: URG|DEC|HYPE|PERS se aplicável, senão omita). MÁX 30 tokens. Output JSON apenas, sem prosa, sem markdown fence.

[USER INPUT]
$USER_PROMPT"

TELEGRAM="$(printf '%s' "$COMPACT_PROMPT" | timeout 4 /opt/homebrew/bin/claude -p \
  --model claude-haiku-4-5-20251001 \
  --permission-mode bypassPermissions \
  --allowedTools '' \
  --output-format json 2>/dev/null \
  | python3 -c "import sys, json
try:
    d = json.load(sys.stdin)
    result = (d.get('result') or '').strip()
    # Strip possible markdown fences
    if result.startswith('\`\`\`'):
        lines = result.split('\n')
        result = '\n'.join(lines[1:-1] if lines[-1].startswith('\`\`\`') else lines[1:])
    # Validate JSON
    json.loads(result)
    print(result)
except Exception:
    print('')
" 2>/dev/null)"

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
