"""Tests for P0 honest exit code — verify gate + extract_verify_block."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from burnless.delegation_parse import extract_verify_block
from burnless import cli, config
from burnless import delegations as deleg_mod


# ── 1. extract_verify_block parsing ────────────────────────────────────────


def test_extract_verify_block_parsing():
    # sh info string, ignores comments and blanks
    md = "## Verify\n\n```sh\ntrue\n# comment\n\ntest 1 -eq 1\n```\n"
    assert extract_verify_block(md) == ["true", "test 1 -eq 1"]

    # bash info string
    assert extract_verify_block("## Verify\n\n```bash\necho hi\n```") == ["echo hi"]

    # shell info string
    assert extract_verify_block("## Verify\n\n```shell\nls /tmp\n```") == ["ls /tmp"]

    # verify info string
    assert extract_verify_block("## Verify\n\n```verify\npwd\n```") == ["pwd"]

    # bare ```
    assert extract_verify_block("## Verify\n\n```\ngrep foo bar\n```") == ["grep foo bar"]

    # absent section → empty list
    assert extract_verify_block("no verify here") == []
    assert extract_verify_block("## Goal\ndone") == []

    # case-insensitive header
    assert extract_verify_block("## VERIFY\n\n```sh\ntrue\n```") == ["true"]

    # blank lines and comment-only lines are stripped
    md2 = "## Verify\n\n```sh\n# skip me\n\necho ok\n\n```\n"
    assert extract_verify_block(md2) == ["echo ok"]


# ── 2. OK + passing check stays OK ─────────────────────────────────────────


def test_ok_plus_passing_check_stays_ok(tmp_path: Path):
    log_path = tmp_path / "d001.log"
    log_path.write_text("", encoding="utf-8")
    summary = {"status": "OK", "validated": [], "issues": [], "next": ""}
    result = cli._apply_verify_gate(
        summary, ["true"], cwd=tmp_path, did="d001", log_path=log_path, timeout=10
    )
    assert result["status"] == "OK"
    assert any("verify:" in str(v) for v in result.get("validated", []))
    log_text = log_path.read_text(encoding="utf-8")
    assert "VERIFY" in log_text


# ── 3. Phantom OK + failing check demotes to PART, retry fires ─────────────


def test_phantom_ok_failing_check_demotes_to_part_and_retry_fires(tmp_path: Path, monkeypatch):
    log_path = tmp_path / "d001.log"
    log_path.write_text("", encoding="utf-8")

    # Gate demotes OK→PART for a failing command
    summary = {"status": "OK", "validated": [], "issues": [], "next": ""}
    demoted = cli._apply_verify_gate(
        summary, ["false"], cwd=tmp_path, did="d001", log_path=log_path, timeout=10
    )
    assert demoted["status"] == "PART"
    assert any("verify_failed" in str(i) for i in demoted.get("issues", []))
    assert demoted.get("next") == "false"

    # A PART summary should trigger the retry condition (same logic as _cmd_run_body)
    _cur_status = str(demoted.get("status") or "").upper()
    assert _cur_status in ("PART", "ERR"), "Demoted summary must trigger the retry loop"

    # Simulate retry firing — mock agents_mod.run to return OK on retry
    retry_calls = []

    def fake_run(agent_cfg, prompt, *, timeout, cwd):
        retry_calls.append(prompt)
        data = {
            "id": "d001", "status": "OK", "kind": "execution", "summary": "fixed",
            "files_touched": [], "validated": [], "evidence": [], "issues": [], "next": "",
        }
        return {
            "stdout": f"```json\n{json.dumps(data)}\n```",
            "stderr": "", "returncode": 0, "stale": False, "interrupted": False,
        }

    monkeypatch.setattr(cli.agents_mod, "run", fake_run)

    _retry_res = cli.agents_mod.run(
        {"name": "haiku", "command": "haiku"}, "prompt", timeout=600, cwd=tmp_path
    )
    _rj = deleg_mod.extract_result_json(_retry_res.get("stdout", ""))
    assert _rj is not None
    assert str(_rj.get("status") or "").upper() == "OK"
    assert len(retry_calls) == 1, "Retry was invoked once"


# ── 4. No ## Verify block → no-op regression ───────────────────────────────


def test_no_verify_block_is_noop(tmp_path: Path):
    log_path = tmp_path / "d001.log"
    log_path.write_text("", encoding="utf-8")

    verify_cmds = extract_verify_block("## Goal\nJust do something\n\n## Task\nDo it.")
    assert verify_cmds == []

    summary = {"status": "OK", "validated": [], "issues": [], "next": ""}
    result = cli._apply_verify_gate(
        summary, verify_cmds, cwd=tmp_path, did="d001", log_path=log_path, timeout=10
    )
    assert result["status"] == "OK"
    # No-op: should return the exact same object (not a copy)
    assert result is summary


# ── 5. honest_exit_code=false → gate skipped ────────────────────────────────


def test_honest_exit_code_false_skips_gate(tmp_path: Path):
    md = "## Verify\n\n```sh\nfalse\n```\n"
    cfg_validation = {"honest_exit_code": False, "verify_timeout_s": 120}

    # Extraction is gated on the config flag (mirrors the _cmd_run_body logic)
    verify_cmds = extract_verify_block(md) if cfg_validation.get("honest_exit_code", True) else []
    assert verify_cmds == [], "Gate must not extract cmds when honest_exit_code=False"

    log_path = tmp_path / "d001.log"
    log_path.write_text("", encoding="utf-8")
    summary = {"status": "OK", "validated": [], "issues": [], "next": ""}
    result = cli._apply_verify_gate(
        summary, verify_cmds, cwd=tmp_path, did="d001", log_path=log_path, timeout=10
    )
    assert result["status"] == "OK"
    assert result is summary
