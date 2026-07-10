"""burnless doctor: healthcheck with optional auto-fix (Fase 5).

Architecture
------------
Each ``Check`` may carry a ``fixer`` closure. A check is *auto-fixable* iff it
has a fixer attached. Fixers are attached only to the allow-listed checks:

    B1            global config       → write default config
    C1 C2 C3 C5   settings/hooks/ptr  → wire hooks (idempotent)
    C4            managed files       → copy templates (mkdir + copy)
    D2            mcp-registered      → ``claude mcp add`` (fail-open)

Everything else (A*, B2-B5, C6, D1, D3) is intentionally *not* auto-fixable and
stays WARN/FAIL.

``run_checks(fix=True)`` runs one read-only pass, applies the available fixers in
a safe dependency order (config → managed files → hook wiring → MCP), then
re-collects every check so the returned list + summary reflect the fixed state.
"""
from __future__ import annotations

import json
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

_VERSION = "1"

_BAND_NAMES = {
    "A": "Install",
    "B": "Global Config",
    "C": "Claude Code",
    "D": "MCP",
    "E": "Chains",
    "F": "Ambiente",
    "G": "Jobs Agendados",
}

# Safe remediation order. mkdir/copy before wiring (hooks point at the copied
# scripts), wiring before MCP registration. Only ids that carry a fixer act.
_FIX_ORDER = ["B1", "C4", "C1", "C2", "C3", "C5", "D2"]


@dataclass
class Check:
    id: str
    band: str
    status: str  # PASS | WARN | FAIL
    detail: str
    fix_hint: str = ""
    fixer: Optional[Callable[[], None]] = None

    @property
    def auto_fixable(self) -> bool:
        return self.fixer is not None


# ── Orchestration ─────────────────────────────────────────────────────────────

def run_checks(*, home: Path | None = None, cwd: Path | None = None,
               fix: bool = False) -> list[Check]:
    """Run all checks. Read-only unless ``fix=True``.

    With ``fix=True``: collect once, apply every auto-fixable WARN/FAIL fixer in
    safe order, then re-collect so the result reflects the remediated state.
    """
    if home is None:
        home = Path.home()

    checks = _collect(home=home, cwd=cwd)
    if not fix:
        return checks

    _apply_fixes(checks)
    return _collect(home=home, cwd=cwd)


def _collect(*, home: Path, cwd: Path | None) -> list[Check]:
    checks: list[Check] = []
    _check_a(checks)
    _check_b(checks, cwd=cwd)
    _check_c(checks, home=home, cwd=cwd)
    _check_d(checks)
    _check_e(checks, cwd=cwd)
    _check_f(checks)
    _check_g(checks, cwd=cwd)
    return checks


def _apply_fixes(checks: list[Check]) -> list[str]:
    """Invoke fixers for auto-fixable WARN/FAIL checks, in safe order.

    Returns the ids actually remediated. Fixers are fail-open: a raising fixer
    is logged to stderr and skipped so one bad fix never aborts the rest.
    """
    by_id = {c.id: c for c in checks}
    applied: list[str] = []
    for cid in _FIX_ORDER:
        c = by_id.get(cid)
        if c is None or c.fixer is None or c.status not in ("WARN", "FAIL"):
            continue
        try:
            c.fixer()
            applied.append(cid)
        except Exception as e:  # fail-open
            print(f"doctor: fix {cid} failed: {e}", file=sys.stderr)
    return applied


# ── Shared fixer helpers ──────────────────────────────────────────────────────

def _write_default_config(cfg_path: Path) -> None:
    from . import config as config_mod
    from . import provider_autodetect
    detected = provider_autodetect.detect_providers()
    agents_override = provider_autodetect.build_agents(detected)
    config_mod.write_default(cfg_path, agents_override=agents_override)


def _wire_hooks(home: Path) -> None:
    from . import init_claude_code as _icc
    _icc.wire_settings_hook(home)


def _install_managed(home: Path) -> None:
    """Copy managed template files into HOME (mkdir + copy + preserve +x)."""
    from .init_claude_code import _MANAGED, _resolve_templates_dir
    tdir = _resolve_templates_dir()
    if tdir is None:
        return
    for src_rel, dst_rel in _MANAGED:
        src = tdir / src_rel
        dst = home / dst_rel
        if not src.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        src_mode = src.stat().st_mode
        if src_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
            dst.chmod(dst.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


# ── Band A: Install (none auto-fixable) ───────────────────────────────────────

def _check_a(checks: list[Check]) -> None:
    # A1: burnless binary in PATH (WARN not FAIL — python -m burnless still works)
    bl_path = shutil.which("burnless")
    if bl_path:
        checks.append(Check("A1", "A", "PASS", f"burnless binary: {bl_path}"))
    else:
        checks.append(Check("A1", "A", "WARN", "burnless not found in PATH",
                            "pip install burnless or add to PATH"))

    # A2: Python version ≥ 3.9
    vi = sys.version_info
    if vi >= (3, 9):
        checks.append(Check("A2", "A", "PASS", f"python {vi.major}.{vi.minor}.{vi.micro}"))
    else:
        checks.append(Check("A2", "A", "FAIL",
                            f"python {vi.major}.{vi.minor} < 3.9",
                            "upgrade to python 3.9+"))

    # A3: Required deps importable
    missing: list[str] = []
    for dep in ("yaml",):
        try:
            __import__(dep)
        except ImportError:
            missing.append(dep)
    if not missing:
        checks.append(Check("A3", "A", "PASS", "required deps importable (yaml)"))
    else:
        checks.append(Check("A3", "A", "FAIL",
                            f"missing deps: {', '.join(missing)}",
                            "pip install burnless"))

    # A4: templates dir (needed for init --claude-code)
    from .init_claude_code import _resolve_templates_dir
    tdir = _resolve_templates_dir()
    if tdir is not None:
        checks.append(Check("A4", "A", "PASS", f"templates: {tdir}"))
    else:
        checks.append(Check("A4", "A", "WARN",
                            "templates directory not found",
                            "reinstall burnless package"))


# ── Band B: Global Config (only B1 auto-fixable) ──────────────────────────────

def _check_b(checks: list[Check], cwd: Path | None = None) -> None:
    from . import paths as paths_mod
    from . import config as config_mod

    bl_root = paths_mod.find_root(start=cwd)
    if bl_root is None:
        # No project root → cannot guess where to write config. Not auto-fixable.
        checks.append(Check("B1", "B", "FAIL",
                            "no .burnless/ found in directory tree",
                            "run `burnless init` in project root"))
        for cid in ("B2", "B3", "B4", "B5"):
            checks.append(Check(cid, "B", "FAIL", "skipped: no .burnless/ found"))
        return

    cfg_path = bl_root / "config.yaml"

    def _fix_config() -> None:
        _write_default_config(cfg_path)

    if not cfg_path.exists():
        checks.append(Check("B1", "B", "FAIL",
                            f"config missing: {cfg_path}",
                            "run `burnless init`",
                            fixer=_fix_config))
        for cid in ("B2", "B3", "B4", "B5"):
            checks.append(Check(cid, "B", "FAIL", "skipped: config missing"))
        return

    try:
        cfg = config_mod.load(cfg_path)
        checks.append(Check("B1", "B", "PASS", f"config parses: {cfg_path}"))
    except Exception as e:
        checks.append(Check("B1", "B", "FAIL",
                            f"config parse error: {e}",
                            "fix YAML syntax in .burnless/config.yaml",
                            fixer=_fix_config))
        for cid in ("B2", "B3", "B4", "B5"):
            checks.append(Check(cid, "B", "FAIL", "skipped: config parse error"))
        return

    # B2: agents configured (not auto-fixable — needs `burnless setup`)
    agents = cfg.get("agents", {})
    configured = [t for t in ("bronze", "silver", "gold", "diamond") if t in agents]
    if configured:
        checks.append(Check("B2", "B", "PASS", f"agents: {', '.join(configured)}"))
    else:
        checks.append(Check("B2", "B", "FAIL",
                            "no agents configured",
                            "run `burnless setup` to auto-detect CLIs"))

    # B3: tier routing resolves (not auto-fixable)
    try:
        from . import routing as routing_mod
        tier, _ = routing_mod.route("test task", cfg.get("routing", {}))
        checks.append(Check("B3", "B", "PASS", f"routing resolves: default→{tier}"))
    except Exception as e:
        checks.append(Check("B3", "B", "FAIL", f"routing error: {e}"))

    # B4: required top-level keys present (not auto-fixable — overwrite is unsafe)
    missing_keys = [k for k in ("agents", "routing", "metrics") if k not in cfg]
    if not missing_keys:
        checks.append(Check("B4", "B", "PASS", "config has all required top-level keys"))
    else:
        checks.append(Check("B4", "B", "WARN",
                            f"config missing keys: {', '.join(missing_keys)}",
                            "run `burnless init --force` to regenerate config"))

    # B5: state.json exists and parses (not auto-fixable)
    state_path = bl_root / "state.json"
    if state_path.exists():
        try:
            json.loads(state_path.read_text(encoding="utf-8"))
            checks.append(Check("B5", "B", "PASS", "state.json parses"))
        except Exception as e:
            checks.append(Check("B5", "B", "WARN", f"state.json parse error: {e}"))
    else:
        checks.append(Check("B5", "B", "WARN",
                            "state.json not found",
                            "run `burnless init` or `burnless setup`"))


# ── Band C: Claude Code wiring (C1-C5 auto-fixable, C6 not) ────────────────────

def _check_c(checks: list[Check], home: Path | None = None, cwd: Path | None = None) -> None:
    if home is None:
        home = Path.home()
    from .init_claude_code import is_wired
    from . import paths as paths_mod

    def _fix_wire() -> None:
        _wire_hooks(home)

    def _fix_managed() -> None:
        _install_managed(home)

    # C1: settings.json exists → wire (creates settings.json)
    wired = is_wired(home)

    if wired["settings_exists"]:
        checks.append(Check("C1", "C", "PASS", "~/.claude/settings.json exists"))
    else:
        checks.append(Check("C1", "C", "FAIL",
                            "~/.claude/settings.json not found",
                            "run `burnless init --claude-code`",
                            fixer=_fix_wire))

    # C2: settings.json parses → wire is fail-open if JSON is corrupt
    if wired["settings_exists"] and wired["settings_parses"]:
        checks.append(Check("C2", "C", "PASS", "settings.json parses as JSON"))
    elif wired["settings_exists"]:
        checks.append(Check("C2", "C", "FAIL",
                            "settings.json is not valid JSON",
                            "fix JSON syntax in ~/.claude/settings.json",
                            fixer=_fix_wire))
    else:
        checks.append(Check("C2", "C", "FAIL",
                            "settings.json missing (cannot parse)",
                            "run `burnless init --claude-code`",
                            fixer=_fix_wire))

    # C3: UserPromptSubmit hook wired → wire
    if wired["userprompt"]:
        checks.append(Check("C3", "C", "PASS", "UserPromptSubmit hook wired"))
    else:
        checks.append(Check("C3", "C", "FAIL",
                            "UserPromptSubmit hook not wired",
                            "run `burnless init --claude-code`",
                            fixer=_fix_wire))

    # C4: managed files present/match templates → copy templates
    managed = wired["managed"]
    missing_mgd = [m for m in managed if m["state"] == "missing"]
    differs_mgd = [m for m in managed if m["state"] == "differs"]
    if not missing_mgd and not differs_mgd:
        checks.append(Check("C4", "C", "PASS",
                            f"all {len(managed)} managed files match templates"))
    elif missing_mgd:
        names = ", ".join(m["rel"] for m in missing_mgd[:3])
        more = f" (+{len(missing_mgd) - 3} more)" if len(missing_mgd) > 3 else ""
        checks.append(Check("C4", "C", "FAIL",
                            f"managed files missing: {names}{more}",
                            "run `burnless init --claude-code`",
                            fixer=_fix_managed))
    else:
        names = ", ".join(m["rel"] for m in differs_mgd[:3])
        checks.append(Check("C4", "C", "WARN",
                            f"managed files differ from templates: {names}",
                            "run `burnless init --claude-code --force` to update",
                            fixer=_fix_managed))

    # C5: SessionStart hook wired (session seed pointer) → wire
    if wired["sessionstart"]:
        checks.append(Check("C5", "C", "PASS", "SessionStart hook wired"))
    else:
        checks.append(Check("C5", "C", "FAIL",
                            "SessionStart hook not wired",
                            "run `burnless init --claude-code`",
                            fixer=_fix_wire))

    # C6: SessionEnd hook wired (clear handoff) → wire
    if wired["sessionend"]:
        checks.append(Check("C6", "C", "PASS", "SessionEnd hook wired"))
    else:
        checks.append(Check("C6", "C", "FAIL",
                            "SessionEnd hook not wired",
                            "run `burnless init --claude-code`",
                            fixer=_fix_wire))

    # C7: epoch SessionStart restore hook wired → wire
    if wired["epoch_session"]:
        checks.append(Check("C7", "C", "PASS", "epoch SessionStart hook wired"))
    else:
        checks.append(Check("C7", "C", "FAIL",
                            "epoch SessionStart hook not wired",
                            "run `burnless init --claude-code`",
                            fixer=_fix_wire))

    # C8: recovery watermark / last error visibility
    project_root = paths_mod.find_root(start=cwd or Path.cwd())
    if project_root is not None:
        try:
            from .pilot import summarize_session_log
            from . import config as config_mod

            summary = summarize_session_log(project_root)
            gap = summary.get("watermark_gap")
            last_error = summary.get("last_error")

            # Read threshold from config
            try:
                cfg = config_mod.load(project_root / ".burnless" / "config.yaml")
                threshold = int(cfg.get("epochs", {}).get("watermark_alarm_gap", 5))
            except Exception:
                threshold = 5

            if last_error:
                checks.append(Check(
                    "C8",
                    "C",
                    "WARN",
                    f"recovery last_error: {last_error}",
                ))
            elif isinstance(gap, int) and gap >= threshold:
                checks.append(Check(
                    "C8",
                    "C",
                    "WARN",
                    f"recovery watermark gap: {gap}",
                ))
            elif isinstance(gap, int) and gap > 0:
                checks.append(Check(
                    "C8",
                    "C",
                    "PASS",
                    f"recovery watermark gap: {gap}",
                ))
            else:
                checks.append(Check(
                    "C8",
                    "C",
                    "PASS",
                    f"recovery watermark gap: {gap if gap is not None else 0}",
                ))
        except Exception as e:
            checks.append(Check("C8", "C", "WARN", f"recovery status unavailable: {e}"))
    else:
        checks.append(Check("C8", "C", "WARN", "recovery status unavailable: no project root"))

    # C9: hook error log visibility
    from . import config as config_mod
    hook_error_log = home / ".burnless" / "state" / "hook_errors.log"

    # Read tail limit from config
    try:
        project_root_for_cfg = paths_mod.find_root(start=cwd or Path.cwd())
        if project_root_for_cfg is not None:
            cfg = config_mod.load(project_root_for_cfg / ".burnless" / "config.yaml")
            tail_limit = int(cfg.get("epochs", {}).get("hook_error_tail", 5))
        else:
            tail_limit = 5
    except Exception:
        tail_limit = 5

    if hook_error_log.exists():
        try:
            text = hook_error_log.read_text(encoding="utf-8", errors="replace").strip().splitlines()
            last_lines = text[-tail_limit:] if text else []
            if last_lines:
                detail = " ".join(l[:180] for l in last_lines)
                checks.append(Check("C9", "C", "WARN", f"hook errors recorded: {detail}"))
            else:
                checks.append(Check("C9", "C", "PASS", "hook error log present but empty"))
        except Exception as e:
            checks.append(Check("C9", "C", "WARN", f"hook error log unreadable: {e}"))
    else:
        checks.append(Check("C9", "C", "PASS", "hook error log absent"))

    # C10: sys.executable resolves to python3 (not auto-fixable)
    exe = sys.executable
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=5)
        ver = (r.stdout or r.stderr).strip()
        checks.append(Check("C10", "C", "PASS", f"sys.executable: {exe} ({ver})"))
    except Exception as e:
        checks.append(Check("C10", "C", "WARN", f"sys.executable unverifiable: {e}"))


# ── Band D: MCP (only D2 auto-fixable) ────────────────────────────────────────

def _check_d(checks: list[Check]) -> None:
    # D1: mcp_server module importable (not auto-fixable — needs pip install)
    try:
        import importlib
        importlib.import_module("burnless.mcp_server")
        checks.append(Check("D1", "D", "PASS", "burnless.mcp_server importable"))
    except ImportError as e:
        checks.append(Check("D1", "D", "WARN",
                            f"mcp_server not importable: {e}",
                            "pip install 'burnless[mcp]' or install mcp package"))
    except Exception as e:
        checks.append(Check("D1", "D", "WARN", f"mcp_server import error: {e}"))

    # D2: mcp_server --check (subprocess, timeout 5s, fail-open to WARN)
    def _fix_mcp() -> None:  # fail-open registration
        subprocess.run(["claude", "mcp", "add", "burnless"],
                       capture_output=True, text=True)

    try:
        r = subprocess.run(
            [sys.executable, "-m", "burnless.mcp_server", "--check"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and "ok" in (r.stdout or "").lower():
            checks.append(Check("D2", "D", "PASS", "mcp_server --check: ok"))
        else:
            checks.append(Check("D2", "D", "WARN",
                                f"mcp_server --check rc={r.returncode}",
                                "pip install mcp",
                                fixer=_fix_mcp))
    except subprocess.TimeoutExpired:
        checks.append(Check("D2", "D", "WARN", "mcp_server --check timed out (5s)"))
    except Exception as e:
        checks.append(Check("D2", "D", "WARN", f"mcp_server --check error: {e}"))

    # D3: claude mcp list (timeout 3s, fail-open to WARN; not auto-fixable)
    try:
        r = subprocess.run(
            ["claude", "mcp", "list"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode == 0:
            checks.append(Check("D3", "D", "PASS", "claude mcp list: ok"))
        else:
            checks.append(Check("D3", "D", "WARN",
                                "claude mcp list failed (burnless not configured as MCP)",
                                "add burnless to claude MCP config"))
    except FileNotFoundError:
        checks.append(Check("D3", "D", "WARN",
                            "claude CLI not found in PATH",
                            "install claude CLI"))
    except subprocess.TimeoutExpired:
        checks.append(Check("D3", "D", "WARN", "claude mcp list timed out (3s)"))
    except Exception as e:
        checks.append(Check("D3", "D", "WARN", f"claude mcp list error: {e}"))


def _check_e(checks: list[Check], cwd: Path | None = None) -> None:
    from . import paths as paths_mod

    root_path = paths_mod.find_root(start=cwd or Path.cwd())
    if root_path is None:
        return

    try:
        from . import recovery
        chains = recovery.list_chains(root_path, host="claude")
    except Exception:
        chains = []

    ambiguous_recent = False
    log_path = root_path / "owner_loop.jsonl"
    if log_path.exists():
        try:
            lines = log_path.read_text(encoding="utf-8").splitlines()[-500:]
            for line in lines:
                try:
                    event = json.loads(line)
                except Exception:
                    continue
                if event.get("claim_mode") == "adoption_ambiguous":
                    ambiguous_recent = True
        except Exception:
            pass

    if ambiguous_recent:
        checks.append(
            Check(
                "E1",
                "E",
                "WARN",
                "adoção ambígua detectada (2+ chains mortas elegíveis na mesma reivindicação)",
                "revise .burnless/owner_loop.jsonl para conferir qual chain foi adotada",
            )
        )
    else:
        checks.append(Check("E1", "E", "PASS", f"{len(chains)} chain(s) — nenhuma adoção ambígua recente"))


# ── Band F: Ambiente (local server reachability) ───────────────────────────────

def _check_f(checks: list[Check]) -> None:
    import os
    import socket
    import urllib.request
    from urllib.parse import urlparse

    api_mode = os.environ.get("BURNLESS_LOCAL_API", "ollama").lower()
    if api_mode == "llamacpp":
        local_host = os.environ.get("BURNLESS_LOCAL_HOST", "http://localhost:11435")
        parsed = urlparse(local_host)
        port = parsed.port or 80
        try:
            with socket.create_connection((parsed.hostname, port), timeout=1.5):
                checks.append(Check("F1", "F", "PASS",
                                    f"env: BURNLESS_LOCAL_API=llamacpp, {local_host} (TCP reachable)"))
        except OSError as e:
            checks.append(Check("F1", "F", "WARN",
                                f"env: BURNLESS_LOCAL_API=llamacpp mas {local_host} inalcancavel ({e})",
                                "unsetar BURNLESS_LOCAL_API ou corrigir BURNLESS_LOCAL_HOST / subir o servidor llamacpp"))
    else:
        local_host = os.environ.get("BURNLESS_OLLAMA_HOST", "http://localhost:11434")
        try:
            with urllib.request.urlopen(local_host.rstrip("/") + "/api/tags", timeout=2) as r:
                ok = getattr(r, "status", 200) == 200
        except Exception as e:
            ok = False
        if ok:
            checks.append(Check("F1", "F", "PASS", f"env: ollama reachable at {local_host}"))
        else:
            checks.append(Check("F1", "F", "WARN",
                                f"env: BURNLESS_LOCAL_API=ollama (default) mas {local_host}/api/tags nao respondeu",
                                "confirme que o ollama esta rodando, ou setar BURNLESS_OLLAMA_HOST"))


# ── Band G: Jobs Agendados ──────────────────────────────────────────────────────

def _check_g(checks: list[Check], cwd: Path | None = None) -> None:
    from datetime import datetime, timezone
    from . import paths as paths_mod
    from . import config as config_mod

    bl_root = paths_mod.find_root(start=cwd)
    if bl_root is None:
        return

    cfg_path = bl_root / "config.yaml"
    if not cfg_path.exists():
        return

    try:
        cfg = config_mod.load(cfg_path)
    except Exception:
        return

    jobs = cfg.get("scheduled_jobs", [])
    if not jobs:
        return

    for i, job in enumerate(jobs, start=1):
        try:
            name = job.get("name")
            path_str = job.get("path")
            period_hours = job.get("period_hours")

            if not all([name, path_str, period_hours is not None]):
                checks.append(Check(f"G{i}", "G", "WARN",
                                    f"scheduled_jobs[{i}] malformado: faltam chaves (name={name}, path={path_str}, period_hours={period_hours})"))
                continue

            path = Path(path_str).expanduser()

            if not path.exists():
                checks.append(Check(f"G{i}", "G", "FAIL",
                                    f"job '{name}': path nao existe ({path_str}) — nunca rodou ou path errado"))
                continue

            mtime_ts = path.stat().st_mtime
            now_ts = datetime.now(timezone.utc).timestamp()
            age_hours = (now_ts - mtime_ts) / 3600

            limit_hours = 2 * period_hours
            if age_hours > limit_hours:
                checks.append(Check(f"G{i}", "G", "FAIL",
                                    f"job '{name}': ultimo sucesso ha {age_hours:.1f}h (periodo esperado {period_hours}h, limite {limit_hours}h)",
                                    f"verificar cron/launchd do job '{name}'"))
            else:
                checks.append(Check(f"G{i}", "G", "PASS",
                                    f"job '{name}': ultimo sucesso ha {age_hours:.1f}h (dentro do periodo de {period_hours}h)"))
        except Exception as e:
            checks.append(Check(f"G{i}", "G", "WARN",
                                f"scheduled_jobs[{i}] erro: {e}"))


# ── Renderers ─────────────────────────────────────────────────────────────────

def render_human(checks: list[Check]) -> str:
    lines = ["burnless doctor", ""]
    current_band: str | None = None

    for c in checks:
        if c.band != current_band:
            current_band = c.band
            label = _BAND_NAMES.get(c.band, c.band)
            lines.append(f"── Band {c.band}: {label} ──")
        hint = f"  → {c.fix_hint}" if c.fix_hint else ""
        lines.append(f"  {c.id:<4} [{c.status:4}]  {c.detail}{hint}")

    lines.append("")
    passes = sum(1 for c in checks if c.status == "PASS")
    warns  = sum(1 for c in checks if c.status == "WARN")
    fails  = sum(1 for c in checks if c.status == "FAIL")
    ec = exit_code(checks)
    lines.append(f"summary: {passes} PASS  {warns} WARN  {fails} FAIL  →  exit {ec}")
    if ec == 0:
        lines.append("Every chat opens full. ✓")

    return "\n".join(lines)


def render_json(checks: list[Check]) -> dict:
    ec = exit_code(checks)
    return {
        "version": _VERSION,
        "checks": [
            {"id": c.id, "band": c.band, "status": c.status,
             "detail": c.detail, "fix_hint": c.fix_hint,
             "auto_fixable": c.auto_fixable}
            for c in checks
        ],
        "summary": {
            "pass": sum(1 for c in checks if c.status == "PASS"),
            "warn": sum(1 for c in checks if c.status == "WARN"),
            "fail": sum(1 for c in checks if c.status == "FAIL"),
        },
        "exit": ec,
    }


def exit_code(checks: list[Check]) -> int:
    return 1 if any(c.status == "FAIL" for c in checks) else 0
