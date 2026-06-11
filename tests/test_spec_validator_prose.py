from burnless.spec_validator import validate_spec_paths


def test_relative_prose_echo_of_absolute_is_ok():
    text = (
        "Edit /Users/roberto/antigravity/burnless/tests/foo.py. "
        "The tests/foo.py file holds the test."
    )
    result = validate_spec_paths(text)
    assert result.ok is True, f"Expected ok=True, offending={result.offending}"


def test_relative_only_is_blocked():
    text = "Edit tests/foo.py please."
    result = validate_spec_paths(text)
    assert result.ok is False
    assert "tests/foo.py" in result.offending
