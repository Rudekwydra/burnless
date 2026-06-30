"""Test codex provider warm-fork isolation from claude-only flags.

Per [[rule-provider-warm-isolation]]: codex commands MUST NEVER receive
claude-only flags, even if warm_args injection or warm module mismatches occur.
"""
import sys
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

import pytest


@pytest.fixture
def burnless_root_tmp(tmp_path):
    """Temporary burnless root for testing."""
    root = tmp_path / ".burnless"
    root.mkdir(parents=True, exist_ok=True)
    return root


def test_codex_never_gets_claude_flags(burnless_root_tmp):
    """Assert that codex commands NEVER receive claude-only flags.

    Scenario: _inject_warm_fork_args is called with a codex command.
    Even if the warm module returns claude-only flags (e.g., via misconfiguration
    or initialization bug), _strip_claude_only_flags must remove all of them.

    Expected: Result contains ZERO claude-only flags.
    """
    from burnless import agents

    codex_cmd = ["codex", "exec", "--model", "gpt-4", "--"]
    cwd = burnless_root_tmp.parent

    # Mock the warm module to return claude-only flags (simulating the bug).
    claude_only_flags = [
        "--no-session-persistence",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--exclude-dynamic-system-prompt-sections",
        "--setting-sources", "~/.config/claude",
        "--append-system-prompt", "test",
    ]

    with patch("importlib.import_module") as mock_import:
        mock_ws = MagicMock()
        mock_ws.PROVIDER = "codex"  # Correct provider match.
        mock_ws.warm_args = Mock(return_value=claude_only_flags)
        mock_ws.warm_prefix = Mock(return_value="")
        mock_ws.worker_cwd = Mock(return_value=None)
        mock_import.return_value = mock_ws

        result_parts, _, _ = agents._inject_warm_fork_args(codex_cmd, cwd)

    # Assert that claude-only flags are gone.
    result_str = " ".join(result_parts)
    forbidden = [
        "--no-session-persistence",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--exclude-dynamic-system-prompt-sections",
        "--setting-sources",
        "--append-system-prompt",
    ]
    for flag in forbidden:
        assert flag not in result_str, (
            f"claude-only flag {flag!r} found in codex result: {result_parts}"
        )


def test_codex_empty_warm_falls_cold(burnless_root_tmp):
    """Assert that empty/failed warm args fall back to COLD silently.

    Scenario: _inject_warm_fork_args is called with a codex command.
    The warm module's warm_args returns empty/None, and init() fails.

    Expected: Result is the original codex command + dedup, NO extra flags,
    NO exception raised.
    """
    from burnless import agents

    codex_cmd = ["codex", "exec", "--model", "gpt-4", "--", "script.sh"]
    cwd = burnless_root_tmp.parent

    with patch("importlib.import_module") as mock_import:
        mock_ws = MagicMock()
        mock_ws.PROVIDER = "codex"
        mock_ws.warm_args = Mock(return_value=[])  # Empty warm args.
        mock_ws.init = Mock(side_effect=Exception("init failed"))
        mock_import.return_value = mock_ws

        # Should NOT raise; should return COLD fallback.
        result_parts, warm_prefix, iso_cwd = agents._inject_warm_fork_args(
            codex_cmd, cwd
        )

    # Assert silent fallback: result is codex_cmd deduped, no extra flags.
    assert result_parts[:3] == ["codex", "exec", "--model"]  # Original structure preserved.
    assert warm_prefix == ""
    assert iso_cwd is None


def test_codex_provider_mismatch_falls_cold(burnless_root_tmp):
    """Assert that provider mismatch (warm module PROVIDER != detected provider) falls back to COLD.

    Scenario: _inject_warm_fork_args detects provider='codex', but the warm
    module has PROVIDER='claude' (e.g., due to resolver bug or cache corruption).

    Expected: Result is the original command unchanged, NO extra flags, silent.
    """
    from burnless import agents

    codex_cmd = ["codex", "exec", "--model", "gpt-4"]
    cwd = burnless_root_tmp.parent

    with patch("importlib.import_module") as mock_import:
        mock_ws = MagicMock()
        mock_ws.PROVIDER = "claude"  # MISMATCH: expecting 'codex'.
        mock_ws.warm_args = Mock(return_value=["--resume", "uuid", "--fork-session"])
        mock_import.return_value = mock_ws

        result_parts, warm_prefix, iso_cwd = agents._inject_warm_fork_args(
            codex_cmd, cwd
        )

    # Assert provider mismatch triggers COLD fallback.
    assert result_parts == codex_cmd
    assert warm_prefix == ""
    assert iso_cwd is None


def test_codex_strip_claude_flags_comprehensive(burnless_root_tmp):
    """Comprehensive test: _strip_claude_only_flags removes all claude-only flags and their values."""
    from burnless import agents

    # Simulate a mixed flag list (claude + codex).
    mixed_flags = [
        "codex",
        "exec",
        "--no-session-persistence",
        "--model", "gpt-4",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--exclude-dynamic-system-prompt-sections",
        "--sandbox", "read-only",
        "--setting-sources", "~/.config/claude",
        "--append-system-prompt", "test prompt",
        "--verbose",
        "--cd", "/tmp",
    ]

    result = agents._strip_claude_only_flags(mixed_flags)

    # Expected: only ["codex", "exec", "--model", "gpt-4", "--sandbox", "read-only", "--cd", "/tmp"]
    assert "codex" in result
    assert "exec" in result
    assert "--model" in result
    assert "gpt-4" in result
    assert "--sandbox" in result
    assert "read-only" in result
    assert "--cd" in result
    assert "/tmp" in result

    # Assert claude-only flags are gone.
    assert "--no-session-persistence" not in result
    assert "--strict-mcp-config" not in result
    assert "--disable-slash-commands" not in result
    assert "--exclude-dynamic-system-prompt-sections" not in result
    assert "--setting-sources" not in result
    assert "~/.config/claude" not in result
    assert "--append-system-prompt" not in result
    assert "test prompt" not in result
    assert "--verbose" not in result
