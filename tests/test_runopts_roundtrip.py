"""Tests for RunOpts roundtrip: CLI flags -> RunOpts -> backend decisions.

Regression suite for P10/7: flags --maestro/--no-maestro/--no-cache-worker
were defined in the CLI but silently ignored because they were never copied
into the RunOpts dataclass. This suite ensures:
1. Every flag in the `run` subparser reaches RunOpts (prevents silent regressions)
2. maestro flag reaches _should_use_maestro_backend logic
3. no_cache_worker flag reaches _should_use_cached_worker logic
"""
from __future__ import annotations

import argparse
import dataclasses
from unittest.mock import patch

import pytest

from burnless import cli
from burnless.exec.runner import (
    RunOpts,
    _should_use_maestro_backend,
    _should_use_cached_worker,
)


def _find_run_subparser(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Extract the 'run' subparser from the main parser."""
    for action in parser._subparsers._group_actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices.get("run")
    raise ValueError("Could not find 'run' subparser")


class TestRunOptRoundtrip:
    def test_every_run_flag_reaches_runopts(self):
        """Regression test: every flag in the `run` subparser must appear in RunOpts.

        This test constructs the real CLI parser, locates the `run` subparser,
        collects all action dest values (except 'help' and allowed exceptions),
        and asserts that each one is a field in the RunOpts dataclass.

        If someone adds a new --flag to the run subparser without adding a field
        to RunOpts, this test will fail and name the flag.
        """
        parser = cli.build_parser()
        run_parser = _find_run_subparser(parser)

        # Dests that are handled before RunOpts or are not RunOpts fields:
        # - 'func': set by set_defaults, not a RunOpts param
        # - 'cmd': subparser dest, not a RunOpts param
        ALLOWLIST = {"func", "cmd"}

        # Collect all action dests from the run subparser
        all_dests = set()
        for action in run_parser._actions:
            if action.dest == "help":
                continue
            if action.dest in ALLOWLIST:
                continue
            all_dests.add(action.dest)

        # Get all RunOpts fields
        runopts_fields = {f.name for f in dataclasses.fields(RunOpts)}

        # Check: every dest is in RunOpts
        missing_in_runopts = all_dests - runopts_fields
        assert (
            not missing_in_runopts
        ), f"Flags defined in run subparser but missing from RunOpts: {sorted(missing_in_runopts)}"

    def test_maestro_flag_reaches_backend_decision(self):
        """Verify that maestro=True in RunOpts makes _should_use_maestro_backend return True."""
        opts = RunOpts(id="d001", maestro=True)
        cfg = {"maestro": {}}  # maestro section exists (makes tier-check pass)
        tier = "silver"

        result = _should_use_maestro_backend(opts, cfg, tier)
        assert result is True, "maestro flag should enable maestro backend"

    def test_no_maestro_flag_overrides_config(self):
        """Verify that no_maestro=True in RunOpts overrides config."""
        opts = RunOpts(id="d001", no_maestro=True)
        cfg = {"maestro": {"run_backend": True}}  # config says to use maestro
        tier = "silver"

        result = _should_use_maestro_backend(opts, cfg, tier)
        assert (
            result is False
        ), "no_maestro flag should disable maestro backend even if config enables it"

    def test_no_cache_worker_flag_respected(self):
        """Verify that no_cache_worker=True in RunOpts disables cached_worker."""
        opts = RunOpts(id="d001", no_cache_worker=True)
        cfg = {"cache_worker": {"enabled": True}}  # config says to use cached_worker
        tier = "silver"
        api_key = "sk-test"

        # Mock cached_worker.is_available to return True
        with patch("burnless.cached_worker.is_available", return_value=True):
            result = _should_use_cached_worker(opts, cfg, tier, api_key)
        assert (
            result is False
        ), "no_cache_worker flag should disable cached_worker even if config enables it"

    def test_maestro_defaults_to_false(self):
        """Verify default behavior: without flags and without config, maestro is OFF."""
        opts = RunOpts(id="d001")  # maestro, no_maestro, no_cache_worker all default to False
        cfg = {}  # no maestro config
        tier = "silver"

        result = _should_use_maestro_backend(opts, cfg, tier)
        assert result is False, "maestro should be OFF by default"

    def test_cache_worker_disabled_without_config(self):
        """Verify default behavior: cache_worker is OFF without explicit config enabling it."""
        opts = RunOpts(id="d001")  # no_cache_worker defaults to False
        cfg = {}  # no cache_worker config
        tier = "silver"
        api_key = "sk-test"

        with patch("burnless.cached_worker.is_available", return_value=True):
            result = _should_use_cached_worker(opts, cfg, tier, api_key)
        assert result is False, "cache_worker should be OFF without config.cache_worker.enabled=true"
