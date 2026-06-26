"""Tests for no_proxy_real_flow harness."""
import importlib.util
import json
import pathlib
import subprocess
import sys

import pytest

_p = pathlib.Path(__file__).resolve().parent / "no_proxy_real_flow.py"
_spec = importlib.util.spec_from_file_location("no_proxy_real_flow", _p)
nprf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nprf)


def test_build_plan_has_four_scenarios():
    plan = nprf.build_plan("t")
    assert len(plan["scenarios"]) == 4
    names = [s["name"] for s in plan["scenarios"]]
    assert names == ["raw", "observe", "on", "cli_do"]


def test_build_plan_metrics_complete():
    plan = nprf.build_plan("t")
    expected_metrics = {
        "input_tokens",
        "output_tokens",
        "cache_read",
        "cache_write",
        "assistant_turns",
        "worker_delegations",
        "retrieval_calls",
        "verify_pass_fail",
        "wall_time",
        "successful_completion",
        "user_visible_verbosity",
        "post_clear_recovery",
    }
    assert expected_metrics.issubset(set(plan["metrics"]))
    for scenario in plan["scenarios"]:
        assert scenario["metrics"] == plan["metrics"]


def test_build_plan_is_pure():
    plan1 = nprf.build_plan("t")
    plan2 = nprf.build_plan("t")
    assert plan1 == plan2

    orig_metrics = list(nprf.METRICS)
    plan1["metrics"].append("fake_metric")
    assert nprf.METRICS == orig_metrics


def test_dry_run_no_subprocess(monkeypatch, capsys):
    subprocess_called = []

    def fake_run(*args, **kwargs):
        subprocess_called.append(True)
        raise AssertionError("subprocess should not be called during --dry-run")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(nprf._sys, "argv", ["prog", "--task", "t", "--dry-run", "--json"])

    exit_code = nprf.main()
    assert exit_code == 0
    assert not subprocess_called

    captured = capsys.readouterr()
    output_json = json.loads(captured.out)
    assert "scenarios" in output_json
    assert len(output_json["scenarios"]) == 4


def test_caveats_present():
    plan = nprf.build_plan("t")
    assert plan["caveats"]
    assert len(plan["caveats"]) > 0
    assert all(isinstance(c, str) for c in plan["caveats"])
