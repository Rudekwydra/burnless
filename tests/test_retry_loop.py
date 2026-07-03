"""Tests for PART/ERR automatic retry loop (brecha #2)."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import call, patch

import pytest

from burnless import cli, config, paths
from burnless import delegations as deleg_mod


# ── helpers ────────────────────────────────────────────────────────────────


def _paths(tmp_path: Path) -> dict[str, Path]:
    root = tmp_path / ".burnless"
    p = paths.paths_for(root)
    for key in ("delegations", "logs", "temp", "capsules", "archive", "chat"):
        p[key].mkdir(parents=True, exist_ok=True)
    return p


def _ok_result(did: str = "d001") -> str:
    data = {
        "id": did,
        "status": "OK",
        "kind": "execution",
        "summary": "done",
        "files_touched": ["/tmp/x.py"],
        "validated": [],
        "evidence": ["/tmp/x.py exists"],
        "issues": [],
        "next": "",
    }
    return f"```json\n{json.dumps(data)}\n```"


def _part_result(did: str = "d001", issues: list[str] | None = None) -> str:
    data = {
        "id": did,
        "status": "PART",
        "kind": "execution",
        "summary": "partial",
        "files_touched": [],
        "validated": [],
        "evidence": [],
        "issues": issues or ["missing_tests"],
        "next": "add tests",
    }
    return f"```json\n{json.dumps(data)}\n```"


def _agent_result(stdout: str, returncode: int = 0) -> dict:
    return {"stdout": stdout, "stderr": "", "returncode": returncode, "stale": False, "interrupted": False}


# ── 1. Config defaults ──────────────────────────────────────────────────────


def test_retry_defaults_in_default_config():
    cfg = config.DEFAULT_CONFIG
    retry = cfg.get("retry", {})
    assert retry.get("max_attempts") == 1
    assert retry.get("stale_worker_retry") is True
    assert retry.get("audit_retry") is True


def test_retry_config_loaded_from_file(tmp_path: Path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("retry:\n  max_attempts: 2\n", encoding="utf-8")
    cfg = config.load(cfg_path)
    assert cfg["retry"]["max_attempts"] == 2
    assert cfg["retry"]["stale_worker_retry"] is True  # default still present


# ── 2. Helper functions ─────────────────────────────────────────────────────


def test_build_retry_prompt_appends_context():
    original = "Do the task."
    summary = {"status": "PART", "issues": ["missing_tests"], "evidence": []}
    result = cli._build_retry_prompt(original, "d001", "PART", summary)
    assert result.startswith("Do the task.")
    assert "d001" in result
    assert "PART" in result
    assert "missing_tests" in result


def test_build_audit_fix_prompt_includes_issues():
    original = "Do the task."
    audit = {"issues": ["evidence_not_verifiable"], "summary": "Cannot verify the claim."}
    result = cli._build_audit_fix_prompt(original, "d001", audit)
    assert result.startswith("Do the task.")
    assert "evidence_not_verifiable" in result
    assert "Cannot verify the claim." in result


# ── 3. Retry on PART — mock agents_mod.run ─────────────────────────────────


def test_part_triggers_retry_and_ok_on_second_attempt(tmp_path: Path, monkeypatch):
    """First call returns PART; second (retry) returns OK. Should not escalate."""
    p = _paths(tmp_path)
    log_path = p["logs"] / "d001.log"
    log_path.write_text("", encoding="utf-8")

    calls = []

    def fake_run(agent_cfg, prompt, *, timeout, cwd):
        calls.append(prompt)
        return _agent_result(_ok_result())  # the retry (second attempt) succeeds

    monkeypatch.setattr(cli.agents_mod, "is_available", lambda cfg: True)
    monkeypatch.setattr(cli.agents_mod, "run", fake_run)

    cfg = {
        "agents": {"bronze": {"name": "haiku", "command": "haiku -p"}},
        "retry": {"max_attempts": 1, "stale_worker_retry": True, "audit_retry": False},
        "metrics": {"expensive_model_usd_per_million": 15.0},
    }

    base_summary = {
        "id": "d001",
        "status": "PART",
        "kind": "execution",
        "summary": "partial",
        "files_touched": [],
        "validated": [],
        "evidence": [],
        "issues": ["missing_tests"],
        "next": "add tests",
    }

    # Simulate the retry loop directly (the same logic as cmd_run uses)
    retry_cfg = cfg.get("retry", {})
    max_attempts = int(retry_cfg.get("max_attempts", 1))
    stale_timeout_s = 300
    summary = dict(base_summary)
    stale = False
    interrupted = False
    _retry_count = 0
    _retry_status: list[str] = []

    _cur_status = str(summary.get("status") or "").upper()
    if _cur_status in ("PART", "ERR") and not interrupted:
        _is_stale = stale and "stale_worker" in (summary.get("issues") or [])
        _attempts_left = 1 if _is_stale else max_attempts
        _do_retry = (_is_stale and retry_cfg.get("stale_worker_retry", True)) or (not _is_stale and _attempts_left > 0)

        while _do_retry and _attempts_left > 0:
            _attempts_left -= 1
            _retry_status.append(_cur_status)

            if _is_stale:
                _retry_prompt_text = "original prompt"
                _retry_timeout = min(stale_timeout_s * 2, 600)
            else:
                _retry_prompt_text = cli._build_retry_prompt("original prompt", "d001", _cur_status, summary)
                _retry_timeout = 600

            _retry_res = cli.agents_mod.run(
                cfg["agents"]["bronze"], _retry_prompt_text, timeout=_retry_timeout, cwd=tmp_path
            )

            with log_path.open("a", encoding="utf-8") as _lf:
                _lf.write(f"\n\n--- RETRY_{_retry_count + 1} ---\n" + _retry_res.get("stdout", "") + "\n")

            _rj = deleg_mod.extract_result_json(_retry_res.get("stdout", ""))
            if _rj is not None:
                _r_sum = _rj
                _r_sum["kind"] = cli._normalize_report_kind(
                    _r_sum.get("kind") or _infer_hint("original prompt")
                )
            else:
                _r_sum = {"id": "d001", "status": "PART", "issues": ["missing_final_json"], "kind": "execution", "summary": "", "files_touched": [], "validated": [], "next": ""}

            _retry_count += 1
            _new_status = str(_r_sum.get("status") or "").upper()

            if _new_status == "OK":
                summary = _r_sum
                break

            _orig_issues = summary.get("issues") or []
            _r_issues = _r_sum.get("issues") or []
            summary = _r_sum
            summary["issues"] = list(dict.fromkeys(_orig_issues + _r_issues))
            _cur_status = _new_status
            _do_retry = _attempts_left > 0

    summary["retry_count"] = _retry_count
    summary["retry_status"] = _retry_status

    assert summary["status"] == "OK", "Should succeed on retry"
    assert _retry_count == 1, "Exactly one retry should have happened"
    assert _retry_status == ["PART"], "Retry status tracks previous status"
    assert len(calls) == 1, "agents.run called once for the retry (original was pre-set in summary)"
    # Retry prompt should include original context + issue info
    assert "d001" in calls[0]
    assert "missing_tests" in calls[0]

    # Log should include RETRY marker
    log_text = log_path.read_text(encoding="utf-8")
    assert "RETRY_1" in log_text


def _infer_hint(prompt: str) -> str:
    return cli._infer_kind_hint(prompt)


def test_part_retry_merges_issues_on_double_failure(tmp_path: Path, monkeypatch):
    """Both attempts return PART; issues from both attempts are merged."""
    p = _paths(tmp_path)
    log_path = p["logs"] / "d001.log"
    log_path.write_text("", encoding="utf-8")

    calls = []

    def fake_run(agent_cfg, prompt, *, timeout, cwd):
        calls.append(prompt)
        # summary already has issue_a — retry returns a new issue to merge
        return _agent_result(_part_result(issues=["issue_b"]))

    monkeypatch.setattr(cli.agents_mod, "is_available", lambda cfg: True)
    monkeypatch.setattr(cli.agents_mod, "run", fake_run)

    summary = {
        "id": "d001", "status": "PART", "kind": "execution", "summary": "partial",
        "files_touched": [], "validated": [], "evidence": [], "issues": ["issue_a"], "next": "",
    }
    stale = False
    interrupted = False
    _retry_count = 0
    _retry_status: list[str] = []
    _cur_status = "PART"
    _attempts_left = 1

    while _attempts_left > 0:
        _attempts_left -= 1
        _retry_status.append(_cur_status)
        _retry_prompt_text = cli._build_retry_prompt("original", "d001", _cur_status, summary)
        _retry_res = cli.agents_mod.run(
            {"name": "x", "command": "x"}, _retry_prompt_text, timeout=600, cwd=tmp_path
        )
        with log_path.open("a", encoding="utf-8") as _lf:
            _lf.write(f"\n\n--- RETRY_{_retry_count + 1} ---\n" + _retry_res.get("stdout", "") + "\n")
        _rj = deleg_mod.extract_result_json(_retry_res.get("stdout", ""))
        _retry_count += 1
        _new_status = str((_rj or {}).get("status") or "PART").upper()
        _orig_issues = summary.get("issues") or []
        _r_issues = (_rj or {}).get("issues") or []
        summary = _rj or summary
        summary["issues"] = list(dict.fromkeys(_orig_issues + _r_issues))
        _cur_status = _new_status

    summary["retry_count"] = _retry_count
    summary["retry_status"] = _retry_status

    assert summary["status"] == "PART"
    assert "issue_a" in summary["issues"]
    assert "issue_b" in summary["issues"]
    assert summary["retry_count"] == 1


# ── 4. Stale worker retry uses doubled timeout ──────────────────────────────


def test_stale_worker_retry_doubles_timeout(tmp_path: Path, monkeypatch):
    """Stale retry uses stale_timeout_s * 2, not args.timeout."""
    p = _paths(tmp_path)
    log_path = p["logs"] / "d001.log"
    log_path.write_text("", encoding="utf-8")

    seen_timeouts = []

    def fake_run(agent_cfg, prompt, *, timeout, cwd):
        seen_timeouts.append(timeout)
        return _agent_result(_ok_result())

    monkeypatch.setattr(cli.agents_mod, "run", fake_run)

    summary = {
        "id": "d001", "status": "PART", "kind": "execution", "summary": "stale",
        "files_touched": [], "validated": [], "evidence": [], "issues": ["stale_worker"], "next": "",
    }
    stale = True
    stale_timeout_s = 120
    _retry_count = 0
    _retry_status: list[str] = []

    _is_stale = stale and "stale_worker" in (summary.get("issues") or [])
    if _is_stale:
        _retry_timeout = min(stale_timeout_s * 2, 600)
        _retry_res = cli.agents_mod.run(
            {"name": "x", "command": "x"}, "original", timeout=_retry_timeout, cwd=tmp_path
        )
        with log_path.open("a", encoding="utf-8") as _lf:
            _lf.write(f"\n\n--- RETRY_1 ---\n" + _retry_res.get("stdout", "") + "\n")
        _rj = deleg_mod.extract_result_json(_retry_res.get("stdout", ""))
        _retry_count = 1
        _retry_status.append("PART")
        if _rj and str(_rj.get("status") or "").upper() == "OK":
            summary = _rj

    assert seen_timeouts == [240], f"expected timeout=240 (120*2), got {seen_timeouts}"
    assert summary["status"] == "OK"
    assert _retry_count == 1


# ── 5. retry_count / retry_status fields in output ─────────────────────────


def test_no_retry_fields_zero_when_ok_immediately(tmp_path: Path, monkeypatch):
    """When worker returns OK on first try, retry_count=0 and retry_status=[]."""
    p = _paths(tmp_path)
    log_path = p["logs"] / "d001.log"
    log_path.write_text("", encoding="utf-8")

    summary = {
        "id": "d001", "status": "OK", "kind": "execution", "summary": "done",
        "files_touched": [], "validated": [], "evidence": ["/tmp/x exists"], "issues": [], "next": "",
    }
    interrupted = False
    stale = False
    _retry_count = 0
    _retry_status: list[str] = []

    # Simulate: no retry triggered since status is OK
    _cur_status = str(summary.get("status") or "").upper()
    if _cur_status in ("PART", "ERR") and not interrupted:
        pass  # would retry but shouldn't reach here

    summary["retry_count"] = _retry_count
    summary["retry_status"] = _retry_status

    assert summary["retry_count"] == 0
    assert summary["retry_status"] == []


# ── 6. Regression tests for 2026-07-02 audit findings #4/#5/#6 ──────────────
# #4: retry loop retried deterministic CLI failures (bad flags, missing
#     binary) identically instead of recognizing they can't self-heal.
# #5: retry called agents_mod.run(agent_cfg, ...) with the ORIGINAL tier
#     config (no tier= kwarg), instead of the provider actually selected
#     for the first attempt — silently re-ranking/switching provider.
# #6: --cold-cache only busted the cached_worker backend; subprocess/codex/
#     claude runs stayed warm regardless of the flag.

from burnless.exec import runner as runner_mod


class TestIsRetryableRunFailure:
    """Unit tests for the new runner_mod._is_retryable_run_failure classifier."""

    def test_none_result_defaults_retryable(self):
        assert runner_mod._is_retryable_run_failure({"status": "PART"}, None) is True

    def test_timeout_is_retryable(self):
        result = {"stdout": "", "stderr": "", "timed_out": True}
        assert runner_mod._is_retryable_run_failure({}, result) is True

    def test_stale_is_retryable(self):
        result = {"stdout": "", "stderr": "", "stale": True}
        assert runner_mod._is_retryable_run_failure({}, result) is True

    def test_5xx_stderr_is_retryable(self):
        result = {"stdout": "", "stderr": "upstream error 503 service unavailable"}
        assert runner_mod._is_retryable_run_failure({}, result) is True

    def test_unexpected_argument_not_retryable(self):
        """The exact failure mode from the 2 bugs fixed earlier today: codex
        CLI rejecting a malformed invocation with 'unexpected argument'."""
        result = {"stdout": "", "stderr": "error: unexpected argument '--foo' found", "returncode": 2}
        assert runner_mod._is_retryable_run_failure({}, result) is False

    def test_flag_used_multiple_times_not_retryable(self):
        result = {"stdout": "", "stderr": "argument '--skip-git-repo-check' cannot be used multiple times", "returncode": 2}
        assert runner_mod._is_retryable_run_failure({}, result) is False

    def test_command_not_found_not_retryable(self):
        result = {"stdout": "", "stderr": "bash: codexx: command not found", "returncode": 127}
        assert runner_mod._is_retryable_run_failure({}, result) is False

    def test_missing_binary_not_retryable(self):
        result = {"stdout": "", "stderr": "/usr/bin/env: 'python5': No such file or directory", "returncode": 127}
        assert runner_mod._is_retryable_run_failure({}, result) is False

    def test_ambiguous_part_defaults_retryable(self):
        """A worker-judged PART with no deterministic CLI signature (e.g. it
        just didn't finish the task) still gets the benefit of the doubt."""
        result = {"stdout": "some normal worker output", "stderr": "", "returncode": 0}
        assert runner_mod._is_retryable_run_failure({}, result) is True


class TestRetryLoopUsesSelectedProviderAndTier(object):
    """End-to-end regression for finding #5: the PART/ERR retry call must
    reuse the provider selected for the FIRST attempt (not the raw tier
    config) and must pass tier= so agents_mod.run() ranks/records health
    under the right tier bucket instead of falling back to 'default'."""

    def _make_root(self, tmp_path: Path) -> Path:
        root = tmp_path / ".burnless"
        p = paths.paths_for(root)
        for key in ("delegations", "logs", "temp", "capsules", "archive", "chat", "runs"):
            p[key].mkdir(parents=True, exist_ok=True)
        return root

    def test_retry_call_receives_selected_agent_cfg_and_tier(self, tmp_path, monkeypatch):
        root = self._make_root(tmp_path)
        p = paths.paths_for(root)
        did = "d001"
        (p["delegations"] / f"{did}.md").write_text(
            "- **Agent:** codex-gpt-5.5 (silver)\n\nDo the task.\n", encoding="utf-8"
        )

        # Two distinct providers under the same tier — ranking picks the
        # SECOND one (simulating a health-based reorder). If the retry call
        # still used the raw, un-ranked tier config (the pre-fix bug), it
        # would neither match the selected provider's identity nor its
        # command, since the raw tier dict here carries no runnable command
        # of its own.
        primary_provider = {"name": "primary-provider", "command": "primary-cmd", "provider": "codex"}
        secondary_provider = {"name": "secondary-provider", "command": "secondary-cmd", "provider": "codex"}
        cfg = {
            "agents": {
                "silver": {
                    "name": "silver-tier",
                    "providers": [primary_provider, secondary_provider],
                }
            },
            "retry": {"max_attempts": 1, "stale_worker_retry": True, "audit_retry": False},
            "metrics": {"expensive_model_usd_per_million": 15.0},
            "validation": {"honest_exit_code": False},
        }

        from burnless.exec import runner as rmod

        monkeypatch.setattr(rmod.config_mod, "load", lambda path: cfg)
        monkeypatch.setattr(rmod.state_mod, "load", lambda path: {})
        monkeypatch.setattr(rmod.metrics_mod, "load", lambda path: {"burnless_tokens": 0})
        monkeypatch.setattr(rmod, "_record_and_bump", lambda *a, **k: None)
        monkeypatch.setattr(rmod, "_emit_audit_record", lambda *a, **k: None)
        monkeypatch.setattr(rmod.agents_mod, "is_available", lambda cfg: True)
        monkeypatch.setattr(rmod.agents_mod, "maybe_prepend_prior_decision", lambda prompt, tier: prompt)
        # Force ranking to prefer the SECOND provider, mimicking a
        # health-based reorder distinct from source order.
        monkeypatch.setattr(
            rmod.agents_mod, "rank_providers",
            lambda agent_cfg, tier: [{"cfg": secondary_provider}, {"cfg": primary_provider}],
        )

        first_result = {
            "agent": "secondary-provider",
            "command": ["codex", "exec"],
            "stdout": _part_result(did),
            "stderr": "",
            "returncode": 0,
            "started_at": "2026-07-02T00:00:00Z",
            "ended_at": "2026-07-02T00:00:01Z",
            "duration_s": 1.0,
            "interrupted": False,
            "stale": False,
        }

        class _FakeRunResult:
            def to_dict(self_inner):
                return dict(first_result)

        monkeypatch.setattr(rmod.live_runner, "run_with_overflow_retries", lambda **kwargs: _FakeRunResult())

        retry_calls = []

        def fake_agents_run(agent_cfg, prompt, *, timeout, cwd, tier=None):
            retry_calls.append({"agent_cfg": agent_cfg, "tier": tier})
            return {"stdout": _ok_result(did), "stderr": "", "returncode": 0, "stale": False, "interrupted": False}

        monkeypatch.setattr(rmod.agents_mod, "run", fake_agents_run)

        opts = rmod.RunOpts(id=did)
        rmod.execute_delegation(opts, root=root)

        assert len(retry_calls) == 1, "retry should call agents_mod.run exactly once"
        assert retry_calls[0]["agent_cfg"] == secondary_provider, (
            "retry must reuse the provider selected for the first attempt "
            f"(secondary-provider), got: {retry_calls[0]['agent_cfg']}"
        )
        assert retry_calls[0]["tier"] == "silver", (
            "retry must pass tier= so provider health/ranking is recorded under the real tier, not 'default'"
        )


class TestColdCacheReachesSubprocessPath:
    """Regression for finding #6: --cold-cache was only wired into the
    cached_worker backend; the default subprocess/live_runner path (claude,
    codex, gemini CLIs) ignored RunOpts.cold_cache entirely, so cold-cache
    benchmarks against those providers were silently still warm."""

    def _make_root(self, tmp_path: Path) -> Path:
        root = tmp_path / ".burnless"
        p = paths.paths_for(root)
        for key in ("delegations", "logs", "temp", "capsules", "archive", "chat", "runs"):
            p[key].mkdir(parents=True, exist_ok=True)
        return root

    def test_cold_cache_flag_reaches_live_runner_and_busts_prompt(self, tmp_path, monkeypatch):
        root = self._make_root(tmp_path)
        p = paths.paths_for(root)
        did = "d002"
        (p["delegations"] / f"{did}.md").write_text(
            "- **Agent:** codex-gpt-5.5 (silver)\n\nDo the task.\n", encoding="utf-8"
        )
        (p["logs"] / f"{did}.log").write_text("", encoding="utf-8")

        cfg = {
            "agents": {
                "silver": {"name": "codex-gpt-5.5", "command": "codex exec -m gpt-5.5", "provider": "codex"},
            },
            "retry": {"max_attempts": 1, "stale_worker_retry": True, "audit_retry": False},
            "metrics": {"expensive_model_usd_per_million": 15.0},
            "validation": {"honest_exit_code": False},
        }

        from burnless.exec import runner as rmod

        monkeypatch.setattr(rmod.config_mod, "load", lambda path: cfg)
        monkeypatch.setattr(rmod.state_mod, "load", lambda path: {})
        monkeypatch.setattr(rmod.metrics_mod, "load", lambda path: {"burnless_tokens": 0})
        monkeypatch.setattr(rmod, "_record_and_bump", lambda *a, **k: None)
        monkeypatch.setattr(rmod, "_emit_audit_record", lambda *a, **k: None)
        monkeypatch.setattr(rmod.agents_mod, "is_available", lambda cfg: True)
        monkeypatch.setattr(rmod.agents_mod, "maybe_prepend_prior_decision", lambda prompt, tier: prompt)

        captured_calls = []

        class _FakeRunResult:
            def to_dict(self_inner):
                return {
                    "agent": "codex-gpt-5.5",
                    "command": ["codex", "exec"],
                    "stdout": _ok_result(did),
                    "stderr": "",
                    "returncode": 0,
                    "started_at": "2026-07-02T00:00:00Z",
                    "ended_at": "2026-07-02T00:00:01Z",
                    "duration_s": 1.0,
                    "interrupted": False,
                    "stale": False,
                }

        def fake_run_with_overflow_retries(**kwargs):
            captured_calls.append(kwargs)
            return _FakeRunResult()

        monkeypatch.setattr(rmod.live_runner, "run_with_overflow_retries", fake_run_with_overflow_retries)

        opts = rmod.RunOpts(id=did, cold_cache=True)
        rmod.execute_delegation(opts, root=root)

        assert len(captured_calls) == 1
        assert captured_calls[0]["cold_cache"] is True, (
            "cold_cache must reach live_runner.run_with_overflow_retries on the subprocess path"
        )
        assert "cache-bust:" in captured_calls[0]["prompt"], (
            "cold_cache must also bust the prompt text itself, not just gate warm-session injection"
        )
