import sys
import pathlib
import tempfile
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
import instruction_surface_check as isc


def test_scan_flags_forbidden():
    """scan_text should flag burnless shell and /rewind patterns."""
    text = "run burnless shell now\nuse /rewind please"
    findings = isc.scan_text(text)
    rule_names = {f["rule"] for f in findings}
    assert "burnless shell" in rule_names
    assert "/rewind mainline" in rule_names


def test_allow_marker_suppresses():
    """Lines with allow_markers should be skipped entirely."""
    text = "legacy partner and rollover are coerced to on"
    findings = isc.scan_text(text)
    assert findings == []


def test_chat_id_not_flagged():
    """--chat-id and /chat-id should NOT match --chat or /chat patterns."""
    text = '"$BB" epoch resume --chat-id "$SID"'
    findings = isc.scan_text(text)
    assert findings == []


def test_bare_chat_flagged():
    """Bare /chat should be flagged."""
    text = "type /chat to start"
    findings = isc.scan_text(text)
    assert len(findings) > 0
    rule_names = {f["rule"] for f in findings}
    assert "/chat command" in rule_names


def test_partner_flagged_without_marker():
    """'partner' word boundary should be flagged when not in allow_marker line."""
    text = "mode == partner"
    findings = isc.scan_text(text)
    rule_names = {f["rule"] for f in findings}
    assert "legacy mode partner" in rule_names


def test_scan_file_missing_returns_empty():
    """scan_file on missing file should return []."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = pathlib.Path(tmpdir)
        findings = isc.scan_file(tmp_path / "nope.md")
        assert findings == []


def test_scan_surfaces_empty_when_no_files():
    """scan_surfaces with nonexistent files should return {}."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = pathlib.Path(tmpdir)
        results = isc.scan_surfaces(repo_root=tmp_path, surfaces=["a.md", "b.sh"])
        assert results == {}


def test_render_clean():
    """render({}) should say 'clean'."""
    output = isc.render({})
    assert "clean" in output


def test_render_findings():
    """render should include file, line, rule, and text."""
    findings = [{"line": 3, "rule": "burnless shell", "text": "burnless shell"}]
    output = isc.render({"x.md": findings})
    assert "x.md" in output
    assert "L3" in output
    assert "burnless shell" in output


def test_main_clean_returns_zero(monkeypatch):
    """main() should return 0 when scan_surfaces returns empty dict."""
    monkeypatch.setattr(isc, "scan_surfaces", lambda *a, **k: {})
    assert isc.main([]) == 0


def test_scan_text_multiline_pattern():
    """Verify scan_text processes multiple lines and line numbers are 1-based."""
    text = "line one\nburnless shell here\nline three"
    findings = isc.scan_text(text)
    assert len(findings) > 0
    assert findings[0]["line"] == 2


def test_allow_marker_case_insensitive():
    """Allow markers should be matched case-insensitively."""
    text = "LEGACY partner discussion"
    findings = isc.scan_text(text)
    assert findings == []


def test_chat_dash_id_suffix_variants():
    """Verify /chat-id and --chat-id are NOT flagged (regression)."""
    text1 = "resume --chat-id abc123"
    text2 = "call /chat-id xyz"
    assert isc.scan_text(text1) == []
    assert isc.scan_text(text2) == []


def test_scan_file_utf8():
    """scan_file should handle UTF-8 content."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = pathlib.Path(tmpdir) / "test.md"
        tmp_path.write_text("burnless shell\nπ symbol\n", encoding="utf-8")
        findings = isc.scan_file(tmp_path)
        assert len(findings) > 0
        assert findings[0]["rule"] == "burnless shell"


def test_forbidden_patterns_are_compiled():
    """FORBIDDEN list should contain compiled regex objects."""
    for name, rx in isc.FORBIDDEN:
        assert isinstance(rx, type(isc.re.compile("")))


def test_rtk_flagged_without_marker():
    """Bare 'rtk' on an active surface is flagged (rtk fully removed 2026-06-26)."""
    findings = isc.scan_text("prefix rtk to the agent command")
    assert any(f["rule"] == "rtk wrapper" for f in findings)


def test_rtk_suppressed_with_removed_marker():
    """An rtk mention on a line marked removed/historical is suppressed."""
    findings = isc.scan_text("rtk wrapper removed 2026-06-26")
    assert findings == []
