#!/bin/bash
[[ -n "$BURNLESS_NO_EPOCH" ]] && exit 0
export PATH="$HOME/.local/bin:$PATH"
BB="$(command -v burnless || echo "$HOME/.local/bin/burnless")"
stdin_data=$(cat)
SID=$(echo "$stdin_data" | /usr/bin/jq -r '.session_id // empty' 2>/dev/null)
CWD=$(echo "$stdin_data" | /usr/bin/jq -r '.cwd // empty' 2>/dev/null)
TP=$(echo "$stdin_data" | /usr/bin/jq -r '.transcript_path // empty' 2>/dev/null)
[[ -z "$SID" || -z "$CWD" || -z "$TP" ]] && exit 0
ROOT=$("$BB" epoch resolve-root --cwd "$CWD" --workspace "$HOME/antigravity" --transcript "$TP" 2>/dev/null)
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
case "$extracted" in *"Resuma o trecho de conversa abaixo"*) exit 0 ;; esac
[[ -z "$extracted" ]] && exit 0
mkdir -p "$ROOT/.burnless/epochs/_rolling"
{
  tmp="$ROOT/.burnless/epochs/_rolling/.seed.md.tmp.$$"
  echo "$extracted" | "$BB" epoch capture --chat-id "$SID" --root "$ROOT" --emit-chain > "$tmp" 2>/dev/null
  if [[ -s "$tmp" ]]; then mv -f "$tmp" "$ROOT/.burnless/epochs/_rolling/seed.md"; else rm -f "$tmp"; fi
  BURNLESS_EPOCH_V2=1 "$BB" epoch refine-owner --chat-id "$SID" --root "$ROOT" >/dev/null 2>&1
} &
exit 0
