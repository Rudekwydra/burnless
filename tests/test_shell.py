from pathlib import Path

from burnless import metrics, paths, shell, state


def test_shell_natural_language_runs_configured_worker_not_maestro(tmp_path: Path, monkeypatch):
    root = tmp_path / ".burnless"
    p = paths.paths_for(root)
    for key in ("delegations", "logs", "temp", "capsules", "archive", "chat"):
        p[key].mkdir(parents=True, exist_ok=True)
    p["config"].write_text(
        """
project_name: test
agents:
  gold:
    name: opus
    command: printf ok
    role: strategy
  silver:
    name: sonnet
    command: printf ok
    role: execution
  bronze:
    name: haiku
    command: printf ok
    role: cheap
routing:
  gold: []
  silver: [projeto]
  bronze: []
metrics:
  expensive_model_usd_per_million: 15.0
compression:
  mode: balanced
""",
        encoding="utf-8",
    )
    state.save(p["state"], state.DEFAULT_STATE | {"project": "test"})
    metrics.save(p["metrics"], metrics._fresh())
    p["history"].write_text("# history\n", encoding="utf-8")

    run_args = []

    def fake_cmd_run(args):
        run_args.append(args)
        return 0

    monkeypatch.setattr(shell.cli_mod.paths_mod, "require_root", lambda: root)
    monkeypatch.setattr(shell.cli_mod, "cmd_run", fake_cmd_run)

    shell.handle_input("olha o projeto /tmp/app", p)

    assert run_args
    assert run_args[0].maestro is False
    assert run_args[0].no_maestro is False


def test_shell_natural_language_adds_local_project_candidates(tmp_path: Path, monkeypatch):
    app = tmp_path / "app_paty"
    app.mkdir()
    (app / ".git").mkdir()
    (app / "package.json").write_text("{}", encoding="utf-8")

    root = tmp_path / ".burnless"
    p = paths.paths_for(root)
    for key in ("delegations", "logs", "temp", "capsules", "archive", "chat"):
        p[key].mkdir(parents=True, exist_ok=True)
    p["config"].write_text(
        """
project_name: test
agents:
  gold: {name: opus, command: "printf ok", role: strategy}
  silver: {name: sonnet, command: "printf ok", role: execution}
  bronze: {name: haiku, command: "printf ok", role: cheap}
routing:
  gold: []
  silver: [projeto]
  bronze: []
metrics:
  expensive_model_usd_per_million: 15.0
compression:
  mode: balanced
""",
        encoding="utf-8",
    )
    state.save(p["state"], state.DEFAULT_STATE | {"project": "test"})
    metrics.save(p["metrics"], metrics._fresh())
    p["history"].write_text("# history\n", encoding="utf-8")

    monkeypatch.setattr(shell.cli_mod.paths_mod, "require_root", lambda: root)
    monkeypatch.setattr(shell.cli_mod, "cmd_run", lambda args: 0)

    shell.handle_input("poderia ver como está o projeto do app da paty assistente de nutricao?", p)

    delegation_id = state.load(p["state"])["last_delegation"]
    body = (p["delegations"] / f"{delegation_id}.md").read_text(encoding="utf-8")
    assert "Natural Language Preflight" in body
    assert str(app) in body


def test_shell_run_result_shows_audit_and_evidence(tmp_path: Path):
    root = tmp_path / ".burnless"
    p = paths.paths_for(root)
    for key in ("delegations", "logs", "temp", "capsules", "archive", "chat"):
        p[key].mkdir(parents=True, exist_ok=True)
    state.save(p["state"], state.DEFAULT_STATE | {"next": ""})
    metrics.save(p["metrics"], metrics._fresh())
    (p["temp"] / "d001.json").write_text(
        """{
  "id": "d001",
  "status": "OK",
  "summary": "Implemented evidence audit.",
  "evidence": ["pytest tests/test_audit.py"],
  "audit": {"status": "OK"},
  "next": ""
}
""",
        encoding="utf-8",
    )

    out = shell._friendly_run_result(p, "d001", 0)

    assert out.startswith("OK:d001")
    assert "Implemented evidence audit." in out
    assert "Audit:" not in out
    assert "Evidence:" not in out


def test_shell_run_result_shows_audit_feedback_reason(tmp_path: Path):
    root = tmp_path / ".burnless"
    p = paths.paths_for(root)
    for key in ("delegations", "logs", "temp", "capsules", "archive", "chat"):
        p[key].mkdir(parents=True, exist_ok=True)
    state.save(p["state"], state.DEFAULT_STATE | {"next": ""})
    metrics.save(p["metrics"], metrics._fresh())
    (p["temp"] / "d002.json").write_text(
        """{
  "id": "d002",
  "status": "PART",
  "summary": "Evidence audit needs follow-up.",
  "evidence": ["pytest tests/test_audit.py"],
  "audit": {
    "status": "FAIL",
    "feedback": "Add concrete evidence: command, file, or check observed."
  },
  "next": "Add concrete evidence: command, file, or check observed."
}
""",
        encoding="utf-8",
    )

    out = shell._friendly_run_result(p, "d002", 1)

    assert out.startswith("PART:d002")
    assert "Reason: Add concrete evidence: command, file, or check observed." in out


def test_shell_run_result_shows_thought_kind(tmp_path: Path):
    root = tmp_path / ".burnless"
    p = paths.paths_for(root)
    for key in ("delegations", "logs", "temp", "capsules", "archive", "chat"):
        p[key].mkdir(parents=True, exist_ok=True)
    state.save(p["state"], state.DEFAULT_STATE | {"next": ""})
    metrics.save(p["metrics"], metrics._fresh())
    (p["temp"] / "d004.json").write_text(
        """{
  "id": "d004",
  "status": "OK",
  "kind": "thought",
  "summary": "Pensado e registrado.",
  "audit": {"status": "SKIPPED"},
  "next": ""
}
""",
        encoding="utf-8",
    )

    out = shell._friendly_run_result(p, "d004", 0)

    assert out.startswith("THOUGHT:d004")
    assert "Pensado e registrado." in out


def test_shell_run_hides_raw_cmd_run_output(tmp_path: Path, monkeypatch, capsys):
    root = tmp_path / ".burnless"
    p = paths.paths_for(root)
    for key in ("delegations", "logs", "temp", "capsules", "archive", "chat"):
        p[key].mkdir(parents=True, exist_ok=True)
    state.save(p["state"], state.DEFAULT_STATE | {"next": ""})
    metrics.save(p["metrics"], metrics._fresh())
    p["history"].write_text("# history\n", encoding="utf-8")
    (p["temp"] / "d003.json").write_text(
        """{
  "id": "d003",
  "status": "OK",
  "summary": "Tudo feito.",
  "next": ""
}
""",
        encoding="utf-8",
    )

    def fake_cmd_run(args):
        print("Capsule created. Saved 999 burnless tokens.")
        print("999 burnless tokens")
        return 0

    monkeypatch.setattr(shell.cli_mod, "cmd_run", fake_cmd_run)

    shell._run(p, "faça algo", "d003")
    out = capsys.readouterr().out

    assert "[investigando...]" in out
    assert "OK:d003" in out
    assert "Tudo feito." in out
    assert "Capsule created." not in out
