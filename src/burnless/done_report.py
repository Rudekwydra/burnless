from __future__ import annotations

from dataclasses import dataclass

# Final-text values that carry no real information about what a worker did.
GENERIC_SUMMARIES = {
    "",
    "done",
    "ok",
    "okay",
    "completed",
    "complete",
    "finished",
    "success",
    "gemma tool-worker completed",
    "tool-worker completed",
}


@dataclass
class DoneReport:
    delegation_id: str
    status: str
    kind: str
    one_line: str
    files_changed: list[str]
    verify_passed: int
    verify_total: int
    evidence_refs: list[str]
    answer_hint: str
    reread_recommended: bool
    reread_reason: str


def is_low_information(text: str | None) -> bool:
    """True when worker final text says nothing useful about the work done."""
    return (text or "").strip().lower() in GENERIC_SUMMARIES


def _decide_reread(status, kind, files_changed, verify_passed, verify_total, answer_hint):
    s = (status or "").upper()
    if s != "OK":
        return True, f"status={s or 'UNKNOWN'} needs inspection"
    if verify_total and verify_passed < verify_total:
        return True, f"verify {verify_passed}/{verify_total} failed"
    if kind == "report" and not answer_hint.strip():
        return True, "report task without answer_hint"
    if kind == "execution" and not files_changed and not verify_total:
        return True, "no files changed and no checks ran"
    return False, ""


def _render_one_line(
    delegation_id, status, kind, summary, files_changed,
    verify_passed, verify_total, evidence_refs, answer_hint,
):
    s = (status or "OK").upper()
    parts = [f"{s}:{delegation_id}"]
    if kind == "report":
        body = (answer_hint or summary or "").strip() or "(no summary)"
        parts.append(f"report: {body}")
        for ref in evidence_refs:
            if ref.startswith(("log:", "output", "diff:")):
                parts.append(f"output_ref:{ref}")
                break
    else:
        if files_changed:
            n = len(files_changed)
            parts.append(f"wrote {n} file{'s' if n != 1 else ''}")
        if verify_total:
            parts.append(f"verify {verify_passed}/{verify_total}")
        body = (summary or "").strip() or "(no summary)"
        parts.append(f"summary: {body}")
    cap = next(
        (r for r in evidence_refs if r.startswith("capsule:")),
        f"capsule:{delegation_id}",
    )
    parts.append(cap)
    return " · ".join(parts)


def build_done_report(
    *,
    delegation_id,
    status,
    kind="execution",
    summary="",
    files_changed=None,
    verify_passed=0,
    verify_total=0,
    evidence_refs=None,
    answer_hint="",
    raw_tail="",
):
    """Build a compact, answer-grade DoneReport from structured worker results.

    Pure: no I/O, no runner internals. Integration layers (runner, ollama,
    capsule, mcp) feed it already-structured values.
    """
    files_changed = list(files_changed or [])
    evidence_refs = list(evidence_refs or [])
    summary = (summary or "").strip()

    # Low-information final text (e.g. "done", "gemma tool-worker completed")
    # gets a synthesized summary from concrete signals instead.
    if is_low_information(summary):
        synth = []
        if files_changed:
            synth.append(f"touched {len(files_changed)} file(s)")
        if verify_total:
            synth.append(f"verify {verify_passed}/{verify_total}")
        if not synth and raw_tail.strip():
            synth.append(raw_tail.strip().splitlines()[-1][:120])
        summary = "; ".join(synth) or summary

    reread, reason = _decide_reread(
        status, kind, files_changed, verify_passed, verify_total, answer_hint
    )
    one_line = _render_one_line(
        delegation_id, status, kind, summary, files_changed,
        verify_passed, verify_total, evidence_refs, answer_hint,
    )
    return DoneReport(
        delegation_id=delegation_id,
        status=(status or "OK").upper(),
        kind=kind,
        one_line=one_line,
        files_changed=files_changed,
        verify_passed=verify_passed,
        verify_total=verify_total,
        evidence_refs=evidence_refs,
        answer_hint=answer_hint,
        reread_recommended=reread,
        reread_reason=reason,
    )
