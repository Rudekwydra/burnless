from burnless.pty_shell import _status_line


def test_status_line_burst_appends_plus_n():
    line = _status_line(1000, 5, "claude", "hint text", burst_delta=42)
    assert "+42" in line
    assert "1,000 burnless tokens" in line
    assert "hint text" in line


def test_status_line_no_burst_when_zero():
    line = _status_line(1000, 5, "claude", "hint", burst_delta=0)
    assert "+0" not in line
    assert "1,000 burnless tokens" in line


def test_status_line_burst_without_hint():
    line = _status_line(500, 2, "codex", "", burst_delta=10)
    assert "+10" in line
    assert "500 burnless tokens" in line


def test_status_line_default_burst_delta_zero():
    # Backward compatibility: callers without burst_delta arg
    line = _status_line(100, 1, "claude")
    assert "+" not in line
    assert "100 burnless tokens" in line


def test_pro_tips_exists_and_nonempty():
    import importlib, inspect
    src = inspect.getsource(importlib.import_module("burnless.pty_shell"))
    assert "_PRO_TIPS" in src
    # Should mention "Pro" at least once (the tips advertise it)
    assert "Pro:" in src
