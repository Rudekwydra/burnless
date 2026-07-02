"""Tests for valueless flag deduplication in agents._dedup_valueless_flags."""
import inspect
import pytest
from burnless.agents import _dedup_valueless_flags
from burnless import live_runner


def test_dedup_removes_second_skip_git():
    """Removes second occurrence of --skip-git-repo-check, preserves first."""
    input_parts = [
        "codex", "exec", "--skip-git-repo-check",
        "--sandbox", "danger-full-access",
        "--cd", "/x",
        "--skip-git-repo-check", "--ignore-rules"
    ]
    result = _dedup_valueless_flags(input_parts)

    assert result.count("--skip-git-repo-check") == 1
    assert "--ignore-rules" in result
    assert "--sandbox" in result
    assert "danger-full-access" in result
    assert "--cd" in result
    assert "/x" in result

    skip_git_idx = result.index("--skip-git-repo-check")
    sandbox_idx = result.index("--sandbox")
    assert skip_git_idx < sandbox_idx


def test_dedup_noop_when_unique():
    """Returns identical list when no duplicates present."""
    input_parts = ["codex", "exec", "--skip-git-repo-check", "--cd", "/tmp"]
    result = _dedup_valueless_flags(input_parts)

    assert result == input_parts


def test_dedup_preserves_valued_flag_values():
    """Preserves value-taking flags (--cd, --sandbox, etc.) even when repeated."""
    input_parts = [
        "codex", "exec",
        "--cd", "/a",
        "--skip-git-repo-check",
        "--cd", "/b",
        "--model", "claude-opus-4-1",
        "--sandbox", "read-only",
        "--model", "claude-sonnet-4-1"
    ]
    result = _dedup_valueless_flags(input_parts)

    assert result.count("--cd") == 2
    assert result.count("--model") == 2
    assert result.count("--skip-git-repo-check") == 1

    assert "/a" in result
    assert "/b" in result
    assert "claude-opus-4-1" in result
    assert "claude-sonnet-4-1" in result


def test_dedup_multiple_valueless_flags():
    """Deduplicates multiple different valueless flags."""
    input_parts = [
        "codex", "exec",
        "--skip-git-repo-check",
        "--ignore-user-config",
        "--skip-git-repo-check",
        "--full-auto",
        "--ignore-rules",
        "--ignore-user-config",
        "--full-auto"
    ]
    result = _dedup_valueless_flags(input_parts)

    assert result.count("--skip-git-repo-check") == 1
    assert result.count("--ignore-user-config") == 1
    assert result.count("--full-auto") == 1
    assert result.count("--ignore-rules") == 1


def test_live_runner_dedups_final_command():
    """Regression (2026-07-02): run_with_live_panel appended codex warm/iso-cwd
    flags (--cd, --skip-git-repo-check, --ignore-user-config, --ignore-rules)
    onto a base command that already had --skip-git-repo-check, with no dedup
    call — codex CLI then rejected the duplicate flag and every codex delegation
    errored. _dedup_valueless_flags existed and was unit-tested, but nothing
    asserted it actually ran in run_with_live_panel's command pipeline. This
    pins the wiring, not just the helper."""
    source = inspect.getsource(live_runner.run_with_live_panel)
    assert "_dedup_valueless_flags(command)" in source
