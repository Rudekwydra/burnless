#!/usr/bin/env bash
# Respawn FRESH com promoção de capsule rolante -> pending_seed.md
# Roda ISTO NO TEU PRÓPRIO TERMINAL interativo (onde o claude atual está).
# O `exec claude` substitui esta sessão por uma fresca que lê o settings.json
# repointado e habilita o modo rollover. NÃO funciona via worker/detached (sem TTY).
#
# Nota: respawn é FRESH (compactação real, dropa o tail).
# Opt-in: BURNLESS_RESPAWN_WARM_SID=<sid> para fork de warm-seed controlado.

set -uo pipefail

TARGET_DIR="$HOME/antigravity/burnless"
STATE_DIR="$HOME/.burnless/state"
PROJECT_DIR="$HOME/.claude/projects/$(printf '%s' "$TARGET_DIR" | sed 's#/#-#g')"

latest_transcript() {
  if [[ -d "$PROJECT_DIR" ]]; then
    python3 - "$PROJECT_DIR" <<'PY'
import sys
from pathlib import Path

project_dir = Path(sys.argv[1])
latest = None
latest_mtime = -1.0
for path in project_dir.glob("*.jsonl"):
    try:
        mtime = path.stat().st_mtime
    except OSError:
        continue
    if mtime >= latest_mtime:
        latest_mtime = mtime
        latest = path

if latest is not None:
    print(latest)
PY
  fi
}

# Check hook
if grep -q "burnless_mode_hook.sh" "$HOME/.claude/settings.json" 2>/dev/null; then
  echo "==> OK: rollover hook fiado no settings.json"
else
  echo "==> AVISO: hook nao fiado — rode 'burnless init --claude-code' antes"
fi

LATEST_JSONL="$(latest_transcript)"
OLDSID=""
if [[ -n "$LATEST_JSONL" ]]; then
  OLDSID="$(basename "$LATEST_JSONL" .jsonl)"
  CAPSULE="$STATE_DIR/session-$OLDSID.rollover.md"

  if [[ -s "$CAPSULE" ]]; then
    # Criar STATE_DIR se não existir (fail-open)
    mkdir -p "$STATE_DIR" || true
    # Promover capsule pra pending_seed.md com mtime fresca
    { printf '<!-- burnless-seed-target: %s -->\n' "$TARGET_DIR"; cat "$CAPSULE"; } > "$STATE_DIR/pending_seed.md" || true
    touch "$STATE_DIR/pending_seed.md" || true
    echo "==> seed promovido de $OLDSID"
  else
    echo "==> AVISO: sem capsule rolante pra $OLDSID — respawn sem seed (fail-open)"
  fi
fi

# DRY-RUN support (testável sem exec claude)
if [[ "${BURNLESS_ROLLOVER_DRYRUN:-0}" == "1" ]]; then
  echo "==> DRYRUN: would exec claude"
  exit 0
fi

if [[ -z "$OLDSID" ]]; then
  echo "==> ERRO: não achei transcript .jsonl em $PROJECT_DIR"
  exit 1
fi

echo "==> no chat novo rode:  /burnless rollover"
echo "==> subindo claude fresco em $TARGET_DIR ..."
cd "$TARGET_DIR"
# Fresh respawn: a brand-new `claude` process reuses the identical ~37k system
# prefix from cache automatically within the 1h TTL (measured 2026-06-12:
# cache_read 37131 / cache_creation 0 across separate fresh processes). The
# compacted rolling memory returns via the SessionStart seed hook reading
# pending_seed.md (promoted above). We deliberately do NOT --resume the old
# session, which would drag its full tail back into context and break the
# rolling-memory compaction.
#
# Opt-in controlled-warm layer ("ambos"): if BURNLESS_RESPAWN_WARM_SID is set,
# fork that small burnless-owned warm session instead (keepalive-managed,
# monitored). Default path is fresh.
if [[ -n "${BURNLESS_RESPAWN_WARM_SID:-}" ]]; then
  echo "==> respawn: fork da warm-seed $BURNLESS_RESPAWN_WARM_SID (controlado)"
  exec claude --resume "$BURNLESS_RESPAWN_WARM_SID" --fork-session
else
  echo "==> respawn: claude FRESCO (prefixo quente automatico ~37k cache_read)"
  exec claude
fi
