from burnless.exec.runner import _verify_badge


def test_ok_with_verify_marker():
    summary = {
        "status": "OK",
        "validated": ["verify: 4/4 checks passed"],
    }
    result = _verify_badge(summary)
    assert result.startswith("✓")
    assert "(4/4)" in result


def test_ok_without_verify_marker():
    summary = {
        "status": "OK",
        "validated": ["some other item"],
    }
    result = _verify_badge(summary)
    assert "unverified" in result


def test_part_returns_empty():
    summary = {
        "status": "PART",
        "validated": ["verify: 3/4 checks passed"],
    }
    assert _verify_badge(summary) == ""
