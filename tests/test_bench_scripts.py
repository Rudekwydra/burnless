"""Structural tests for bench/*.py scripts that make real provider calls.

These scripts are not unit-testable in the normal sense (mocking real cache
behavior would be exactly the synthetic benchmark the design doc rejects —
docs/plans/2026-07-21-ask-control-plane-dogfood-handoff.md sec 14). Instead
this file proves the CLI is wired and exercises the pure classification logic
with hand-built fake envelope data, without ever invoking a real provider.
"""
from __future__ import annotations

import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bench"))

import prefix_cache_bench  # noqa: E402

BENCH_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "bench", "prefix_cache_bench.py")


def test_prefix_cache_bench_argparse_wires_flags():
    proc = subprocess.run(
        [sys.executable, BENCH_SCRIPT, "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0
    assert "--tier" in proc.stdout
    assert "--runs" in proc.stdout
    assert "--prefix-text" in proc.stdout


class TestClassifyPrefixCacheResult:
    def test_supported_when_capable_and_hit_observed(self):
        result = prefix_cache_bench.classify_prefix_cache_result(True, [0, 512, 0])
        assert result == "supported"

    def test_unsupported_when_not_capable(self):
        result = prefix_cache_bench.classify_prefix_cache_result(False, [0, 0, 0])
        assert result == "unsupported"

    def test_unobservable_when_capable_but_no_hit_observed(self):
        result = prefix_cache_bench.classify_prefix_cache_result(True, [0, 0, 0])
        assert result == "unobservable"
