from burnless.done_report import (
    DoneReport,
    build_done_report,
    is_low_information,
)


def test_execution_one_line_has_files_and_verify():
    r = build_done_report(
        delegation_id="d123",
        status="OK",
        kind="execution",
        summary="fixed CLI force forwarding",
        files_changed=["a.py", "b.py"],
        verify_passed=3,
        verify_total=3,
        evidence_refs=["capsule:d123"],
    )
    assert isinstance(r, DoneReport)
    assert r.one_line == (
        "OK:d123 · wrote 2 files · verify 3/3 · "
        "summary: fixed CLI force forwarding · capsule:d123"
    )
    assert r.reread_recommended is False


def test_partial_verify_recommends_reread():
    r = build_done_report(
        delegation_id="d124",
        status="OK",
        kind="execution",
        summary="x",
        files_changed=["a.py"],
        verify_passed=2,
        verify_total=3,
    )
    assert r.reread_recommended is True
    assert "verify 2/3" in r.reread_reason


def test_report_kind_uses_answer_hint():
    r = build_done_report(
        delegation_id="d130",
        status="OK",
        kind="report",
        summary="",
        answer_hint="top files by line count captured",
        evidence_refs=["log:d130", "capsule:d130"],
    )
    assert r.kind == "report"
    assert "report: top files by line count captured" in r.one_line
    assert "output_ref:log:d130" in r.one_line
    assert r.reread_recommended is False


def test_report_without_answer_hint_recommends_reread():
    r = build_done_report(
        delegation_id="d131", status="OK", kind="report", summary=""
    )
    assert r.reread_recommended is True


def test_low_information_summary_is_synthesized():
    assert is_low_information("done")
    assert is_low_information("gemma tool-worker completed")
    assert not is_low_information("wrote parser fix")
    r = build_done_report(
        delegation_id="d140",
        status="OK",
        kind="execution",
        summary="done",
        files_changed=["x.py"],
        verify_passed=1,
        verify_total=1,
    )
    assert "touched 1 file(s)" in r.one_line


def test_non_ok_status_recommends_reread():
    r = build_done_report(
        delegation_id="d150",
        status="PART",
        kind="execution",
        files_changed=["a.py"],
    )
    assert r.reread_recommended is True
    assert "PART" in r.reread_reason


def test_execution_no_files_no_checks_recommends_reread():
    r = build_done_report(
        delegation_id="d160",
        status="OK",
        kind="execution",
        summary="thought about it",
    )
    assert r.reread_recommended is True
