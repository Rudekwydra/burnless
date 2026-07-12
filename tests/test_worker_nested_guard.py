"""F10 — nested re-delegação guard.

Bloquear `burnless do/delegate` chamado de dentro de worker (re-delegação aninhada).
Worker carrega BURNLESS_WORKER=1 (agents.py:796, live_runner.py:433).
Este teste valida que cmd_do/cmd_delegate chequeiam esse env e bloqueiam re-delegação
sem opt-in explícito BURNLESS_ALLOW_NESTED=1.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from burnless import cli, paths as paths_mod


def _make_minimal_project(tmp_path: Path) -> Path:
    """Create minimal .burnless project structure."""
    root = tmp_path / ".burnless"
    p = paths_mod.paths_for(root)
    for key in ("delegations", "logs", "temp", "capsules", "archive", "chat", "runs"):
        p[key].mkdir(parents=True, exist_ok=True)
    return root


def _delegate_args() -> argparse.Namespace:
    """Minimal args for cmd_delegate."""
    return argparse.Namespace(
        text="test task",
        tier=None,
        goal=None,
        success=None,
        chain=None,
        force=True,
        allow_relative_paths=False,
        allow_unfenced_verify=False,
    )


def _do_args() -> argparse.Namespace:
    """Minimal args for cmd_do."""
    return argparse.Namespace(
        text="test task",
        tier=None,
        force=True,
        allow_relative_paths=False,
        allow_unfenced_verify=False,
        timeout=600,
        stale_timeout_s=None,
        cold_cache=False,
        diamond=None,
        gold=None,
        silver=None,
        bronze=None,
    )


class TestWorkerNestedGuard:
    def test_delegate_blocked_in_worker_context(self, tmp_path, monkeypatch, capsys):
        """cmd_delegate returns 7 when BURNLESS_WORKER=1 and BURNLESS_ALLOW_NESTED not set."""
        root = _make_minimal_project(tmp_path)
        monkeypatch.setattr(cli.paths_mod, "require_root", lambda: root)
        monkeypatch.setenv("BURNLESS_WORKER", "1")
        monkeypatch.delenv("BURNLESS_ALLOW_NESTED", raising=False)

        args = _delegate_args()
        result = cli.cmd_delegate(args)

        assert result == 7, f"Expected exit code 7, got {result}"
        captured = capsys.readouterr()
        assert "BURNLESS_ALLOW_NESTED=1" in captured.err, f"Expected nested-guard message in stderr, got: {captured.err}"

    def test_do_blocked_in_worker_context(self, tmp_path, monkeypatch, capsys):
        """cmd_do returns 7 when BURNLESS_WORKER=1 and BURNLESS_ALLOW_NESTED not set."""
        root = _make_minimal_project(tmp_path)
        monkeypatch.setattr(cli.paths_mod, "require_root", lambda: root)
        monkeypatch.setenv("BURNLESS_WORKER", "1")
        monkeypatch.delenv("BURNLESS_ALLOW_NESTED", raising=False)

        args = _do_args()
        result = cli.cmd_do(args)

        assert result == 7, f"Expected exit code 7, got {result}"
        captured = capsys.readouterr()
        assert "BURNLESS_ALLOW_NESTED=1" in captured.err, f"Expected nested-guard message in stderr, got: {captured.err}"

    def test_optin_passes_gate_delegate(self, tmp_path, monkeypatch):
        """cmd_delegate with BURNLESS_ALLOW_NESTED=1 passes gate and hits require_root."""
        root = _make_minimal_project(tmp_path)
        monkeypatch.setattr(cli.paths_mod, "require_root", lambda: _sentinel("delegate"))
        monkeypatch.setenv("BURNLESS_WORKER", "1")
        monkeypatch.setenv("BURNLESS_ALLOW_NESTED", "1")

        args = _delegate_args()
        try:
            cli.cmd_delegate(args)
            assert False, "Expected RuntimeError sentinel"
        except RuntimeError as e:
            assert str(e) == "delegate", f"Expected sentinel 'delegate', got {e}"

    def test_optin_passes_gate_do(self, tmp_path, monkeypatch):
        """cmd_do with BURNLESS_ALLOW_NESTED=1 passes gate and hits require_root."""
        root = _make_minimal_project(tmp_path)
        monkeypatch.setattr(cli.paths_mod, "require_root", lambda: _sentinel("do"))
        monkeypatch.setenv("BURNLESS_WORKER", "1")
        monkeypatch.setenv("BURNLESS_ALLOW_NESTED", "1")

        args = _do_args()
        try:
            cli.cmd_do(args)
            assert False, "Expected RuntimeError sentinel"
        except RuntimeError as e:
            assert str(e) == "do", f"Expected sentinel 'do', got {e}"

    def test_normal_context_passes_gate_delegate(self, tmp_path, monkeypatch):
        """cmd_delegate without BURNLESS_WORKER passes gate and hits require_root."""
        root = _make_minimal_project(tmp_path)
        monkeypatch.setattr(cli.paths_mod, "require_root", lambda: _sentinel("normal_delegate"))
        monkeypatch.delenv("BURNLESS_WORKER", raising=False)

        args = _delegate_args()
        try:
            cli.cmd_delegate(args)
            assert False, "Expected RuntimeError sentinel"
        except RuntimeError as e:
            assert str(e) == "normal_delegate", f"Expected sentinel 'normal_delegate', got {e}"

    def test_normal_context_passes_gate_do(self, tmp_path, monkeypatch):
        """cmd_do without BURNLESS_WORKER passes gate and hits require_root."""
        root = _make_minimal_project(tmp_path)
        monkeypatch.setattr(cli.paths_mod, "require_root", lambda: _sentinel("normal_do"))
        monkeypatch.delenv("BURNLESS_WORKER", raising=False)

        args = _do_args()
        try:
            cli.cmd_do(args)
            assert False, "Expected RuntimeError sentinel"
        except RuntimeError as e:
            assert str(e) == "normal_do", f"Expected sentinel 'normal_do', got {e}"


def _sentinel(label: str):
    """Helper to raise a sentinel exception."""
    raise RuntimeError(label)
