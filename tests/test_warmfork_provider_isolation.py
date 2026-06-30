"""Tests for warm-fork provider isolation: ensuring claude-only flags don't leak into codex commands."""

from burnless.agents import _strip_claude_only_flags


def test_strip_claude_flags_valueless():
    """Remove valueless claude-only flags, preserve others."""
    input_flags = ["--cd", "/x", "--no-session-persistence", "--ignore-rules"]
    result = _strip_claude_only_flags(input_flags)
    assert result == ["--cd", "/x", "--ignore-rules"]


def test_strip_claude_flags_valued():
    """Remove claude-only flags with values (flag + next token), preserve others."""
    input_flags = ["--cd", "/x", "--setting-sources", "project,local", "--ignore-rules"]
    result = _strip_claude_only_flags(input_flags)
    assert result == ["--cd", "/x", "--ignore-rules"]


def test_strip_multiple_valued_flags():
    """Remove multiple valued claude-only flags."""
    input_flags = [
        "--append-system-prompt",
        "some text",
        "--output-format",
        "json",
        "--cd",
        "/root",
    ]
    result = _strip_claude_only_flags(input_flags)
    assert result == ["--cd", "/root"]


def test_strip_all_claude_flag_types():
    """Remove all variants of claude-only flags."""
    input_flags = [
        "--no-session-persistence",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--exclude-dynamic-system-prompt-sections",
        "--verbose",
        "--include-partial-messages",
        "--permission-mode",
        "restricted",
        "--allowedTools",
        "tool1,tool2",
    ]
    result = _strip_claude_only_flags(input_flags)
    assert result == []


def test_strip_mixed_with_preservations():
    """Preserve non-claude flags while removing claude-only flags."""
    input_flags = [
        "--model",
        "claude-opus",
        "--no-session-persistence",
        "--cd",
        "/workspace",
        "--setting-sources",
        "local",
        "--sandbox",
        "read-only",
        "--strict-mcp-config",
    ]
    result = _strip_claude_only_flags(input_flags)
    assert result == ["--model", "claude-opus", "--cd", "/workspace", "--sandbox", "read-only"]


def test_strip_preserves_value_flags():
    """Preserve non-claude flags with values like --model, --cd, --sandbox."""
    input_flags = [
        "--model",
        "claude-opus",
        "--cd",
        "/home/user",
        "--sandbox",
        "danger-full-access",
    ]
    result = _strip_claude_only_flags(input_flags)
    assert result == [
        "--model",
        "claude-opus",
        "--cd",
        "/home/user",
        "--sandbox",
        "danger-full-access",
    ]


def test_strip_edge_case_valued_flag_at_end():
    """Handle valued claude flag at end of list (no next token to consume)."""
    input_flags = [
        "--cd",
        "/x",
        "--permission-mode",
    ]
    result = _strip_claude_only_flags(input_flags)
    assert result == ["--cd", "/x"]


def test_strip_empty_input():
    """Handle empty flag list."""
    result = _strip_claude_only_flags([])
    assert result == []


def test_codex_command_has_no_claude_flags():
    """Verify that a typical codex command with injected claude flags strips them correctly."""
    codex_extra = [
        "--no-session-persistence",
        "--setting-sources",
        "project",
        "--cd",
        "/workspace",
        "--ignore-rules",
    ]
    result = _strip_claude_only_flags(codex_extra)
    _CLAUDE_ONLY_FLAGS = {
        "--no-session-persistence",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--exclude-dynamic-system-prompt-sections",
        "--setting-sources",
        "--append-system-prompt",
        "--output-format",
        "--include-partial-messages",
        "--verbose",
        "--permission-mode",
        "--allowedTools",
    }
    for flag in result:
        if flag.startswith("--"):
            assert flag not in _CLAUDE_ONLY_FLAGS, f"Flag {flag} should have been removed"
