#!/bin/bash

export PATH="$HOME/.local/bin:$PATH"
BURNLESS_BIN="$(command -v burnless || echo "$HOME/.local/bin/burnless")"

stdin_data=$(cat)
SID=$(echo "$stdin_data" | /usr/bin/jq -r '.session_id // empty' 2>/dev/null)
CWD=$(echo "$stdin_data" | /usr/bin/jq -r '.cwd // empty' 2>/dev/null)
TP=$(echo "$stdin_data" | /usr/bin/jq -r '.transcript_path // empty' 2>/dev/null)

[[ -z "$SID" || -z "$CWD" || -z "$TP" ]] && exit 0

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
[[ -f "$ROOT/.burnless/epochs.off" ]] && exit 0

extracted=$(python3 -c '
import sys, json
u = ""
a = ""
try:
  for ln in open(sys.argv[1]):
    try:
      o = json.loads(ln)
      m = o.get("message", {})
      r = m.get("role")
      c = m.get("content")
      if isinstance(c, list):
        t = "".join(b.get("text", "") for b in c if b.get("type") == "text")
      elif isinstance(c, str):
        t = c
      else:
        t = ""
      if not t.strip():
        continue
      if r == "user":
        u = t
      elif r == "assistant":
        a = t
    except:
      pass
  if u or a:
    print("PERGUNTA:\n" + u + "\n\nRESPOSTA:\n" + a)
except:
  pass
' "$TP" 2>/dev/null)

if [[ -n "$extracted" ]]; then
  mkdir -p "$ROOT/.burnless/epochs/_rolling"
  {
    # Single guarded, non-destructive write (Layer A + B): capture emits the
    # active chain to stdout with --emit-chain; promote the temp seed only if it
    # is non-empty, so a summarizer failure preserves the last good seed.
    tmp="$ROOT/.burnless/epochs/_rolling/.seed.md.tmp.$$"
    echo "$extracted" | "$BURNLESS_BIN" epoch capture --chat-id "$SID" --root "$ROOT" --emit-chain > "$tmp" 2>/dev/null
    if [[ -s "$tmp" ]]; then mv -f "$tmp" "$ROOT/.burnless/epochs/_rolling/seed.md"; else rm -f "$tmp"; fi
  } &
fi

exit 0
