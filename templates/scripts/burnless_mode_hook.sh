#!/usr/bin/env bash
# Burnless Claude Code UserPromptSubmit hook.
# Modes:
#   off     = no-op
#   observe = Burnless measures/explains; no behavior constraints on the assistant
#   on      = delegate-only Maestro injection + policy
# Legacy aliases (one release, coerced, then deprecated):
#   partner  -> observe   (legacy alias)
#   rollover -> on        (legacy alias)
set -uo pipefail

INPUT="$(cat)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
STATE_DIR="$HOME/.burnless/state"
BB="$(command -v burnless || echo "$HOME/.local/bin/burnless")"
mkdir -p "$STATE_DIR"

python_hook() {
  INPUT_JSON="$INPUT" "$PYTHON_BIN" - "$STATE_DIR" <<'PY'
import json
import os
import sys
import time
from pathlib import Path

# canonical modes + legacy alias coercion (deprecated; not persisted as legacy)
CANON = {"off", "observe", "on"}
LEGACY = {"partner": "observe", "rollover": "on"}  # legacy alias coercion (deprecated)
PROJECT_MODE_TTL_SECONDS = 24 * 60 * 60
SESSION_MODE_GC_SECONDS = 7 * 24 * 60 * 60


def canon(value):
    v = (value or "").strip()
    return LEGACY.get(v, v if v in CANON else "off")


def slugify(value):
    text = str(value or "").strip().lower()
    out = []
    for ch in text:
        if ch.isalnum() or ch in {"-", "_"}:
            out.append(ch)
        else:
            out.append("-")
    slug = "".join(out).strip("-")
    return slug or "project"


def emit(context):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": context,
        }
    }, ensure_ascii=False))


def log_turn_start(session_id, process_instance_id, cwd):
    run_id = os.environ.get("BURNLESS_PILOT_RUN_ID")
    if not run_id:
        return
    try:
        import subprocess
        payload = {
            "session_id": session_id,
            "process_instance_id": process_instance_id,
            "cwd": cwd,
            "source": "prompt",
        }
        root = str(Path(str(cwd)).resolve())
        subprocess.run(
            [
                os.environ.get("BB") or "burnless",
                "pilot-event",
                "--root",
                root,
                "--run-id",
                run_id,
                "--event",
                "turn_start",
                "--host",
                "claude",
                "--source",
                "prompt",
                "--cwd",
                cwd,
            ],
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            capture_output=True,
            check=False,
        )
    except Exception:
        pass


try:
    state_dir = Path(sys.argv[1])
    payload = json.loads(os.environ["INPUT_JSON"])
    sid = str(payload.get("session_id") or "").strip()
    prompt = str(payload.get("prompt") or "").strip()
    project_source = (
        payload.get("cwd")
        or os.environ.get("PWD")
        or os.environ.get("BURNLESS_WORKSPACE")
        or Path.cwd()
    )
    project_slug = slugify(Path(str(project_source)).name)
    mode_file = state_dir / f"session-{sid}.mode" if sid else None
    project_mode_file = state_dir / f"last-{project_slug}.mode"

    def gc_orphans():
        cutoff = time.time() - SESSION_MODE_GC_SECONDS
        for path in state_dir.glob("session-*.mode"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
            except OSError:
                pass

    def read_raw_mode():
        if mode_file and mode_file.exists():
            try:
                return mode_file.read_text(encoding="utf-8", errors="replace").strip() or "off"
            except OSError:
                return "off"
        if project_mode_file.exists():
            try:
                if time.time() - project_mode_file.stat().st_mtime > PROJECT_MODE_TTL_SECONDS:
                    project_mode_file.unlink(missing_ok=True)
                    return "off"
                return project_mode_file.read_text(encoding="utf-8", errors="replace").strip() or "off"
            except OSError:
                return "off"
        return "off"

    def write_mode(value):
        gc_orphans()
        if mode_file:
            try:
                mode_file.write_text(value, encoding="utf-8")
            except OSError:
                pass
        try:
            project_mode_file.write_text(value, encoding="utf-8")
        except OSError:
            pass

    # /burnless command handler
    if prompt and prompt.lstrip().startswith(("/burnless", "__BURNLESS_MODE_CMD__")):
        raw = prompt.split(None, 1)
        arg = raw[1].strip() if len(raw) > 1 else ""
        arg = arg.replace("__BURNLESS_MODE_CMD__", "").strip()
        chosen = "".join(ch for ch in arg.lower() if ch.isalpha())
        if chosen in {"menu", "models"}:
            import subprocess
            try:
                _out = subprocess.run(["burnless", "menu"], capture_output=True, text=True, timeout=10).stdout
            except Exception as _e:
                _out = f"(burnless menu unavailable: {_e})"
            emit("Burnless config (show this table verbatim to the user, do nothing else):\n\n" + _out)
            raise SystemExit(0)
        if chosen in CANON:
            write_mode(chosen)
            emit(f"Burnless mode -> {chosen} (next turn). Confirm to the user, do nothing else.")
            raise SystemExit(0)
        if chosen in LEGACY:
            canonical = LEGACY[chosen]
            write_mode(canonical)
            emit(
                f"Burnless: '{chosen}' is a legacy alias (deprecated) and now maps to "
                f"'{canonical}'. Set to {canonical} (next turn). Confirm to the user, do nothing else."
            )
            raise SystemExit(0)
        # no/unknown arg -> menu
        current = canon(read_raw_mode())
        emit(
            f"Burnless mode: {current}\n\n"
            "/burnless on       delegate-only + rolling memory + retrieval hints\n"
            "/burnless observe  measure and explain, no behavior constraints\n"
            "/burnless off      raw chat\n"
            "/burnless menu     tier/provider table\n"
            "/burnless status   session HUD"
        )
        raise SystemExit(0)

    log_turn_start(sid, sid, str(project_source))

    if os.environ.get("BURNLESS_OFF") == "1":
        raise SystemExit(0)

    # runtime dispatch — migrate any legacy persisted value to canonical on read
    raw_mode = read_raw_mode()
    mode = canon(raw_mode)
    if mode_file and raw_mode.strip() and raw_mode.strip() != mode:
        write_mode(mode)

    if mode == "off":
        raise SystemExit(0)
    if mode == "observe":
        emit(
            "[BURNLESS OBSERVE] Burnless is measuring this session and would record decisions "
            "and would-have-injected context, but does not constrain you. Work normally; note "
            "delegation and verification opportunities where relevant."
        )
        raise SystemExit(0)
    if mode == "on":
        emit(
            "[BURNLESS ON] You are the Maestro. Compress intent and ONLY delegate via "
            "burnless do/delegate (--tier bronze|silver|gold) with a tight spec + a ## Verify block. "
            "Do not write code or edit disk yourself. Read only the compact capsule (burnless read dXXX), "
            "never the raw log. Answer from the capsule, briefly."
        )
        raise SystemExit(0)
    raise SystemExit(0)
except Exception:
    sys.exit(0)
PY
}

BB="$BB" python_hook
