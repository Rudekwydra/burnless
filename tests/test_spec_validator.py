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
