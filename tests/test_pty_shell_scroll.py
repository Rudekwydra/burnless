"""Tests for pty_shell regex + helper functions (no real PTY spawn)."""
import re
from pathlib import Path
import tempfile
import json

# Recreate the regex patterns used in pty_shell._run_pty so we can unit-test
# the matching logic without spawning a real PTY.
_SCROLL_RE = re.compile(rb"\x1b\[[\d;]*r")
_CLEAR_RE = re.compile(rb"\x1b\[H\x1b\[[23]J|\x1b\[[23]J")


def test_scroll_re_matches_basic():
    assert _SCROLL_RE.search(b"\x1b[1;50r")
    assert _SCROLL_RE.search(b"prefix\x1b[r suffix")


def test_scroll_re_does_not_match_clear():
    assert _SCROLL_RE.search(b"\x1b[2J") is None
    assert _SCROLL_RE.search(b"\x1b[H") is None


def test_clear_re_matches_2J():
    assert _CLEAR_RE.search(b"\x1b[2J")
    assert _CLEAR_RE.search(b"prefix\x1b[2J suffix")


def test_clear_re_matches_3J():
    assert _CLEAR_RE.search(b"\x1b[3J")


def test_clear_re_matches_combined_home_then_clear():
    assert _CLEAR_RE.search(b"\x1b[H\x1b[2J")
    assert _CLEAR_RE.search(b"\x1b[H\x1b[3J")


def test_clear_re_does_not_match_plain_text():
    assert _CLEAR_RE.search(b"hello world") is None
    assert _CLEAR_RE.search(b"\x1b[33myellow\x1b[0m") is None


def test_read_metrics_returns_zero_when_missing():
    from burnless.pty_shell import _read_metrics
    tokens, dels = _read_metrics(Path("/tmp/nonexistent_metrics_12345.json"))
    assert tokens == 0
    assert dels == 0


def test_read_metrics_parses_valid_file():
    from burnless.pty_shell import _read_metrics
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"burnless_tokens": 12345, "delegation_counter": 7}, f)
        path = Path(f.name)
    try:
        tokens, dels = _read_metrics(path)
        assert tokens == 12345
        assert dels == 7
    finally:
        path.unlink()


def test_status_line_format():
    from burnless.pty_shell import _status_line
    line = _status_line(1234, 5, "claude", "hint")
    assert "1,234 burnless tokens" in line
    assert "5 delegations" in line
    assert "claude" in line
    assert "hint" in line


def test_status_line_no_hint():
    from burnless.pty_shell import _status_line
    line = _status_line(0, 0, "codex", "")
    assert "0 burnless tokens" in line
    assert "codex" in line
    assert line.endswith("codex")
