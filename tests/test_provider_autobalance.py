import argparse
import json
import subprocess

from burnless import agents, cli


def test_rank_providers_prefers_best_score(tmp_path, monkeypatch):
    health_path = tmp_path / "provider_health.json"
    monkeypatch.setenv("BURNLESS_PROVIDER_HEALTH_PATH", str(health_path))
    health_path.write_text(
        json.dumps(
            {
                "silver:openai": {
                    "tier": "silver",
                    "provider": "openai",
                    "success_rate": 0.50,
                    "avg_latency": 0.20,
                },
                "silver:anthropic": {
                    "tier": "silver",
                    "provider": "anthropic",
                    "success_rate": 0.90,
                    "avg_latency": 0.80,
                },
            }
        ),
        encoding="utf-8",
    )
    cfg = {
        "name": "balanced",
        "command": "printf noop",
        "providers": [
            {"provider": "anthropic", "name": "claude", "command": "printf anthropic"},
            {"provider": "openai", "name": "gpt", "command": "printf openai"},
        ],
    }

    ranked = agents.rank_providers(cfg, tier="silver")

    assert ranked[0]["cfg"]["provider"] == "anthropic"
    assert ranked[1]["cfg"]["provider"] == "openai"


def test_run_falls_back_on_timeout_and_updates_stats(tmp_path, monkeypatch):
    monkeypatch.setenv("BURNLESS_PROVIDER_HEALTH_PATH", str(tmp_path / "provider_health.json"))
    monkeypatch.setattr(agents.shutil, "which", lambda _: "/bin/mock")
    calls = {"n": 0}

    def fake_run(parts, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise subprocess.TimeoutExpired(parts, kwargs["timeout"], output="partial", stderr="timed out")
        return subprocess.CompletedProcess(parts, 0, stdout='{"status":"OK"}', stderr="")

    monkeypatch.setattr(agents.subprocess, "run", fake_run)
    cfg = {
        "name": "balanced",
        "command": "printf noop",
        "providers": [
            {"provider": "anthropic", "name": "claude", "command": "printf anthropic"},
            {"provider": "openai", "name": "gpt", "command": "printf openai"},
        ],
    }

    result = agents.run(cfg, "hello", timeout=5, tier="silver")
    stats = {row["key"]: row for row in agents.list_provider_stats()}

    assert calls["n"] == 2
    assert result["selected_provider"] == "openai"
    assert len(result["provider_attempts"]) == 2
    assert stats["silver:anthropic"]["failures"] == 1
    assert stats["silver:openai"]["successes"] == 1


def test_providers_stats_and_reset_commands(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("BURNLESS_PROVIDER_HEALTH_PATH", str(tmp_path / "provider_health.json"))
    provider_cfg = {"provider": "gemini", "name": "gemini-pro", "command": "gemini -p"}
    agents.record_provider_result(tier="gold", provider_cfg=provider_cfg, success=True, latency_s=1.25)

    rc = cli.cmd_providers_stats(argparse.Namespace())
    out = capsys.readouterr().out
    assert rc == 0
    assert "gold:gemini" in out
    assert "avg_latency=1.25s" in out

    rc = cli.cmd_providers_reset(argparse.Namespace())
    out = capsys.readouterr().out
    assert rc == 0
    assert "cleared 1 provider health record(s)" in out
    assert agents.list_provider_stats() == []


def test_provider_alias_commands_are_registered():
    parser = cli.build_parser()

    stats_args = parser.parse_args(["provider-stats"])
    reset_args = parser.parse_args(["provider-reset"])

    assert stats_args.func is cli.cmd_providers_stats
    assert reset_args.func is cli.cmd_providers_reset
