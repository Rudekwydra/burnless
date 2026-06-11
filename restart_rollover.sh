#!/usr/bin/env bash
# Respawn FRESH com promoção de capsule rolante -> pending_seed.md
# Roda ISTO NO TEU PRÓPRIO TERMINAL interativo (onde o claude atual está).
# O `exec claude` substitui esta sessão por uma fresca que lê o settings.json
# repointado e habilita o modo rollover. NÃO funciona via worker/detached (sem TTY).
#
# Nota: respawn é FRESH (compactação real, dropa o tail).
# Fork (continuidade quente) é outra feature e deliberadamente NÃO usado aqui.

set -uo pipefail

TARGET_DIR="$HOME/antigravity/burnless"
STATE_DIR="/Users/roberto/.burnless/state"
PROJ_DIR="/Users/roberto/.claude/projects/-Users-roberto-antigravity-burnless"

# Check hook
if grep -q "burnless_mode_hook.sh" "$HOME/.claude/settings.json" 2>/dev/null; then
  echo "==> OK: rollover hook fiado no settings.json"
else
  echo "==> AVISO: hook nao fiado — rode 'burnless init --claude-code' antes"
fi

# Promoção de capsule rolante -> pending_seed.md
if [[ -d "$PROJ_DIR" ]]; then
  LATEST_JSONL=$(ls -t "$PROJ_DIR"/*.jsonl 2>/dev/null | head -1)
  if [[ -n "$LATEST_JSONL" ]]; then
    OLDSID=$(basename "$LATEST_JSONL" .jsonl)
    CAPSULE="$STATE_DIR/session-$OLDSID.rollover.md"

    if [[ -s "$CAPSULE" ]]; then
      # Criar STATE_DIR se não existir (fail-open)
      mkdir -p "$STATE_DIR" || true
      # Promover capsule pra pending_seed.md com mtime fresca
      cp "$CAPSULE" "$STATE_DIR/pending_seed.md" || true
      touch "$STATE_DIR/pending_seed.md" || true
      echo "==> seed promovido de $OLDSID"
    else
      echo "==> AVISO: sem capsule rolante pra $OLDSID — respawn sem seed (fail-open)"
    fi
  fi
fi

# DRY-RUN support (testável sem exec claude)
if [[ "${BURNLESS_ROLLOVER_DRYRUN:-0}" == "1" ]]; then
  echo "==> DRYRUN: would exec claude"
  exit 0
fi

echo "==> no chat novo rode:  /burnless rollover"
echo "==> subindo claude fresco em $TARGET_DIR ..."
cd "$TARGET_DIR"
# Path A: fork preserves cache warmth (~82k cache_read).
exec claude --resume $OLDSID --fork-session
