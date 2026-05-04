from pathlib import Path

from burnless import cli, paths


def _paths(tmp_path: Path) -> dict[str, Path]:
    root = tmp_path / ".burnless"
    p = paths.paths_for(root)
    for key in ("delegations", "logs", "temp", "capsules", "archive", "chat"):
        p[key].mkdir(parents=True, exist_ok=True)
    return p


def test_ok_without_evidence_is_downgraded_to_part(tmp_path: Path):
    p = _paths(tmp_path)
    log_path = p["logs"] / "d001.log"
    log_path.write_text("pytest passed\n", encoding="utf-8")

    summary = cli._audit_summary_evidence(
        p,
        cfg={"agents": {"bronze": {"name": "haiku", "command": "haiku -p"}}},
        did="d001",
        prompt="task",
        summary={"id": "d001", "status": "OK", "summary": "done", "issues": []},
        log_path=log_path,
        timeout=30,
        cwd=tmp_path,
    )

    assert summary["status"] == "PART"
    assert "missing_evidence" in summary["issues"]
    assert "Add concrete evidence" in summary["next"]
    assert not (p["temp"] / "d001.audit.json").exists()


def test_auditor_failure_downgrades_ok_and_persists_audit(tmp_path: Path, monkeypatch):
    p = _paths(tmp_path)
    log_path = p["logs"] / "d002.log"
    log_path.write_text("pytest failed\n", encoding="utf-8")

    monkeypatch.setattr(cli.agents_mod, "is_available", lambda cfg: True)
    monkeypatch.setattr(
        cli.agents_mod,
        "run",
        lambda cfg, prompt, *, timeout, cwd: {
            "stdout": '```json\n{"status":"FAIL","summary":"unsupported","evidence_checked":["pytest"],"issues":["no proof"]}\n```',
            "stderr": "",
            "returncode": 0,
        },
    )

    summary = cli._audit_summary_evidence(
        p,
        cfg={"agents": {"bronze": {"name": "haiku", "command": "haiku -p"}}},
        did="d002",
        prompt="task",
        summary={
            "id": "d002",
            "status": "OK",
            "summary": "done",
            "evidence": ["pytest"],
            "issues": [],
        },
        log_path=log_path,
        timeout=30,
        cwd=tmp_path,
    )

    assert summary["status"] == "PART"
    assert summary["audit"]["status"] == "FAIL"
    assert "Add concrete evidence" in summary["next"]
    assert "Add concrete evidence" in summary["audit"]["feedback"]
    assert "audit_failed" in summary["issues"]
    assert (p["temp"] / "d002.audit.json").exists()


def test_auditor_unavailable_keeps_execution_nonfatal(tmp_path: Path, monkeypatch):
    p = _paths(tmp_path)
    log_path = p["logs"] / "d003.log"
    log_path.write_text("pytest passed\n", encoding="utf-8")

    monkeypatch.setattr(cli.agents_mod, "is_available", lambda cfg: False)

    summary = cli._audit_summary_evidence(
        p,
        cfg={"agents": {"bronze": {"name": "haiku", "command": "haiku -p"}}},
        did="d003",
        prompt="task",
        summary={
            "id": "d003",
            "status": "PART",
            "summary": "part done",
            "evidence": ["tests/test_audit.py exists"],
            "issues": [],
        },
        log_path=log_path,
        timeout=30,
        cwd=tmp_path,
    )

    assert summary["status"] == "PART"
    assert summary["audit"]["status"] == "UNAVAILABLE"
    assert summary["audit"]["auditor_tier"] is None
    assert summary["audit"]["attempted_tiers"] == ["bronze"]
    assert "Add concrete evidence" in summary["next"]
    assert "Add concrete evidence" in summary["audit"]["feedback"]
    assert "audit_unavailable" in summary["issues"]
    assert (p["temp"] / "d003.audit.json").exists()


def test_audit_feedback_appends_to_existing_next(tmp_path: Path, monkeypatch):
    p = _paths(tmp_path)
    log_path = p["logs"] / "d004.log"
    log_path.write_text("pytest failed\n", encoding="utf-8")

    monkeypatch.setattr(cli.agents_mod, "is_available", lambda cfg: True)
    monkeypatch.setattr(
        cli.agents_mod,
        "run",
        lambda cfg, prompt, *, timeout, cwd: {
            "stdout": '```json\n{"status":"FAIL","summary":"unsupported","evidence_checked":["pytest"],"issues":["no proof"]}\n```',
            "stderr": "",
            "returncode": 0,
        },
    )

    summary = cli._audit_summary_evidence(
        p,
        cfg={"agents": {"bronze": {"name": "haiku", "command": "haiku -p"}}},
        did="d004",
        prompt="task",
        summary={
            "id": "d004",
            "status": "OK",
            "summary": "done",
            "evidence": ["pytest"],
            "issues": [],
            "next": "Keep the existing follow-up.",
        },
        log_path=log_path,
        timeout=30,
        cwd=tmp_path,
    )

    assert summary["next"].startswith("Keep the existing follow-up.")
    assert "Add concrete evidence" in summary["next"]


def test_bronze_unavailable_escalates_to_silver_ok(tmp_path: Path, monkeypatch):
    p = _paths(tmp_path)
    log_path = p["logs"] / "d005.log"
    log_path.write_text("pytest passed\n", encoding="utf-8")

    monkeypatch.setattr(
        cli.agents_mod,
        "is_available",
        lambda cfg: cfg["name"] == "silver",
    )
    monkeypatch.setattr(
        cli.agents_mod,
        "run",
        lambda cfg, prompt, *, timeout, cwd: {
            "stdout": '```json\n{"status":"OK","summary":"supported","evidence_checked":["pytest"],"issues":[]}\n```',
            "stderr": "",
            "returncode": 0,
        },
    )

    summary = cli._audit_summary_evidence(
        p,
        cfg={
            "agents": {
                "bronze": {"name": "bronze", "command": "bronze -p"},
                "silver": {"name": "silver", "command": "silver -p"},
            }
        },
        did="d005",
        prompt="task",
        summary={
            "id": "d005",
            "status": "OK",
            "summary": "done",
            "evidence": ["pytest"],
            "issues": [],
        },
        log_path=log_path,
        timeout=30,
        cwd=tmp_path,
    )

    assert summary["status"] == "OK"
    assert summary["audit"]["status"] == "OK"
    assert summary["audit"]["auditor_tier"] == "silver"
    assert summary["audit"]["attempted_tiers"] == ["bronze", "silver"]
    assert "audit_unavailable" not in summary["issues"]


def test_bronze_and_silver_unavailable_escalates_to_gold_ok(tmp_path: Path, monkeypatch):
    p = _paths(tmp_path)
    log_path = p["logs"] / "d006.log"
    log_path.write_text("pytest passed\n", encoding="utf-8")

    monkeypatch.setattr(
        cli.agents_mod,
        "is_available",
        lambda cfg: cfg["name"] == "gold",
    )
    monkeypatch.setattr(
        cli.agents_mod,
        "run",
        lambda cfg, prompt, *, timeout, cwd: {
            "stdout": '```json\n{"status":"PASS","summary":"supported","evidence_checked":["pytest"],"issues":[]}\n```',
            "stderr": "",
            "returncode": 0,
        },
    )

    summary = cli._audit_summary_evidence(
        p,
        cfg={
            "agents": {
                "bronze": {"name": "bronze", "command": "bronze -p"},
                "silver": {"name": "silver", "command": "silver -p"},
                "gold": {"name": "gold", "command": "gold -p"},
            }
        },
        did="d006",
        prompt="task",
        summary={
            "id": "d006",
            "status": "OK",
            "summary": "done",
            "evidence": ["pytest"],
            "issues": [],
        },
        log_path=log_path,
        timeout=30,
        cwd=tmp_path,
    )

    assert summary["status"] == "OK"
    assert summary["audit"]["status"] == "PASS"
    assert summary["audit"]["auditor_tier"] == "gold"
    assert summary["audit"]["attempted_tiers"] == ["bronze", "silver", "gold"]
    assert "audit_unavailable" not in summary["issues"]


def test_all_configured_auditors_unavailable_downgrades_ok(tmp_path: Path, monkeypatch):
    p = _paths(tmp_path)
    log_path = p["logs"] / "d007.log"
    log_path.write_text("pytest passed\n", encoding="utf-8")

    monkeypatch.setattr(cli.agents_mod, "is_available", lambda cfg: False)

    summary = cli._audit_summary_evidence(
        p,
        cfg={
            "agents": {
                "bronze": {"name": "bronze", "command": "bronze -p"},
                "silver": {"name": "silver", "command": "silver -p"},
                "gold": {"name": "gold", "command": "gold -p"},
            }
        },
        did="d007",
        prompt="task",
        summary={
            "id": "d007",
            "status": "OK",
            "summary": "done",
            "evidence": ["pytest"],
            "issues": [],
        },
        log_path=log_path,
        timeout=30,
        cwd=tmp_path,
    )

    assert summary["status"] == "PART"
    assert summary["audit"]["status"] == "UNAVAILABLE"
    assert summary["audit"]["auditor_tier"] is None
    assert summary["audit"]["attempted_tiers"] == ["bronze", "silver", "gold"]
    assert "audit_unavailable" in summary["issues"]


def test_multiple_cheap_auditors_escalate_to_silver(tmp_path: Path, monkeypatch):
    p = _paths(tmp_path)
    log_path = p["logs"] / "d008.log"
    log_path.write_text("pytest passed\n", encoding="utf-8")

    monkeypatch.setattr(
        cli.agents_mod,
        "is_available",
        lambda cfg: cfg["name"] == "silver",
    )
    monkeypatch.setattr(
        cli.agents_mod,
        "run",
        lambda cfg, prompt, *, timeout, cwd: {
            "stdout": '```json\n{"status":"OK","summary":"supported","evidence_checked":["pytest"],"issues":[]}\n```',
            "stderr": "",
            "returncode": 0,
        },
    )

    summary = cli._audit_summary_evidence(
        p,
        cfg={
            "audit": {"auditors": ["bronze", "local-bronze", "silver", "gold"]},
            "agents": {
                "bronze": {"name": "bronze", "command": "bronze -p"},
                "local-bronze": {"name": "local-bronze", "command": "local -p"},
                "silver": {"name": "silver", "command": "silver -p"},
                "gold": {"name": "gold", "command": "gold -p"},
            }
        },
        did="d008",
        prompt="task",
        summary={
            "id": "d008",
            "status": "OK",
            "summary": "done",
            "evidence": ["pytest"],
            "issues": [],
        },
        log_path=log_path,
        timeout=30,
        cwd=tmp_path,
    )

    assert summary["status"] == "OK"
    assert summary["audit"]["status"] == "OK"
    assert summary["audit"]["auditor_tier"] == "silver"
    assert summary["audit"]["auditor_name"] == "silver"
    assert summary["audit"]["attempted_tiers"] == ["bronze", "local-bronze", "silver"]
    assert summary["audit"]["attempted_auditors"] == ["bronze", "local-bronze", "silver"]


def test_custom_auditor_name_execution(tmp_path: Path, monkeypatch):
    p = _paths(tmp_path)
    log_path = p["logs"] / "d009.log"
    log_path.write_text("pytest passed\n", encoding="utf-8")

    monkeypatch.setattr(
        cli.agents_mod,
        "is_available",
        lambda cfg: cfg["name"] == "ollama-cheap",
    )
    monkeypatch.setattr(
        cli.agents_mod,
        "run",
        lambda cfg, prompt, *, timeout, cwd: {
            "stdout": '```json\n{"status":"OK","summary":"custom supported","evidence_checked":["pytest"],"issues":[]}\n```',
            "stderr": "",
            "returncode": 0,
        },
    )

    summary = cli._audit_summary_evidence(
        p,
        cfg={
            "audit": {"auditors": ["ollama-cheap", "silver", "gold"]},
            "agents": {
                "ollama-cheap": {"name": "ollama-cheap", "command": "ollama -p"},
                "silver": {"name": "silver", "command": "silver -p"},
                "gold": {"name": "gold", "command": "gold -p"},
            }
        },
        did="d009",
        prompt="task",
        summary={
            "id": "d009",
            "status": "OK",
            "summary": "done",
            "evidence": ["pytest"],
            "issues": [],
        },
        log_path=log_path,
        timeout=30,
        cwd=tmp_path,
    )

    assert summary["status"] == "OK"
    assert summary["audit"]["status"] == "OK"
    assert summary["audit"]["auditor_name"] == "ollama-cheap"
    assert summary["audit"]["attempted_auditors"] == ["ollama-cheap"]

