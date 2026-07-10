from burnless.spec_validator import (
    find_verify_command_substitution,
    should_block_verify_command_substitution,
    format_command_substitution_rejection,
)


def test_find_backtick_in_verify_block():
    """Detects a backtick in a ## Verify fenced block."""
    spec = """
## Verify
```sh
grep -q `whoami` file.txt
echo done
```
"""
    offending = find_verify_command_substitution(spec)
    assert len(offending) > 0
    assert any("`whoami`" in line for line in offending)


def test_find_dollar_paren_in_verify_block():
    """Detects $(...) in a ## Verify fenced block."""
    spec = """
## Verify
```sh
test "$(cat f.txt)" = "expected"
```
"""
    offending = find_verify_command_substitution(spec)
    assert len(offending) > 0
    assert any("$(" in line for line in offending)


def test_no_command_substitution_in_clean_verify():
    """Clean verify block with only greps/tests returns empty list."""
    spec = """
## Verify
```sh
grep -q "pattern" file.txt
test -f file.txt
pytest tests/ -q
```
"""
    offending = find_verify_command_substitution(spec)
    assert offending == []


def test_should_block_when_backtick_present():
    """should_block returns True when backtick is detected."""
    spec = """
## Verify
```sh
grep `echo hello` file.txt
```
"""
    assert should_block_verify_command_substitution(spec) is True


def test_should_block_when_dollar_paren_present():
    """should_block returns True when $(...) is detected."""
    spec = """
## Verify
```sh
test "$(python3 script.py)" = "output"
```
"""
    assert should_block_verify_command_substitution(spec) is True


def test_should_not_block_clean_verify():
    """should_block returns False when no command substitution."""
    spec = """
## Verify
```sh
grep pattern file.txt
pytest -q
```
"""
    assert should_block_verify_command_substitution(spec) is False


def test_should_not_block_without_verify_section():
    """should_block returns False when no ## Verify section."""
    spec = """
## Summary
Some description
"""
    assert should_block_verify_command_substitution(spec) is False


def test_format_rejection_pt_br():
    """format_command_substitution_rejection returns PT-BR message by default."""
    offending = ["grep `whoami` file.txt", "test $(cat f.txt)"]
    msg = format_command_substitution_rejection(offending, lang="pt-BR")
    assert "[BLOCK]" in msg
    assert "burnless" in msg
    assert "backtick" in msg or "command-substitution" in msg
    assert "grep `whoami` file.txt" in msg
    assert "test $(cat f.txt)" in msg


def test_format_rejection_en():
    """format_command_substitution_rejection returns EN message when lang=en."""
    offending = ["grep `whoami` file.txt"]
    msg = format_command_substitution_rejection(offending, lang="en")
    assert "[BLOCK]" in msg
    assert "backtick" in msg.lower() or "command substitution" in msg.lower()
    assert "grep `whoami` file.txt" in msg


def test_multiple_violations_all_reported():
    """All offending lines are included in the returned list."""
    spec = """
## Verify
```sh
grep `bad1` f1.txt
test `bad2` = x
echo normal line
out=$(python3 script.py)
```
"""
    offending = find_verify_command_substitution(spec)
    assert len(offending) == 3
    assert any("bad1" in line for line in offending)
    assert any("bad2" in line for line in offending)
    assert any("out=$(python3" in line for line in offending)
    # "echo normal line" should NOT be in offending
    assert not any("echo normal line" in line for line in offending)
