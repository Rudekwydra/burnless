"""Audit subsystem extracted from cli.py.

Public API (also re-exported by cli.py with leading-underscore aliases for
backward compatibility with existing tests):

    audit_summary_evidence(...)    — main audit orchestrator (QTP-A/B + LLM ladder)
    audit_execution_filesystem(...) — filesystem-first audit for execution kind
    fast_path_check(...)           — deterministic non-LLM check (git/path stat)
    summary_evidence(summary)      — normalize evidence list from worker summary
    append_issue(summary, issue)   — additive issue mutation
    add_evidence_feedback(...)     — append "give evidence" hint to summary.next
    write_audit_result(path, audit) — persist audit json
    render_audit_prompt(...)       — LLM auditor prompt template
    infer_kind_hint(text)          — guess thought|execution from a prompt
    normalize_report_kind(value)   — canonicalize report kind value

This module is a faithful extraction from cli.py (pre-v0.8 refactor). Bodies
are copied unchanged; only the public names lose their leading underscore.
The cli.py re-export shim keeps `cli._audit_summary_evidence` etc. working
so existing tests and callers don't need to migrate immediately.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from . import agents as agents_mod
from . import delegations as deleg_mod

# ── Constants ────────────────────────────────────────────────────────────────

_THOUGHT_HINTS = (
    "planeje", "plano", "plan", "design", "desenhe", "arquitetura", "architecture",
    "decida", "decidir", "decision", "analise", "análise", "analyze", "review",
    "investigue", "investigar", "inspect", "study", "estude", "spec", "brief",
    "proposta", "proposal", "brainstorm", "ideia", "idea",
)
_EXECUTION_HINTS = (
    "implemente", "implementar", "fix", "corrija", "corrigir", "patch", "test",
    "teste", "write", "escreva", "editar", "edit", "create", "criar", "run",
    "execute", "executar", "validate", "validar",
)

_HEX_RE = re.compile(r'\b([0-9a-f]{7,40})\b', re.IGNORECASE)
_ABS_PATH_RE = re.compile(r'(/[^\s,;:"\')\]]+)')
_VALIDATED_SIZE_RE = re.compile(r'([A-Za-z0-9_./\-]+\.[A-Za-z0-9]+).*?(\d+)\s*bytes', re.IGNORECASE)


# ── Helpers ──────────────────────────────────────────────────────────────────

def summary_evidence(summary: dict) -> list[str]:
    raw = summary.get("evidence")
    if raw is None:
        raw = summary.get("validated")
    if not isinstance(raw, list):
        return []
    evidence: list[str] = []
    for item in raw:
        text = str(item).strip()
        if text:
            evidence.append(text[:180])
    return evidence


def append_issue(summary: dict, issue: str) -> None:
    issues = summary.get("issues")
    if not isinstance(issues, list):
        issues = []
    if issue not in issues:
        issues.append(issue)
    summary["issues"] = issues


def add_evidence_feedback(summary: dict, audit: dict | None = None) -> None:
    feedback = "Add concrete evidence: command, file, or check observed."
    current_next = str(summary.get("next") or "").strip()
    if feedback not in current_next:
        summary["next"] = f"{current_next} {feedback}".strip()
    if audit is not None:
        audit.setdefault("feedback", feedback)


def write_audit_result(path: Path, audit: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(audit, f, indent=2, ensure_ascii=False)


def infer_kind_hint(text: str) -> str:
    low = text.lower()
    thought_score = sum(1 for hint in _THOUGHT_HINTS if hint in low)
    exec_score = sum(1 for hint in _EXECUTION_HINTS if hint in low)
    if thought_score > exec_score:
        return "thought"
    if exec_score > thought_score:
        return "execution"
    return "execution"


def normalize_report_kind(value: object) -> str:
    text = str(value or "").strip().lower()
    if text in {"thought", "thinking", "design", "plan", "analysis"}:
        return "thought"
    if text in {"execution", "feito", "done", "implemented"}:
        return "execution"
    return "execution"


# ── Filesystem-first auditor (QTP-A) ─────────────────────────────────────────

def audit_execution_filesystem(summary: dict, cwd: Path) -> dict | None:
    """QTP-A: filesystem-first audit for kind=execution reports.

    For execution-kind reports, hard evidence (files exist on disk + sizes
    match declared values) outweighs auditor prose nitpicks. Returns:
      - audit dict with status OK if all files_touched exist and validated
        sizes match within 1024B tolerance
      - audit dict with status FAIL if any declared file is missing or
        sizes mismatch
      - None if there's not enough evidence to decide (caller falls back
        to fast_path / LLM auditor ladder)

    QTP-B: when this returns OK, the runner does not downgrade worker OK
    based on prose-level audit issues — files on disk are the source of truth.
    """
    files_touched = summary.get("files_touched") or []
    if not isinstance(files_touched, list) or not files_touched:
        return None

    missing: list[str] = []
    for path_str in files_touched:
        if not isinstance(path_str, str) or not path_str:
            continue
        p = Path(path_str)
        if not p.is_absolute():
            p = cwd / p
        if not p.exists():
            missing.append(path_str)

    if missing:
        return {
            "status": "FAIL",
            "summary": f"Filesystem audit: {len(missing)} declared file(s) missing on disk",
            "evidence_checked": [str(x) for x in files_touched[:5]],
            "issues": [f"missing: {m}" for m in missing[:5]],
            "auditor_tier": "filesystem_first",
            "auditor_name": "filesystem_first",
            "attempted_tiers": [],
            "attempted_auditors": [],
        }

    validated = summary.get("validated") or []
    size_mismatches: list[str] = []
    if isinstance(validated, list):
        for entry in validated:
            m = _VALIDATED_SIZE_RE.search(str(entry))
            if not m:
                continue
            name, declared = m.group(1), int(m.group(2))
            actual_path: Path | None = None
            for ft in files_touched:
                if not isinstance(ft, str):
                    continue
                if name in ft:
                    p = Path(ft)
                    if not p.is_absolute():
                        p = cwd / p
                    if p.exists():
                        actual_path = p
                        break
            if actual_path is None:
                continue
            try:
                actual_size = actual_path.stat().st_size
            except OSError:
                continue
            if abs(actual_size - declared) > 1024:
                size_mismatches.append(
                    f"{name}: declared {declared}B, actual {actual_size}B"
                )

    if size_mismatches:
        return {
            "status": "FAIL",
            "summary": f"Filesystem audit: size mismatch in {len(size_mismatches)} file(s)",
            "evidence_checked": [str(x) for x in (validated[:3] if isinstance(validated, list) else [])],
            "issues": size_mismatches[:5],
            "auditor_tier": "filesystem_first",
            "auditor_name": "filesystem_first",
            "attempted_tiers": [],
            "attempted_auditors": [],
        }

    return {
        "status": "OK",
        "summary": f"Filesystem audit: {len(files_touched)} file(s) present on disk, sizes match",
        "evidence_checked": [str(x) for x in files_touched[:5]],
        "issues": [],
        "auditor_tier": "filesystem_first",
        "auditor_name": "filesystem_first",
        "attempted_tiers": [],
        "attempted_auditors": [],
    }


# ── Fast-path deterministic check ────────────────────────────────────────────

def fast_path_check(evidence: list[str], cwd: Path) -> tuple[bool, str]:
    """Return (passed, reason) if evidence is deterministically verifiable without LLM.

    T1: 7+ consecutive hex chars verified with git cat-file -e.
    T2: absolute path that exists with size > 0.
    """
    combined = " ".join(evidence)
    for m in _HEX_RE.finditer(combined):
        h = m.group(1)
        try:
            r = subprocess.run(
                ["git", "cat-file", "-e", h],
                cwd=cwd,
                capture_output=True,
                timeout=5,
            )
            if r.returncode == 0:
                return True, f"git cat-file -e {h} → exit 0"
        except Exception:
            pass
    for m in _ABS_PATH_RE.finditer(combined):
        path_str = m.group(1).rstrip(".,;:\"'")
        try:
            fp = Path(path_str)
            if fp.exists() and fp.stat().st_size > 0:
                return True, f"stat({path_str}) → exists, size={fp.stat().st_size}"
        except Exception:
            pass
    return False, ""


# ── LLM audit prompt ─────────────────────────────────────────────────────────

def render_audit_prompt(*, did: str, prompt: str, summary: dict, log_excerpt: str) -> str:
    return f"""\
You are the Burnless Auditor. Read-only: do not edit files or run commands.
Check whether the worker summary and evidence are supported by the delegation prompt and log excerpt.
Evidence must cite observable commands, files, logs, or checks, not opinions.

Delegation ID: {did}

Worker summary JSON:
```json
{json.dumps(summary, indent=2, ensure_ascii=False)}
```

Delegation prompt excerpt:
```
{prompt[:8000]}
```

Log tail:
```
{log_excerpt}
```

Return only a final JSON block:
```json
{{
  "status": "OK | FAIL",
  "summary": "<one short sentence>",
  "evidence_checked": [],
  "issues": []
}}
```
"""


# ── Main orchestrator ────────────────────────────────────────────────────────

def audit_summary_evidence(
    p: dict[str, Path],
    *,
    cfg: dict,
    did: str,
    prompt: str,
    summary: dict,
    log_path: Path,
    timeout: int,
    cwd: Path,
) -> dict:
    summary = dict(summary)
    status = str(summary.get("status") or "").upper()
    summary["status"] = status or summary.get("status")
    kind = normalize_report_kind(summary.get("kind") or summary.get("report_kind") or infer_kind_hint(prompt))
    summary["kind"] = kind
    evidence = summary_evidence(summary)

    # H8: pre_audit_call — plugins run BEFORE any early-exit so they can
    # provide audit even when config disables the LLM ladder. This makes
    # opt-in plugins like snapshot_audit (filesystem-only, zero-cost) work
    # regardless of `audit.enabled`. The config flag only gates the LLM
    # ladder (filesystem/fast_path/bronze/silver/gold) downstream.
    from . import plugin_loader as _pl
    _plugins = _pl.load_plugins(Path.home() / ".burnless")
    audit_path = p["temp"] / f"{did}.audit.json"
    auditors_ladder = cfg.get("audit", {}).get("auditors") or ["bronze", "silver", "gold"]
    _h8 = _pl.call_all_plugins(
        _plugins, "pre_audit_call",
        {"hook": "pre_audit_call", "did": did, "evidence": evidence, "summary": summary, "auditors_ladder": auditors_ladder},
    )
    if _h8 and _h8.get("audit") is not None:
        audit = dict(_h8["audit"])
        audit.setdefault("auditor_tier", "plugin")
        audit.setdefault("auditor_name", "plugin")
        audit.setdefault("attempted_tiers", [])
        audit.setdefault("attempted_auditors", [])
        write_audit_result(audit_path, audit)
        summary["audit"] = audit
        _pl.call_all_plugins(
            _plugins, "audit_result_received",
            {"hook": "audit_result_received", "did": did, "audit": audit, "summary": summary},
        )
        return summary
    if _h8 and _h8.get("override_ladder"):
        auditors_ladder = list(_h8["override_ladder"])

    if kind == "thought" and not evidence:
        audit = {
            "status": "SKIPPED",
            "summary": "Thought-only report; execution evidence not required.",
            "evidence_checked": [],
            "issues": [],
            "auditor_tier": None,
            "auditor_name": None,
            "attempted_tiers": [],
            "attempted_auditors": [],
        }
        summary["audit"] = audit
        return summary
    if status == "OK" and not evidence:
        summary["status"] = "PART"
        append_issue(summary, "missing_evidence")
        add_evidence_feedback(summary)
        status = "PART"
    if status not in {"OK", "PART"} or not evidence:
        return summary

    if cfg.get("audit", {}).get("enabled", True) is False:
        summary["audit"] = {
            "status": "SKIPPED",
            "summary": "Audit disabled in config (audit.enabled: false).",
            "evidence_checked": [],
            "issues": [],
            "auditor_tier": None,
            "auditor_name": None,
            "attempted_tiers": [],
            "attempted_auditors": [],
        }
        return summary

    # QTP-A/B: filesystem-first auditor for kind=execution. When the worker
    # declared files_touched, treat the filesystem as ground truth. Files
    # present + sizes match → audit OK regardless of any LLM prose nitpicks.
    # Files missing or size mismatch → audit FAIL with concrete reason.
    if kind == "execution":
        fs_audit = audit_execution_filesystem(summary, cwd)
        if fs_audit is not None:
            # QTP-E: attach visual thumbnails for png/pdf/pptx/html artifacts
            from . import visual_review as _vr
            _vr_cfg = cfg.get("visual_review") or {}
            if _vr_cfg.get("enabled", True):
                _vr.attach_thumbnails(
                    summary, cwd,
                    enabled=True,
                    thumbnails=_vr_cfg.get("thumbnails", True),
                    max_size=int(_vr_cfg.get("max_size", 256)),
                    max_artifacts=int(_vr_cfg.get("max_artifacts", 5)),
                )
            write_audit_result(audit_path, fs_audit)
            summary["audit"] = fs_audit
            if fs_audit["status"] == "FAIL" and status == "OK":
                summary["status"] = "PART"
                first_issue = (fs_audit.get("issues") or ["filesystem_audit_fail"])[0]
                append_issue(summary, first_issue)
            _pl.call_all_plugins(
                _plugins, "audit_result_received",
                {"hook": "audit_result_received", "did": did, "audit": fs_audit, "summary": summary},
            )
            return summary

    # Fast-path: deterministic check before calling any LLM auditor (T1/T2).
    fp_passed, fp_reason = fast_path_check(evidence, cwd)
    if fp_passed:
        audit = {
            "status": "OK",
            "summary": f"Fast-path: {fp_reason}",
            "evidence_checked": evidence[:3],
            "issues": [],
            "auditor_tier": "fast_path",
            "auditor_name": "fast_path",
            "attempted_tiers": [],
            "attempted_auditors": [],
        }
        write_audit_result(audit_path, audit)
        summary["audit"] = audit
        # H4: audit_result_received
        _pl.call_all_plugins(_plugins, "audit_result_received", {"hook": "audit_result_received", "did": did, "audit": audit, "summary": summary})
        return summary

    log_excerpt = log_path.read_text(encoding="utf-8")[-12000:] if log_path.exists() else ""
    audit_prompt = render_audit_prompt(did=did, prompt=prompt, summary=summary, log_excerpt=log_excerpt)
    agent_cfg = cfg.get("agents") or {}
    # auditors_ladder already set above (possibly overridden by H8)
    attempted_auditors = []
    unavailable = []
    audit = None
    for auditor_name in auditors_ladder:
        tier_cfg = agent_cfg.get(auditor_name)
        if not tier_cfg:
            continue
        attempted_auditors.append(auditor_name)
        if not agents_mod.is_available(tier_cfg):
            unavailable.append(f"{auditor_name} auditor unavailable")
            continue
        try:
            result = agents_mod.run(tier_cfg, audit_prompt, timeout=timeout, cwd=cwd)
        except Exception as exc:
            unavailable.append(f"{auditor_name} auditor failed to run: {exc}")
            continue
        audit = deleg_mod.extract_result_json(result.get("stdout", "")) or {
            "status": "FAIL",
            "summary": "Auditor did not emit final JSON.",
            "evidence_checked": evidence[:3],
            "issues": ["missing_audit_json"],
        }
        audit["auditor_tier"] = auditor_name  # legacy
        audit["auditor_name"] = auditor_name
        audit["attempted_tiers"] = attempted_auditors  # legacy
        audit["attempted_auditors"] = attempted_auditors
        break

    if audit is None:
        audit = {
            "status": "UNAVAILABLE",
            "summary": "; ".join(unavailable) or "No configured auditor tiers available.",
            "evidence_checked": evidence[:3],
            "issues": ["audit_unavailable"],
            "auditor_tier": None,
            "auditor_name": None,
            "attempted_tiers": attempted_auditors,
            "attempted_auditors": attempted_auditors,
        }
        write_audit_result(audit_path, audit)
        summary["audit"] = audit
        if summary.get("status") == "OK":
            summary["status"] = "PART"
        append_issue(summary, "audit_unavailable")
        add_evidence_feedback(summary, audit)
        # H4: audit_result_received
        _pl.call_all_plugins(_plugins, "audit_result_received", {"hook": "audit_result_received", "did": did, "audit": audit, "summary": summary})
        return summary

    audit_status = str(audit.get("status") or "").upper()
    audit["status"] = audit_status or "FAIL"
    audit.setdefault("attempted_tiers", attempted_auditors)
    audit.setdefault("attempted_auditors", attempted_auditors)
    write_audit_result(audit_path, audit)
    summary["audit"] = audit
    # H4: audit_result_received
    _pl.call_all_plugins(_plugins, "audit_result_received", {"hook": "audit_result_received", "did": did, "audit": audit, "summary": summary})
    if audit["status"] not in {"OK", "PASS"}:
        if summary.get("status") == "OK":
            summary["status"] = "PART"
        append_issue(summary, "audit_failed")
        add_evidence_feedback(summary, audit)
    return summary
