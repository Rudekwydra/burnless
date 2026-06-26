from pathlib import Path

import pytest

from burnless import cli, paths
from burnless import delegations as deleg_mod


def _paths(tmp_path: Path) -> dict[str, Path]:
    root = tmp_path / ".burnless"
    p = paths.paths_for(root)
    for key in ("delegations", "logs", "temp", "capsules", "archive", "chat"):
        p[key].mkdir(parents=True, exist_ok=True)
    return p


@pytest.mark.skip(reason="v0.8: envelope killed; OK without evidence stays OK. Audit (when enabled) derives from git diff.")
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


def test_thought_only_summary_skips_execution_evidence_audit(tmp_path: Path):
    p = _paths(tmp_path)
    log_path = p["logs"] / "d001.log"
    log_path.write_text("thinking only\n", encoding="utf-8")

    summary = cli._audit_summary_evidence(
        p,
        cfg={"agents": {"bronze": {"name": "haiku", "command": "haiku -p"}}},
        did="d001",
        prompt="planeje a arquitetura do monitor",
        summary={
            "id": "d001",
            "status": "OK",
            "kind": "thought",
            "summary": "Proposed a split between thought and execution reports.",
            "issues": [],
        },
        log_path=log_path,
        timeout=30,
        cwd=tmp_path,
    )

    assert summary["status"] == "OK"
    assert summary["kind"] == "thought"
    assert summary["audit"]["status"] == "SKIPPED"
    assert summary["audit"]["auditor_tier"] is None
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
        cfg={"audit": {"llm_ladder": True}, "agents": {"bronze": {"name": "haiku", "command": "haiku -p"}}},
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
    # canonical verdict vocabulary is OK/ERR (legacy PASS/FAIL retired)
    assert summary["audit"]["status"] == "ERR"
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
        cfg={"audit": {"llm_ladder": True}, "agents": {"bronze": {"name": "haiku", "command": "haiku -p"}}},
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
        cfg={"audit": {"llm_ladder": True}, "agents": {"bronze": {"name": "haiku", "command": "haiku -p"}}},
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
            "audit": {"llm_ladder": True},
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
            "audit": {"llm_ladder": True},
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
    # canonical verdict vocabulary is OK/ERR (legacy PASS/FAIL retired)
    assert summary["audit"]["status"] == "OK"
    assert summary["audit"]["auditor_tier"] == "gold"
    assert summary["audit"]["attempted_tiers"] == ["bronze", "silver", "gold"]
    assert "audit_unavailable" not in summary["issues"]


def test_write_log_persists_report_kind(tmp_path: Path):
    log_path = tmp_path / "d001.log"
    deleg_mod.write_log(
        log_path,
        {
            "agent": "sonnet",
            "command": ["claude", "-p"],
            "kind": "thought",
            "returncode": 0,
            "duration_s": 1.23,
            "started_at": "2026-05-05T00:00:00+00:00",
            "ended_at": "2026-05-05T00:00:01+00:00",
            "stdout": "ok",
            "stderr": "",
        },
    )

    text = log_path.read_text(encoding="utf-8")
    assert "# kind: thought" in text


def test_all_configured_auditors_unavailable_downgrades_ok(tmp_path: Path, monkeypatch):
    p = _paths(tmp_path)
    log_path = p["logs"] / "d007.log"
    log_path.write_text("pytest passed\n", encoding="utf-8")

    monkeypatch.setattr(cli.agents_mod, "is_available", lambda cfg: False)

    summary = cli._audit_summary_evidence(
        p,
        cfg={
            "audit": {"llm_ladder": True},
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
            "audit": {"llm_ladder": True, "auditors": ["bronze", "local-bronze", "silver", "gold"]},
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
            "audit": {"llm_ladder": True, "auditors": ["ollama-cheap", "silver", "gold"]},
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


def test_llm_ladder_disabled_by_default_skips_audit(tmp_path: Path, monkeypatch):
    p = _paths(tmp_path)
    log_path = p["logs"] / "d010.log"
    log_path.write_text("pytest passed\n", encoding="utf-8")

    def _boom(*a, **k):
        raise AssertionError("LLM auditor must not run when llm_ladder is off")
    monkeypatch.setattr(cli.agents_mod, "is_available", lambda cfg: True)
    monkeypatch.setattr(cli.agents_mod, "run", _boom)

    summary = cli._audit_summary_evidence(
        p,
        cfg={"agents": {"bronze": {"name": "bronze", "command": "bronze -p"}}},
        did="d010",
        prompt="task",
        summary={"id": "d010", "status": "OK", "summary": "done", "evidence": ["some prose with no verifiable token"], "issues": []},
        log_path=log_path,
        timeout=30,
        cwd=tmp_path,
    )

    assert summary["status"] == "OK"
    assert summary["audit"]["status"] == "SKIPPED"
    assert "llm_ladder" in summary["audit"]["summary"].lower() or "ladder disabled" in summary["audit"]["summary"].lower()
