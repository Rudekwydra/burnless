#!/usr/bin/env bash
# Burnless Claude Code UserPromptSubmit hook.
# Modes:
#   off      = no-op
#   partner  = no-op; assistant keeps reasoning, Burnless stays as execution boundary
#   on       = delegate-only Maestro injection
#   rollover = rolling capsule injection from transcript_path (experimental)
set -uo pipefail

INPUT="$(cat)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
STATE_DIR="$HOME/.burnless/state"
mkdir -p "$STATE_DIR"

emit() {
  "$PYTHON_BIN" - "$1" <<'PY'
import json, sys
context = sys.argv[1]
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": context,
    }
}, ensure_ascii=False))
PY
}

python_hook() {
  INPUT_JSON="$INPUT" "$PYTHON_BIN" - "$STATE_DIR" <<'PY'
import json
import os
import sys
from pathlib import Path

try:
    state_dir = Path(sys.argv[1])
    payload = json.loads(os.environ["INPUT_JSON"])
    session_id = str(payload.get("session_id") or "").strip()
    prompt = str(payload.get("prompt") or "").strip()
    transcript_path = str(payload.get("transcript_path") or "").strip()
    mode_file = state_dir / f"session-{session_id}.mode" if session_id else None
    mode = "off"
    if mode_file and mode_file.exists():
        try:
            mode = mode_file.read_text(encoding="utf-8", errors="replace").strip() or "off"
        except OSError:
            mode = "off"

    def write_mode(value: str) -> None:
        if mode_file:
            mode_file.write_text(value, encoding="utf-8")

    def extract_text(node) -> str:
        if isinstance(node, str):
            return node.strip()
        if isinstance(node, list):
            parts = []
            for item in node:
                if isinstance(item, dict):
                    txt = item.get("text")
                    if isinstance(txt, str) and txt.strip():
                        parts.append(txt.strip())
                elif isinstance(item, str) and item.strip():
                    parts.append(item.strip())
            return "\n".join(parts).strip()
        if isinstance(node, dict):
            content = node.get("content")
            if content is not None:
                return extract_text(content)
        return ""

    def build_capsule(limit: int) -> tuple[str, dict]:
        entries = []
        if transcript_path:
            tp = Path(transcript_path)
            if tp.exists():
                try:
                    for raw in tp.read_text(encoding="utf-8", errors="replace").splitlines():
                        raw = raw.strip()
                        if not raw:
                            continue
                        try:
                            rec = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        kind = rec.get("type")
                        msg = rec.get("message") or {}
                        if kind not in {"user", "assistant"}:
                            continue
                        text = extract_text(msg)
                        if text:
                            entries.append({"type": kind, "text": text})
                except OSError:
                    pass
        if prompt and not (entries and entries[-1]["type"] == "user" and entries[-1]["text"] == prompt):
            entries.append({"type": "user", "text": prompt})

        user_count = sum(1 for item in entries if item["type"] == "user")
        cycle = max(1, ((max(user_count, 1) - 1) // max(limit, 1)) + 1)
        window = max(1, limit)
        tail = entries[-window * 2:]

        bullets = []
        if prompt:
            bullets.append(f"Pedido atual: {prompt[:280]}")

        recent_users = [item["text"] for item in entries if item["type"] == "user"][-window:]
        recent_assistant = [item["text"] for item in entries if item["type"] == "assistant"][-window:]

        if recent_users:
            bullets.append("Pedidos recentes:")
            for text in recent_users[-3:]:
                bullets.append(f"- {text[:220]}")

        if recent_assistant:
            bullets.append("Respostas recentes:")
            for text in recent_assistant[-3:]:
                bullets.append(f"- {text[:220]}")

        if tail:
            bullets.append("Trecho rolante:")
            for item in tail[-6:]:
                prefix = "U" if item["type"] == "user" else "A"
                bullets.append(f"{prefix}: {item['text'][:180]}")

        capsule = "\n".join(bullets).strip()
        if not capsule:
            capsule = "Pedido atual: sem texto útil."

        meta = {
            "cycle": cycle,
            "turns": user_count,
            "limit": limit,
            "transcript_path": transcript_path,
        }
        return capsule, meta

    payload = json.loads(os.environ["INPUT_JSON"])
    sid = str(payload.get("session_id") or "").strip()
    prompt = str(payload.get("prompt") or "").strip()
    transcript_path = str(payload.get("transcript_path") or "").strip()

    if prompt and prompt.lstrip().startswith(("/burnless", "__BURNLESS_MODE_CMD__")):
        raw = prompt.split(None, 1)
        arg = ""
        if len(raw) > 1:
            arg = raw[1].strip()
        arg = arg.replace("__BURNLESS_MODE_CMD__", "").strip()
        chosen = "".join(ch for ch in arg.lower() if ch.isalpha())
        if chosen in {"on", "partner", "off", "rollover"}:
            write_mode(chosen)
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": f"Burnless mode -> {chosen} (next turn). Confirm to the user, do nothing else.",
                }
            }, ensure_ascii=False))
            raise SystemExit(0)
        current = "off"
        if mode_file and mode_file.exists():
            try:
                current = mode_file.read_text(encoding="utf-8", errors="replace").strip() or "off"
            except OSError:
                current = "off"
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": f"Show the Burnless mode menu: /burnless on|partner|rollover|off. Current: {current}.",
            }
        }, ensure_ascii=False))
        raise SystemExit(0)

    if os.environ.get("BURNLESS_OFF") == "1":
        raise SystemExit(0)

    mode = "off"
    if mode_file and mode_file.exists():
        try:
            mode = mode_file.read_text(encoding="utf-8", errors="replace").strip() or "off"
        except OSError:
            mode = "off"
    if mode == "off":
        raise SystemExit(0)
    if mode == "partner":
        raise SystemExit(0)
    if mode == "on":
        context = (
            "[BURNLESS ON] You are the Maestro. Compress intent and ONLY delegate via "
            "burnless do/delegate (--tier bronze|silver|gold) with a tight spec + a ## Verify block. "
            "Do not write code or edit disk yourself. Read only the compact capsule (burnless read dXXX), "
            "never the raw log. Answer from the capsule, briefly."
        )
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": context,
            }
        }, ensure_ascii=False))
        raise SystemExit(0)
    if mode == "rollover":
        try:
            limit = int(os.environ.get("BURNLESS_ROLLOVER_TURNS", "10"))
        except ValueError:
            limit = 10
        capsule, meta = build_capsule(limit)
        capsule_path = state_dir / f"session-{sid}.rollover.md"
        meta_path = state_dir / f"session-{sid}.rollover.json"
        if sid:
            try:
                capsule_path.write_text(capsule, encoding="utf-8")
                meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError:
                pass

        # Rotation-point detection: check if we hit the threshold.
        turns = meta.get("turns", 0)
        rotation_marker = state_dir / f"session-{sid}.restart_due"
        rotation_msg = ""
        if turns > 0 and (turns % limit) == 0:
            # Write marker file.
            if sid:
                try:
                    rotation_marker.write_text(str(turns), encoding="utf-8")
                except OSError:
                    pass
            rotation_msg = f"\n\n[BURNLESS ROTATION] turn {turns}: rotation point. To actually reset context, open a fresh session (bash ~/antigravity/burnless/restart_rollover.sh) — auto-respawn not yet wired."
        else:
            # Remove marker if present and not at rotation point.
            try:
                rotation_marker.unlink(missing_ok=True)
            except OSError:
                pass

        context = (
            f"[BURNLESS ROLLOVER] (experimental: rolling focus; does NOT reduce native context until the session-respawn consumer lands) cycle={meta['cycle']} window={meta['limit']} turns={meta['turns']}\n"
            f"Use the capsule below as the working state. Prefer it over older transcript details.\n\n"
            f"{capsule}"
            f"{rotation_msg}"
        )
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": context,
            }
        }, ensure_ascii=False))
        raise SystemExit(0)
    raise SystemExit(0)
except Exception:
    sys.exit(0)
PY
}

python_hook
