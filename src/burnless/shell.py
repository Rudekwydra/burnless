from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
import threading
import time as _time
from pathlib import Path

try:
    from prompt_toolkit import prompt as _pt_prompt
    from prompt_toolkit.formatted_text import ANSI
    _HAS_PROMPT_TOOLKIT = True
except ImportError:
    _HAS_PROMPT_TOOLKIT = False

from . import TAGLINE, __version__
from . import chat_history
from . import compression as compression_mod
from . import config as config_mod
from . import dashboard
from . import delegations as deleg_mod
from . import metrics as metrics_mod
from . import natural_planner
from . import paths as paths_mod
from . import routing as routing_mod
from . import state as state_mod
from . import cli as cli_mod


HELP = """\
Commands:
  /help
  /status               project + headline metric
  /metrics              counters and estimated cost avoided
  /plan <text>          set the project plan (compact state)
  /delegate <text>      create a numbered delegation
  /run d002             execute the delegation
  /read d002            print the compact summary
  /log d002             print the raw log
  /capsule d002         show or regenerate the operational capsule
  /compression safe|balanced|aggressive
  /voice on|off         mirror user's tone in replies (default on, ~5% extra tokens)
  /agents               list configured agents
  /setup                detect CLIs and write a sensible config
  /import               index your existing AI memories (folders)
  /chat                 enter persistent chat mode
  /use gold|silver|bronze|auto    sticky tier for next runs
  :gold | :silver | :bronze | :auto    same, shorter
  /clear                clear screen
  /exit                 leave the shell

Natural language works too:
  fix d002
  continue
  ver status
  mostrar métricas
  abrir capsule d002
"""


def main() -> int:
    root = paths_mod.find_root()
    if root is None:
        print("No Burnless project found here.")
        answer = input("Initialize one? [Y/n] ").strip().lower()
        if answer in {"n", "no", "nao", "não"}:
            return 1
        cli_mod.cmd_init(argparse.Namespace(project=None, force=False))
        root = paths_mod.require_root()

    p = paths_mod.paths_for(root)
    chat_history.ensure(p["history"])
    _clear_screen()
    _print_banner(p)

    while True:
        try:
            text = _read_input(_prompt(p)).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not text:
            continue
        done = handle_input(text, p)
        if done:
            return 0


_pt_session: "PromptSession | None" = None  # type: ignore[name-defined]


def _get_pt_session() -> "PromptSession":  # type: ignore[name-defined]
    global _pt_session
    if _pt_session is not None:
        return _pt_session
    from prompt_toolkit import PromptSession

    paste_len: list[int] = [0]

    def bottom_toolbar():
        n = paste_len[0]
        return f" [paste {n:,} chars — press Enter to send]" if n > 80 else ""

    _pt_session = PromptSession(bottom_toolbar=bottom_toolbar)

    def _on_text_changed(_):
        paste_len[0] = len(_pt_session.default_buffer.text)  # type: ignore[union-attr]

    _pt_session.default_buffer.on_text_changed += _on_text_changed
    return _pt_session


def _prompt(p: dict[str, Path]) -> str:
    tier = _state(p).get("active_tier")
    label = tier or "auto"
    # orange fire color for the prefix, dim brackets, bold ›
    return f"\033[33mburnless\033[0m \033[2m[{label}]\033[0m \033[33m›\033[0m "


def _read_input(prompt_str: str) -> str:
    """Read one line of input with bracketed-paste support.

    prompt_toolkit buffers pasted text and only submits on a real Enter —
    no mid-paste line-by-line processing. Large pastes show a char count
    in the status bar, mirroring Claude Code's paste indicator. Falls back
    to plain input() when not a tty or prompt_toolkit is unavailable.
    """
    if not _HAS_PROMPT_TOOLKIT or not sys.stdout.isatty():
        return input(prompt_str)
    return _get_pt_session().prompt(ANSI(prompt_str))


def handle_input(text: str, p: dict[str, Path]) -> bool:
    from . import intents

    intent = intents.parse(text)
    if intent.kind == "exit":
        chat_history.append(p["history"], user=text, burnless="Session closed.")
        return True
    if intent.kind == "help":
        return _respond(p, text, HELP)
    if intent.kind == "status":
        return _respond(p, text, dashboard.render_status(_state(p), _metrics(p)))
    if intent.kind == "metrics":
        cfg = _config(p)
        return _respond(
            p,
            text,
            dashboard.render_metrics(
                _metrics(p),
                show_cost=bool(cfg.get("metrics", {}).get("show_estimated_cost", True)),
            ),
        )
    if intent.kind == "agents":
        return _respond(p, text, _render_agents(p))
    if intent.kind == "setup":
        rc = cli_mod.cmd_setup(
            argparse.Namespace(non_interactive=False, yes=False, project=None)
        ) or 0
        chat_history.append(
            p["history"], user=text,
            burnless=("Setup wizard finished." if rc == 0 else "Setup wizard aborted."),
        )
        return False
    if intent.kind == "clear":
        _clear_screen()
        _print_banner(p)
        return False
    if intent.kind == "use_tier":
        return _set_active_tier(p, text, intent.args[0])
    if intent.kind == "chat":
        from . import chat_mode
        chat_mode.run_chat(p)
        _clear_screen()
        _print_banner(p)
        chat_history.append(p["history"], user=text, burnless="Chat session closed.")
        return False
    if intent.kind == "import":
        return _import_memories(p, text)
    if intent.kind == "plan":
        return _respond(p, text, "Write a plan after the command, for example:\n/plan validate compression in a real workflow")
    if intent.kind == "plan_text":
        out, _ = _capture(cli_mod.cmd_plan, argparse.Namespace(text=intent.args[0]))
        return _respond(p, text, out.strip())
    if intent.kind == "delegate":
        return _respond(p, text, "Write an objective after the command, for example:\n/delegate fix the failing benchmark")
    if intent.kind == "objective":
        return _new_objective(p, text, intent.args[0])
    if intent.kind == "run_last":
        last = _state(p).get("last_delegation")
        if not last:
            return _respond(p, text, "No delegation has been created yet.")
        return _run(p, text, last)
    if intent.kind == "run":
        return _run(p, text, intent.args[0])
    if intent.kind == "read":
        out, rc = _capture(cli_mod.cmd_read, argparse.Namespace(id=intent.args[0]))
        return _respond(p, text, out.strip(), error=rc != 0)
    if intent.kind == "log":
        out, rc = _capture(cli_mod.cmd_log, argparse.Namespace(id=intent.args[0]))
        return _respond(p, text, out.strip(), error=rc != 0)
    if intent.kind == "capsule":
        out, rc = _capture(cli_mod.cmd_capsule, argparse.Namespace(id=intent.args[0], mode=None))
        return _respond(p, text, out.strip(), error=rc != 0)
    if intent.kind == "compression":
        return _set_compression(p, text, intent.args[0])
    if intent.kind == "voice":
        return _set_voice_match(p, text, bool(intent.args[0]))
    if intent.kind == "fix":
        return _fix(p, text, intent.args[0])
    if intent.kind == "continue":
        return _continue(p, text)
    return _new_objective(p, text, text)


def _clear_screen() -> None:
    if not sys.stdout.isatty():
        return
    if os.name == "nt":
        os.system("cls")
    else:
        sys.stdout.write("\x1b[2J\x1b[H")
        sys.stdout.flush()


def _print_banner(p: dict[str, Path]) -> None:
    state = _state(p)
    cfg = _config(p)
    m = _metrics(p)
    root = p["root"].parent
    comp_cfg = cfg.get("compression", {})
    compression = comp_cfg.get("mode", compression_mod.DEFAULT_MODE)
    voice_match = comp_cfg.get("voice_match", True)
    tier = state.get("active_tier") or "auto"
    project = state.get("project") or root.name
    burnless_tokens = int(m.get("burnless_tokens", 0))
    delegations = int(state.get("delegation_counter", 0) or 0)

    voice_tag = "voice:on" if voice_match else "voice:off"
    print(f"\033[33m🔥 Burnless v{__version__}\033[0m   tier: {tier}   compression: {compression}   \033[2m{voice_tag}\033[0m")
    print(f"{project} · {_display_path(root)}")
    print(f"{burnless_tokens:,} burnless tokens · {delegations} delegations · /help")
    if voice_match:
        print("\033[2m  tip: replies mirror your tone (~5% extra tokens). `/voice off` for robotic prose.\033[0m")
    else:
        print("\033[2m  tip: voice-match off — replies are pragmatic. `/voice on` to mirror your tone.\033[0m")
    print()


def _new_objective(p: dict[str, Path], user_text: str, objective: str) -> bool:
    cfg = _config(p)
    planned = natural_planner.plan_objective(objective, project_root=p["root"].parent)
    task = planned.task
    tier, matched = routing_mod.route(task, cfg["routing"])
    agent = cfg["agents"][tier]["name"]
    did = _create_delegation(p, task, goal=planned.original, tier=None)
    prefix = f"\033[2m→ {did} · {tier}/{agent}\033[0m"
    return _run(p, user_text, did, prefix=prefix)


def _fix(p: dict[str, Path], user_text: str, did: str) -> bool:
    log_path = p["logs"] / f"{did}.log"
    if not log_path.exists():
        return _respond(p, user_text, f"I could not find a log for {did}.", error=True)
    snippet = _tail(log_path.read_text(encoding="utf-8"), 2500)
    task = (
        f"Inspect .burnless/logs/{did}.log and fix the failure from delegation {did}. "
        "Patch the command, template, or code that caused the error, then validate the fix.\n\n"
        f"Relevant log tail:\n{snippet}"
    )
    new_id = _create_delegation(p, task, goal=f"Fix {did}", tier="silver")
    prefix = f"\033[2m→ {new_id} · fix {did}\033[0m"
    return _run(p, user_text, new_id, prefix=prefix)


def _continue(p: dict[str, Path], user_text: str) -> bool:
    state = _state(p)
    did = state.get("last_capsule") or state.get("last_delegation")
    capsule = {}
    if did:
        capsule_path = p["capsules"] / f"{did}.json"
        if capsule_path.exists():
            capsule = json.loads(capsule_path.read_text(encoding="utf-8"))
    next_step = state.get("next") or capsule.get("next") or "Inspect the last capsule and continue the useful next step."
    task = (
        f"Continue from the current Burnless state. Last delegation: {did or 'none'}. "
        f"Next useful step: {next_step}"
    )
    new_id = _create_delegation(p, task, goal="Continue from last capsule", tier="silver")
    prefix = f"\033[2m→ {new_id} · continue\033[0m"
    return _run(p, user_text, new_id, prefix=prefix)


def _run(p: dict[str, Path], user_text: str, did: str, *, prefix: str = "") -> bool:
    if prefix:
        print(prefix)

    # Ephemeral spinner: writes to the real fd-1 so it survives _capture's redirect.
    _real_out = sys.__stdout__
    _is_tty = _real_out is not None and hasattr(_real_out, "isatty") and _real_out.isatty()

    if not _is_tty:
        # Non-tty (tests, pipes, CI): print a static marker that _capture won't swallow.
        print("[investigando...]")
        _stop_ev: threading.Event | None = None
        _spinner_t: threading.Thread | None = None
    else:
        _stop_ev = threading.Event()
        _frames = ["|", "/", "-", "\\"]

        def _spin() -> None:
            i = 0
            while not _stop_ev.wait(0.1):  # type: ignore[union-attr]
                frame = _frames[i % len(_frames)]
                assert _real_out is not None
                _real_out.write(f"\r{frame} {did} investigando...")
                _real_out.flush()
                i += 1

        _spinner_t = threading.Thread(target=_spin, daemon=True)
        _spinner_t.start()

    try:
        _, rc = _capture(
            cli_mod.cmd_run,
            argparse.Namespace(
                id=did,
                dry_run=False,
                timeout=600,
                mode="plain",
                progress=None,
                maestro=False,
                no_maestro=False,
            ),
        )
    finally:
        if _stop_ev is not None:
            _stop_ev.set()
        if _spinner_t is not None:
            _spinner_t.join(timeout=0.5)
        if _is_tty and _real_out is not None:
            _real_out.write("\r\033[K")
            _real_out.flush()

    response = _friendly_run_result(p, did, rc)
    if prefix:
        response = f"{prefix}\n{response}"
    print(response)
    chat_history.append(p["history"], user=user_text, burnless=response)
    return False


def _friendly_run_result(p: dict[str, Path], did: str, rc: int) -> str:
    summary_path = p["temp"] / f"{did}.json"
    capsule_path = p["capsules"] / f"{did}.json"
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        kind = str(summary.get("kind") or summary.get("report_kind") or "execution").strip().lower()
        status = str(summary.get("status") or ("OK" if rc == 0 else "PART")).upper()
        text = (summary.get("summary") or "").strip()
        head = f"{'THOUGHT' if kind == 'thought' and status == 'OK' else status}:{did}"
        if text:
            head = f"{head}\n{text}"
        audit = summary.get("audit") if isinstance(summary.get("audit"), dict) else {}
        feedback = str(audit.get("feedback") or summary.get("next") or "").strip()
        if status != "OK" and feedback:
            head = f"{head}\nReason: {feedback[:180]}"
        return head
    if summary_path.exists() or capsule_path.exists():
        return f"PART:{did}\nNeeds follow-up."
    return f"ERR:{did}\nWorker failed before saving a summary."


def _create_delegation(p: dict[str, Path], task: str, *, goal: str, tier: str | None) -> str:
    if tier is None:
        sticky = _state(p).get("active_tier")
        if sticky:
            tier = sticky
    args = argparse.Namespace(
        text=task,
        goal=goal,
        success="task completed; final JSON block emitted as required.",
        tier=tier,
        chain=None,
        force=False,
    )
    _capture(cli_mod.cmd_delegate, args)
    return _state(p).get("last_delegation")


def _set_active_tier(p: dict[str, Path], user_text: str, tier: str) -> bool:
    state = _state(p)
    if tier == "auto":
        state["active_tier"] = None
        msg = "Tier set to auto (router will choose per task)."
    else:
        state["active_tier"] = tier
        agent = _config(p)["agents"][tier]["name"]
        msg = f"Tier sticky: {tier}/{agent}. Reset with `:auto`."
    state_mod.save(p["state"], state)
    return _respond(p, user_text, msg)


def _import_memories(p: dict[str, Path], user_text: str) -> bool:
    from . import setup_wizard
    det = setup_wizard.detect(scan_memory=True)
    if not det.memory_paths:
        return _respond(p, user_text, "No memory folders found in the usual places.")
    indexed = setup_wizard._index_memories(det.memory_paths, p)
    return _respond(
        p, user_text,
        f"Indexed {indexed} memory file(s) from {len(det.memory_paths)} location(s).\n"
        f"Index: {p['root'] / 'memories' / 'index.json'}",
    )


def _set_compression(p: dict[str, Path], user_text: str, mode: str) -> bool:
    cfg = _config(p)
    cfg.setdefault("compression", {})["mode"] = mode
    config_mod.save(p["config"], cfg)
    state = _state(p)
    state["compression"] = mode
    state_mod.save(p["state"], state)
    return _respond(p, user_text, f"Compression set to {mode}.\n\n{int(_metrics(p).get('burnless_tokens', 0)):,} burnless tokens")


def _set_voice_match(p: dict[str, Path], user_text: str, on: bool) -> bool:
    cfg = _config(p)
    cfg.setdefault("compression", {})["voice_match"] = on
    config_mod.save(p["config"], cfg)
    state_repr = "on" if on else "off"
    explain = (
        "Replies will mirror your tone (~5% extra tokens)."
        if on else "Replies will be pragmatic prose (no voice mirroring, ~5% cheaper)."
    )
    return _respond(p, user_text, f"voice-match {state_repr}. {explain}")


def _render_agents(p: dict[str, Path]) -> str:
    cfg = _config(p)
    lines = []
    for tier, agent in cfg.get("agents", {}).items():
        lines.append(f"{tier}/{agent.get('name')} - {agent.get('role')}")
    return "\n".join(lines)


def _respond(p: dict[str, Path], user_text: str, response: str, *, error: bool = False) -> bool:
    if error and not response:
        response = "Command failed."
    print(response)
    chat_history.append(p["history"], user=user_text, burnless=response)
    return False


def _record_only(p: dict[str, Path], user_text: str, response: str) -> bool:
    print(response)
    chat_history.append(p["history"], user=user_text, burnless=response)
    return False


def _capture(func, args: argparse.Namespace) -> tuple[str, int]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        try:
            rc = func(args) or 0
        except SystemExit as e:
            rc = int(e.code) if isinstance(e.code, int) else 1
            if not isinstance(e.code, int) and e.code:
                print(e.code)
    return buf.getvalue(), rc


def _state(p: dict[str, Path]) -> dict:
    return state_mod.load(p["state"])


def _metrics(p: dict[str, Path]) -> dict:
    return metrics_mod.load(p["metrics"])


def _config(p: dict[str, Path]) -> dict:
    return config_mod.load(p["config"])


def _display_path(path: Path) -> str:
    try:
        return "~/" + str(path.expanduser().relative_to(Path.home()))
    except ValueError:
        return str(path)


def _tail(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[-limit:]
