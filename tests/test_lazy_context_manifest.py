from __future__ import annotations
import json
from pathlib import Path
import pytest

from burnless.cli import _with_runtime_context, _parse_chain_from_delegation


# ---------------------------------------------------------------------------
# _with_runtime_context — manifest generation
# ---------------------------------------------------------------------------


def test_empty_chain_no_manifest(tmp_path):
    """(a) chain vazia → prompt não tem bloco Manifest."""
    prompt = _with_runtime_context(
        "task body",
        project_root=tmp_path,
        burnless_root=tmp_path / ".burnless",
        chain=[],
    )
    assert "## Lazy Context Manifest" not in prompt


def test_none_chain_no_manifest(tmp_path):
    """chain=None (default) → sem bloco Manifest."""
    prompt = _with_runtime_context(
        "task body",
        project_root=tmp_path,
        burnless_root=tmp_path / ".burnless",
    )
    assert "## Lazy Context Manifest" not in prompt


def test_chain_two_ids_manifest_present(tmp_path):
    """(b) chain com 2 ids → prompt tem bloco com paths corretos, paths existem em fixture."""
    burnless = tmp_path / ".burnless"
    capsules = burnless / "capsules"
    delegations = burnless / "delegations"
    capsules.mkdir(parents=True)
    delegations.mkdir(parents=True)

    (capsules / "d042.json").write_text('{"id": "d042"}', encoding="utf-8")
    (capsules / "d038.json").write_text('{"id": "d038"}', encoding="utf-8")
    (delegations / "d042.md").write_text("# Delegation d042", encoding="utf-8")

    prompt = _with_runtime_context(
        "task body",
        project_root=tmp_path,
        burnless_root=burnless,
        chain=["d042", "d038"],
    )

    assert "## Lazy Context Manifest" in prompt
    assert ".burnless/capsules/d042.json" in prompt
    assert ".burnless/capsules/d038.json" in prompt
    assert ".burnless/delegations/d042.md" in prompt

    # verify fixture files actually exist
    assert (capsules / "d042.json").exists()
    assert (capsules / "d038.json").exists()
    assert (delegations / "d042.md").exists()


def test_chain_predecessor_label(tmp_path):
    """First chain entry labeled 'predecessor direto', second labeled 'irmão'."""
    burnless = tmp_path / ".burnless"
    capsules = burnless / "capsules"
    capsules.mkdir(parents=True)
    (capsules / "d042.json").write_text('{"id": "d042"}', encoding="utf-8")
    (capsules / "d038.json").write_text('{"id": "d038"}', encoding="utf-8")
    prompt = _with_runtime_context(
        "task body",
        project_root=tmp_path,
        burnless_root=burnless,
        chain=["d042", "d038"],
    )
    assert "predecessor direto" in prompt
    assert "irmão" in prompt


def test_chain_nonexistent_id_omitted_from_manifest(tmp_path):
    """(c) chain com id inexistente → capsule omitida; bloco não gerado se todos ausentes (P2)."""
    burnless = tmp_path / ".burnless"

    prompt = _with_runtime_context(
        "task body",
        project_root=tmp_path,
        burnless_root=burnless,
        chain=["d999"],
    )

    # P2: non-existent capsule is omitted; no manifest block when all entries are missing
    assert "## Lazy Context Manifest" not in prompt
    assert ".burnless/capsules/d999.json" not in prompt
    assert not (burnless / "capsules" / "d999.json").exists()


# ---------------------------------------------------------------------------
# P2 — chain wiring: capsule as path, not inline
# ---------------------------------------------------------------------------


def test_chain_two_delegations_capsule_as_path(tmp_path):
    """Encadeamento 2 delegações: capsule d001 aparece como path no prompt de d002, não inline."""
    burnless = tmp_path / ".burnless"
    capsules_dir = burnless / "capsules"
    capsules_dir.mkdir(parents=True)

    capsule_content = '{"id": "d001", "status": "OK", "next": "proceed to step 2"}'
    (capsules_dir / "d001.json").write_text(capsule_content, encoding="utf-8")

    prompt = _with_runtime_context(
        "task body for d002",
        project_root=tmp_path,
        burnless_root=burnless,
        chain=["d001"],
    )

    assert "## Lazy Context Manifest" in prompt
    assert ".burnless/capsules/d001.json" in prompt
    # capsule content must NOT be inlined
    assert '"status": "OK"' not in prompt
    assert '"next": "proceed to step 2"' not in prompt


def test_chain_mixed_existing_missing(tmp_path):
    """Chain com um id existente e um ausente: apenas o existente aparece no manifest."""
    burnless = tmp_path / ".burnless"
    capsules_dir = burnless / "capsules"
    capsules_dir.mkdir(parents=True)

    (capsules_dir / "d010.json").write_text('{"id": "d010"}', encoding="utf-8")
    # d999 is intentionally absent

    prompt = _with_runtime_context(
        "task body",
        project_root=tmp_path,
        burnless_root=burnless,
        chain=["d010", "d999"],
    )

    assert "## Lazy Context Manifest" in prompt
    assert ".burnless/capsules/d010.json" in prompt
    assert ".burnless/capsules/d999.json" not in prompt


def test_sequential_two_delegations_manifest_chain(tmp_path):
    """Sequência: 2 delegações. Prompt da 2ª contém manifest com path da capsule da 1ª, sem inlinar conteúdo."""
    burnless = tmp_path / ".burnless"
    capsules_dir = burnless / "capsules"
    delegations_dir = burnless / "delegations"
    capsules_dir.mkdir(parents=True)
    delegations_dir.mkdir(parents=True)

    # Simula capsule escrita pelo dispatcher após execução OK de d001
    d001_data = {"id": "d001", "status": "OK", "objective": "step one complete"}
    (capsules_dir / "d001.json").write_text(json.dumps(d001_data), encoding="utf-8")
    (delegations_dir / "d001.md").write_text("# Delegation d001", encoding="utf-8")

    # Prompt para d002 com chain=["d001"] (como dispatcher.run_all passaria)
    prompt_d002 = _with_runtime_context(
        "task body for d002",
        project_root=tmp_path,
        burnless_root=burnless,
        chain=["d001"],
    )

    assert "## Lazy Context Manifest" in prompt_d002
    assert ".burnless/capsules/d001.json" in prompt_d002
    # Capsule content must NOT be inlined — worker reads it via Read tool
    assert "step one complete" not in prompt_d002
    # File must be readable from cwd (worker can access it)
    assert (capsules_dir / "d001.json").exists()
    assert json.loads((capsules_dir / "d001.json").read_text())["status"] == "OK"


# ---------------------------------------------------------------------------
# _parse_chain_from_delegation
# ---------------------------------------------------------------------------


def test_parse_chain_two_ids():
    md = "---\nchain: [d042, d038]\n---\n# Delegation d100\n"
    assert _parse_chain_from_delegation(md) == ["d042", "d038"]


def test_parse_chain_single_id():
    md = "---\nchain: [d042]\n---\n# Delegation d100\n"
    assert _parse_chain_from_delegation(md) == ["d042"]


def test_parse_chain_no_frontmatter():
    md = "# Delegation d100\n\n## Goal\ndo stuff\n"
    assert _parse_chain_from_delegation(md) == []


def test_parse_chain_frontmatter_without_chain():
    md = "---\nsome: value\n---\n# Delegation d100\n"
    assert _parse_chain_from_delegation(md) == []


# ---------------------------------------------------------------------------
# P3 — BLK lazy fetch fallback: cmd_run detects and retries with full push
# ---------------------------------------------------------------------------

import argparse
from burnless.cli import cmd_run
from burnless import config as config_mod
from burnless import state as state_mod
from burnless import metrics as metrics_mod


def _init_burnless(tmp_path):
    burnless = tmp_path / ".burnless"
    for d in ("delegations", "logs", "temp", "capsules", "archive", "chat", "runs"):
        (burnless / d).mkdir(parents=True, exist_ok=True)
    config_mod.write_default(burnless / "config.yaml")
    st = dict(state_mod.DEFAULT_STATE)
    st["project"] = "test"
    state_mod.save(burnless / "state.json", st)
    metrics_mod.save(burnless / "metrics.json", metrics_mod._fresh())
    return burnless


def _make_delegation_md(did):
    return (
        f"# Delegation {did}\n\n"
        f"- **agent:** claude (silver)\n\n"
        f"## Goal\n\nTest goal.\n\n"
        f"## Task\n\nDo the task.\n\n"
        f"## Constraints\n\nBe concise.\n\n"
        f"## Success criteria\n\ntask completed.\n\n"
        f"## Required final output\n\n"
        f'```json\n{{"id": "{did}", "status": "OK|BLK", "kind": "execution", '
        f'"summary": "", "files_touched": [], "validated": [], "evidence": [], '
        f'"issues": [], "next": ""}}\n```\n'
    )


def _run_result(stdout, *, returncode=0):
    return {
        "agent": "test",
        "command": ["claude"],
        "stdout": stdout,
        "stderr": "",
        "returncode": returncode,
        "started_at": "2026-01-01T00:00:00+00:00",
        "ended_at": "2026-01-01T00:00:01+00:00",
        "duration_s": 1.0,
        "interrupted": False,
        "stale": False,
    }


def _blk_json(did, issues, evidence=None):
    data = {
        "id": did,
        "status": "BLK",
        "kind": "execution",
        "summary": "blocked by missing lazy context",
        "files_touched": [],
        "validated": [],
        "evidence": evidence or issues,
        "issues": issues,
        "next": "",
    }
    return f"```json\n{json.dumps(data)}\n```"


def _ok_json(did, evidence_path):
    data = {
        "id": did,
        "status": "OK",
        "kind": "execution",
        "summary": "done",
        "files_touched": ["foo.py"],
        "validated": [],
        "evidence": [evidence_path],
        "issues": [],
        "next": "",
    }
    return f"```json\n{json.dumps(data)}\n```"


def _raise(*args, **kwargs):
    raise Exception("no live runner in test")


def test_blk_lazy_fetch_triggers_retry(tmp_path, monkeypatch):
    """BLK with 'lazy fetch failed' in issues → re-runs once with full push → OK."""
    monkeypatch.chdir(tmp_path)
    burnless = _init_burnless(tmp_path)

    did = "d001"
    deleg = burnless / "delegations" / f"{did}.md"
    deleg.write_text(_make_delegation_md(did), encoding="utf-8")

    # Capsule the worker claims it could not fetch
    cap = burnless / "capsules" / "d099.json"
    cap.write_text('{"id": "d099", "status": "OK", "next": "step done"}', encoding="utf-8")

    # Real file used as evidence so the fast-path audit check passes
    evidence_file = tmp_path / "result.txt"
    evidence_file.write_text("done\n", encoding="utf-8")
    # QTP-A: filesystem-first auditor needs files_touched to actually exist
    (tmp_path / "foo.py").write_text("ok\n", encoding="utf-8")

    call_count = 0

    def mock_agents_run(agent_cfg, prompt, *, timeout=None, cwd=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _run_result(_blk_json(did, ["lazy fetch failed: .burnless/capsules/d099.json"]))
        return _run_result(_ok_json(did, str(evidence_file)))

    monkeypatch.setattr("burnless.agents.is_available", lambda cfg: True)
    monkeypatch.setattr("burnless.agents.run", mock_agents_run)
    monkeypatch.setattr("burnless.live_runner.run_with_live_panel", _raise)

    args = argparse.Namespace(
        id=did,
        dry_run=False,
        timeout=60,
        stale_timeout_s=None,
        maestro=False,
        no_maestro=True,
        mode="plain",
        progress=None,
    )
    rc = cmd_run(args)

    assert call_count == 2, f"expected 2 agent runs (initial BLK + fallback retry), got {call_count}"
    assert rc == 0, f"expected exit 0 (OK), got {rc}"


def test_blk_other_cause_no_retry(tmp_path, monkeypatch):
    """BLK by a non-lazy cause does NOT trigger a retry."""
    monkeypatch.chdir(tmp_path)
    burnless = _init_burnless(tmp_path)

    did = "d002"
    deleg = burnless / "delegations" / f"{did}.md"
    deleg.write_text(_make_delegation_md(did), encoding="utf-8")

    call_count = 0

    def mock_agents_run(agent_cfg, prompt, *, timeout=None, cwd=None):
        nonlocal call_count
        call_count += 1
        return _run_result(_blk_json(did, ["insufficient permissions to proceed"]))

    monkeypatch.setattr("burnless.agents.is_available", lambda cfg: True)
    monkeypatch.setattr("burnless.agents.run", mock_agents_run)
    monkeypatch.setattr("burnless.live_runner.run_with_live_panel", _raise)

    args = argparse.Namespace(
        id=did,
        dry_run=False,
        timeout=60,
        stale_timeout_s=None,
        maestro=False,
        no_maestro=True,
        mode="plain",
        progress=None,
    )
    rc = cmd_run(args)

    assert call_count == 1, f"expected 1 agent run (no retry for non-lazy BLK), got {call_count}"
    assert rc == 1, f"expected exit 1 (BLK), got {rc}"
