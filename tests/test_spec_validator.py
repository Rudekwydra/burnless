from pathlib import Path

from burnless import spec_validator as sv


def test_relative_path_flagged():
    r = sv.validate_spec_paths("edit src/burnless/cli.py to add a flag")
    assert not r.ok
    assert r.offending == ["src/burnless/cli.py"]


def test_absolute_path_ok():
    r = sv.validate_spec_paths("edit /Users/x/proj/src/cli.py")
    assert r.ok
    assert r.offending == []


def test_home_path_ok():
    r = sv.validate_spec_paths("edit ~/proj/src/foo.py")
    assert r.ok


def test_url_not_flagged():
    r = sv.validate_spec_paths("see https://example.com/docs/page.html for details")
    assert r.ok


def test_prose_without_paths_ok():
    r = sv.validate_spec_paths("implement the feature and add tests")
    assert r.ok


def test_bare_filename_not_flagged():
    r = sv.validate_spec_paths("update cli.py with the new logic")
    assert r.ok


def test_dot_slash_normalized():
    r = sv.validate_spec_paths("touch ./src/foo.py please")
    assert not r.ok
    assert r.offending == ["src/foo.py"]


def test_dedup():
    r = sv.validate_spec_paths("edit src/a.py then re-read src/a.py")
    assert r.offending == ["src/a.py"]


def test_multiple_paths():
    r = sv.validate_spec_paths("edit src/a.py and tests/test_a.py")
    assert set(r.offending) == {"src/a.py", "tests/test_a.py"}


def test_format_rejection_lists_paths():
    r = sv.validate_spec_paths("edit src/a.py")
    msg = sv.format_rejection(r, Path("/Users/x/proj"), "en")
    assert "/Users/x/proj/src/a.py" in msg
    assert "--allow-relative-paths" in msg


def test_autofix_rewrites_relative_path():
    """2026-07-02: bare relative-path mentions in prose (a whole class of
    footgun that kept blocking real dispatches this session) get rewritten
    to absolute in place instead of hard-blocking the whole delegation."""
    text = "edit src/burnless/cli.py to add a flag"
    fixed, rewritten = sv.autofix_relative_paths(text, Path("/Users/x/proj"))
    assert rewritten == ["src/burnless/cli.py"]
    assert fixed == "edit /Users/x/proj/src/burnless/cli.py to add a flag"
    assert sv.validate_spec_paths(fixed).ok


def test_autofix_noop_when_no_offenders():
    text = "edit /Users/x/proj/src/cli.py"
    fixed, rewritten = sv.autofix_relative_paths(text, Path("/Users/x/proj"))
    assert rewritten == []
    assert fixed == text


def test_autofix_rewrites_multiple_occurrences():
    text = "read src/a.py first, then edit src/a.py again"
    fixed, rewritten = sv.autofix_relative_paths(text, Path("/Users/x/proj"))
    assert rewritten == ["src/a.py"]
    assert fixed.count("/Users/x/proj/src/a.py") == 2
    assert sv.validate_spec_paths(fixed).ok


def test_autofix_rewrites_multiple_distinct_paths():
    text = "edit src/a.py and tests/test_a.py"
    fixed, rewritten = sv.autofix_relative_paths(text, Path("/Users/x/proj"))
    assert set(rewritten) == {"src/a.py", "tests/test_a.py"}
    assert "/Users/x/proj/src/a.py" in fixed
    assert "/Users/x/proj/tests/test_a.py" in fixed
    assert sv.validate_spec_paths(fixed).ok


def test_format_autofix_notice_lists_rewritten_paths():
    msg = sv.format_autofix_notice(["src/a.py"], Path("/Users/x/proj"), "en")
    assert "AUTOFIX" in msg
    assert "/Users/x/proj/src/a.py" in msg
    assert "--allow-relative-paths" in msg
