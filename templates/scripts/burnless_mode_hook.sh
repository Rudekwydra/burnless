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
        if chosen in {"menu", "models"}:
            import subprocess
            try:
                _out = subprocess.run(["burnless", "menu"], capture_output=True, text=True, timeout=10).stdout
            except Exception as _e:
                _out = f"(burnless menu unavailable: {_e})"
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": "Burnless config (show this table verbatim to the user, do nothing else):\n\n" + _out,
                }
            }, ensure_ascii=False))
            raise SystemExit(0)
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
                "additionalContext": f"Show the Burnless mode menu: /burnless on|partner|rollover|off|menu. Current: {current}.",
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
        turns = int(meta.get("turns", 0))

        light_path = state_dir / f"session-{sid}.rollover.md" if sid else None
        meta_path  = state_dir / f"session-{sid}.rollover.json" if sid else None
        seed_path  = state_dir / f"session-{sid}.seed.md" if sid else None

        # REWIND DETECTION
        prev_max = 0
        if meta_path and meta_path.exists():
            try:
                prev_meta = json.loads(meta_path.read_text(encoding="utf-8", errors="replace"))
                prev_max = int(prev_meta.get("max_turns_seen", 0))
            except Exception:
                prev_max = 0
        rewound = turns < prev_max

        new_max = turns if rewound else max(prev_max, turns)

        if sid:
            try:
                light_path.write_text(capsule, encoding="utf-8")
                meta_out = dict(meta)
                meta_out["max_turns_seen"] = new_max
                meta_out["rewound"] = rewound
                meta_path.write_text(json.dumps(meta_out, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError:
                pass

        def _gemma_compact(text: str) -> str:
            try:
                import urllib.request as _ur
                req = _ur.Request("http://localhost:11434/api/tags")
                with _ur.urlopen(req, timeout=2) as resp:
                    tags = json.loads(resp.read())
                model_name = ""
                for m in tags.get("models", []):
                    if "gemma" in m.get("name", "").lower():
                        model_name = m["name"]
                        break
                if not model_name:
                    return ""
                SYS = (
                    "You are a lossless semantic compressor. Compress the input into a dense capsule "
                    "preserving ALL decisions, tasks, open questions, file paths, and next steps. "
                    "Output ONLY the capsule, no preamble."
                )
                body = json.dumps({"model": model_name, "prompt": SYS + "\n\nINPUT:\n" + text, "stream": False}).encode()
                req2 = _ur.Request(
                    "http://localhost:11434/api/generate",
                    data=body,
                    headers={"Content-Type": "application/json"},
                )
                with _ur.urlopen(req2, timeout=20) as resp2:
                    result = json.loads(resp2.read())
                return result.get("response", "").strip()
            except Exception:
                return ""

        # ROTATION SNAPSHOT (durable seed) — only at rotation point, never on rewind
        if not rewound and turns > 0 and turns % limit == 0 and sid:
            snapshot = _gemma_compact(capsule) or capsule
            try:
                seed_path.write_text(snapshot, encoding="utf-8")
            except OSError:
                pass
            try:
                (state_dir / "rotation_due").write_text(
                    f"{sid} {meta['cycle']} {turns}\n", encoding="utf-8"
                )
            except OSError:
                pass

        # BUILD INJECTED CONTEXT
        context = ""
        if seed_path and seed_path.exists():
            try:
                seed_text = seed_path.read_text(encoding="utf-8", errors="replace")
                context += (
                    "[BURNLESS ROLLING MEMORY — durable seed, survives /rewind]\n"
                    "Use this as the authoritative working state:\n\n"
                    + seed_text + "\n\n"
                )
            except OSError:
                pass
        context += (
            f"[BURNLESS ROLLOVER] cycle={meta['cycle']} window={meta['limit']} turns={turns}\n"
            f"Prefer the seed above; the rolling focus below is supplementary.\n\n"
            f"{capsule}"
        )
        if turns > 0 and turns % limit == 0 and not rewound:
            context += (
                f"\n\n\U0001f504 [BURNLESS ROLL] turn {turns}: contexto cheio. "
                f"Dê /rewind pro início da sessão — esta seed re-injeta sozinha no próximo turno "
                f"e o cache do prefixo fica quente. "
                f"(compactação semântica via gemma; nada de /compact ou /clear nativo.)"
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
