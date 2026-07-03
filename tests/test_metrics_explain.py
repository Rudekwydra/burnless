from __future__ import annotations

import argparse
import json
from pathlib import Path


def test_append_and_read_spend_roundtrip(tmp_path):
    from burnless import metrics

    spend_path = tmp_path / ".burnless" / "spend.jsonl"
    metrics.append_spend(
        spend_path,
        ts="2026-07-02T00:00:00Z",
        delegation_id="d1",
        tier="silver",
        provider="claude",
        model="claude-sonnet",
        usage={"input_tokens": 10, "output_tokens": 20},
        duration_s=1.25,
        backend="claude",
        retry_count=0,
    )

    rows = metrics.read_spend(spend_path)
    assert len(rows) == 1
    assert rows[0]["delegation_id"] == "d1"
    assert rows[0]["usage"]["output_tokens"] == 20


def test_metrics_explain_renders_audit_and_spend(tmp_path, capsys, monkeypatch):
    from burnless import cli

    burnless_root = tmp_path / ".burnless"
    burnless_root.mkdir(parents=True, exist_ok=True)
    audit_path = burnless_root / "audit.jsonl"
    audit_path.write_text(
        json.dumps(
            {
                "ts": "2026-07-02T00:00:00Z",
                "source": "capsule_compression",
                "amount": 123,
                "basis": "chars4",
                "delegation_id": "d42",
                "reason": "encoder: raw user message → capsule",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    spend_path = burnless_root / "spend.jsonl"
    spend_path.write_text(
        json.dumps(
            {
                "ts": "2026-07-02T00:00:00Z",
                "delegation_id": "d42",
                "tier": "bronze",
                "provider": "claude",
                "model": "claude-sonnet",
                "usage": {
                    "input_tokens": 11,
                    "output_tokens": 22,
                    "cache_read_input_tokens": 3,
                    "cache_creation_input_tokens": 4,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr("burnless.paths.require_root", lambda: burnless_root)
    args = argparse.Namespace(
        snapshot=None,
        diff=False,
        explain=True,
        limit=10,
        global_view=False,
        since=None,
        metrics_cmd=None,
    )
    rc = cli.cmd_metrics(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "audit.jsonl" in out
    assert "spend.jsonl" in out
    assert "chars4" in out
    assert "bronze" in out
    assert "claude" in out


def test_agents_run_persists_spend(tmp_path, monkeypatch):
    from burnless import agents, metrics

    agent_cfg = {"name": "test-agent", "provider": "anthropic"}
    fake_result = {
        "agent": "test-agent",
        "provider": "claude",
        "command": ["claude", "-p"],
        "stdout": "",
        "stderr": "",
        "returncode": 0,
        "started_at": "2026-07-02T00:00:00Z",
        "ended_at": "2026-07-02T00:00:01Z",
        "duration_s": 1.0,
        "usage": {"input_tokens": 7, "output_tokens": 9},
        "selected_provider": "claude",
    }

    monkeypatch.setattr(agents, "_run_once", lambda *a, **kw: fake_result.copy())
    monkeypatch.setattr(agents, "rank_providers", lambda *a, **kw: [])

    result = agents.run(agent_cfg, "hello", cwd=tmp_path, tier="bronze")
    assert result["usage"]["output_tokens"] == 9

    spend_rows = metrics.read_spend(tmp_path / ".burnless" / "spend.jsonl")
    assert len(spend_rows) == 1
    assert spend_rows[0]["tier"] == "bronze"
    assert spend_rows[0]["usage"]["input_tokens"] == 7
