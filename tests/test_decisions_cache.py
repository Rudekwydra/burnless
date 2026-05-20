import argparse
import json
from pathlib import Path

from burnless import agents, cli, config, delegations, metrics, paths, state


def test_decisions_cache_fail_open_on_corrupted_file(tmp_path: Path, monkeypatch):
    cache_path = tmp_path / "decisions_cache.json"
    cache_path.write_text("{broken", encoding="utf-8")
    monkeypatch.setenv("BURNLESS_DECISIONS_CACHE_PATH", str(cache_path))

    assert agents.list_decisions() == []
    assert agents.maybe_prepend_prior_decision("# Delegation d001", tier="silver") == "# Delegation d001"


def test_decisions_list_and_clear_commands(tmp_path: Path, monkeypatch, capsys):
    cache_path = tmp_path / "decisions_cache.json"
    monkeypatch.setenv("BURNLESS_DECISIONS_CACHE_PATH", str(cache_path))
    agents.remember_silver_decision(
        tier="silver",
        prompt="## Goal\npersist state\n\n## Task\nuse sqlite instead of json for local storage\n",
        summary={"summary": "Use SQLite instead of JSON for local persistence."},
        stdout="DECISION: Use SQLite instead of JSON for local persistence.",
    )

    assert cli.main(["decisions", "list", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed[0]["decision_text"] == "Use SQLite instead of JSON for local persistence."

    assert cli.main(["decisions", "clear"]) == 0
    assert "cleared 1 cached decision(s)" in capsys.readouterr().out
    assert agents.list_decisions() == []


def test_run_reuses_prior_decision_and_updates_cache(tmp_path: Path, monkeypatch):
    root = tmp_path / ".burnless"
    p = paths.paths_for(root)
    for key in ("delegations", "logs", "temp", "capsules", "archive", "chat", "runs"):
        p[key].mkdir(parents=True, exist_ok=True)

    config.write_default(p["config"])
    cfg = config.load(p["config"])
    cfg["agents"]["silver"] = {"name": "sonnet", "command": "printf ok", "role": "execution"}
    cfg["routing"]["silver"] = ["sqlite"]
    config.save(p["config"], cfg)
    state.save(p["state"], state.DEFAULT_STATE | {"project": "demo"})
    metrics.save(p["metrics"], metrics._fresh())

    deleg_path = p["delegations"] / "d001.md"
    delegations.write_delegation(
        deleg_path,
        delegations.render_delegation(
            delegation_id="d001",
            goal="persist agent state",
            task="decide whether to use sqlite instead of json for local persistence",
            success="task completed",
            kind_hint="thought",
            agent_name="sonnet",
            tier="silver",
            routed_by="manual",
        ),
    )

    cache_path = tmp_path / "decisions_cache.json"
    monkeypatch.setenv("BURNLESS_DECISIONS_CACHE_PATH", str(cache_path))
    cache_path.write_text(
        json.dumps(
            [
                {
                    "decision_hash": "seedhash",
                    "context_summary": "persist agent state | decide whether to use sqlite instead of json for local persistence",
                    "decision_text": "Use SQLite instead of JSON for local persistence.",
                    "hits": 3,
                    "last_used": "2026-01-01T00:00:00+00:00",
                }
            ]
        ),
        encoding="utf-8",
    )

    captured = {}

    class _FakeResult:
        agent = "sonnet"
        command = ["printf", "ok"]
        stdout = '```json\n{"id":"d001","status":"OK","kind":"thought","summary":"Use SQLite instead of JSON for local persistence.","evidence":[],"files_touched":[],"validated":[],"issues":[],"next":""}\n```'
        stderr = ""
        returncode = 0
        started_at = "2026-01-01T00:00:00+00:00"
        ended_at = "2026-01-01T00:00:01+00:00"
        duration_s = 1.0
        interrupted = False

        def to_dict(self):
            return {
                "agent": self.agent,
                "command": self.command,
                "stdout": self.stdout,
                "stderr": self.stderr,
                "returncode": self.returncode,
                "started_at": self.started_at,
                "ended_at": self.ended_at,
                "duration_s": self.duration_s,
                "interrupted": self.interrupted,
            }

        def get(self, key, default=None):
            return self.to_dict().get(key, default)

    def fake_runner(**kwargs):
        captured["prompt"] = kwargs["prompt"]
        kwargs["log_path"].write_text("--- ASSISTANT ---\nDECISION: Use SQLite instead of JSON for local persistence.\n", encoding="utf-8")
        return _FakeResult()

    monkeypatch.setattr(cli.paths_mod, "require_root", lambda: root)
    monkeypatch.setattr(cli.agents_mod, "is_available", lambda cfg: True)
    monkeypatch.setattr(cli.live_runner, "run_with_live_panel", fake_runner)
    monkeypatch.setattr(cli, "_audit_summary_evidence", lambda *args, **kwargs: kwargs["summary"])

    args = argparse.Namespace(
        id="d001",
        dry_run=False,
        timeout=30,
        stale_timeout_s=None,
        maestro=False,
        no_maestro=False,
        no_cache_worker=True,
        cold_cache=False,
        no_decode=True,
        mode="plain",
        progress=None,
    )

    assert cli.cmd_run(args) == 0
    assert "## PRIOR DECISION" in captured["prompt"]

    entries = agents.list_decisions()
    assert len(entries) == 2 or len(entries) == 1
    matched = next(e for e in entries if e["decision_text"] == "Use SQLite instead of JSON for local persistence.")
    assert matched["hits"] >= 4
