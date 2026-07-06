"""Regression guard: the suite must never reach the real macOS `security`.

If it does, tests that spawn the claude CLI (or burnless' own OAuth read) pop a
blocking "Keychain Not Found" modal under the isolated test $HOME. conftest
prepends a no-op shim to PATH; this pins that it stays in effect.
"""
from __future__ import annotations

import os
import shutil
import subprocess


def test_security_binary_is_shimmed_during_tests():
    if os.environ.get("BURNLESS_ALLOW_KEYCHAIN") == "1":
        return
    resolved = shutil.which("security")
    assert resolved is not None, "no `security` on PATH at all"
    assert "burnless_test_shims" in resolved, (
        f"real `security` resolved ({resolved}); a spawned subprocess would pop a "
        "macOS Keychain modal. conftest must prepend the shim to PATH."
    )


def test_shimmed_security_returns_cleanly_without_prompting():
    if os.environ.get("BURNLESS_ALLOW_KEYCHAIN") == "1":
        return
    # The exact call the claude CLI / subscription_usage make — must not hang or
    # prompt; the shim returns immediately.
    r = subprocess.run(
        ["security", "find-generic-password", "-a", "tester", "-w", "-s", "Claude Code"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert r.returncode == 0
    assert r.stdout.strip() == ""
