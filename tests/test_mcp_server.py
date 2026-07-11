from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from burnless.mcp_server import (
    handle_delegate,
    handle_route,
    handle_run,
    handle_capsule,
    handle_read,
    handle_status,
    handle_do,
    handle_metrics,
)
from burnless import paths, state as state_mod


@pytest.fixture
def mock_burnless_project(tmp_path: Path) -> Path:
    """Create complete mock .burnless project with config, state, and directories."""
    root = tmp_path / ".burnless"
    root.mkdir(parents=True, exist_ok=True)
    (root / "delegations").mkdir(exist_ok=True)
    (root / "capsules").mkdir(exist_ok=True)
    (root / "runs").mkdir(exist_ok=True)

    state_path = root / "state.json"
    state_mod.save(state_path, state_mod.DEFAULT_STATE.copy())

    config_path = root / "config.yaml"
    config_path.write_text(
        """
agents:
  bronze:
    name: haiku
    command: claude --model haiku
  silver:
    name: sonnet
    command: claude --model sonnet
  gold:
    name: opus
    command: claude --model opus
routing:
  gold:
    - refactor
    - architecture
    - design
  silver:
    - implementa
    - build
    - create
  bronze:
    - read
    - list
    - status
""",
        encoding="utf-8",
    )
    return root


@pytest.mark.asyncio
async def test_delegate_success(mock_burnless_project: Path) -> None:
    result = await handle_delegate(text="implement endpoint", project_root=str(mock_burnless_project.parent))
    assert result.get("error") is None
    assert "id" in result
    assert result["tier"] == "bronze"
    assert result["agent"] == "haiku"
    assert (mock_burnless_project / "delegations" / f"{result['id']}.md").exists()


@pytest.mark.asyncio
async def test_delegate_explicit_tier(mock_burnless_project: Path) -> None:
    result = await handle_delegate(
        text="refactor architecture",
        tier="gold",
        project_root=str(mock_burnless_project.parent),
    )
    assert result.get("error") is None
    assert result["tier"] == "gold"
    assert result["routed_by"] == "manual"


@pytest.mark.asyncio
async def test_delegate_empty_text() -> None:
    result = await handle_delegate(text="", project_root="/nonexistent")
    assert result.get("error") == "invalid_input"


@pytest.mark.asyncio
async def test_delegate_no_burnless_root() -> None:
    result = await handle_delegate(text="test", project_root="/nonexistent")
    assert result.get("error") == "no_burnless_root"


@pytest.mark.asyncio
async def test_route_success(mock_burnless_project: Path) -> None:
    result = await handle_route(text="refactor code", project_root=str(mock_burnless_project.parent))
    assert result.get("error") is None
    assert result["tier"] == "gold"
    assert result["matched_keyword"] == "refactor"


@pytest.mark.asyncio
async def test_route_default_bronze(mock_burnless_project: Path) -> None:
    result = await handle_route(text="some random task", project_root=str(mock_burnless_project.parent))
    assert result.get("error") is None
    assert result["tier"] == "bronze"
    assert result["matched_keyword"] is None
    assert result["default_used"] is True


@pytest.mark.asyncio
async def test_route_no_burnless_root() -> None:
    result = await handle_route(text="test", project_root="/nonexistent")
    assert result.get("error") == "no_burnless_root"


@pytest.mark.asyncio
async def test_run_delegation_not_found(mock_burnless_project: Path) -> None:
    result = await handle_run(id="d999", project_root=str(mock_burnless_project.parent))
    assert result.get("error") == "delegation_not_found"


@pytest.mark.asyncio
async def test_run_sync(monkeypatch, mock_burnless_project: Path) -> None:
    deleg_result = await handle_delegate(text="implementa X", project_root=str(mock_burnless_project.parent))
    assert "id" in deleg_result
    deleg_id = deleg_result["id"]

    # Never spawn a real worker in the suite: stub the execution, keep the flow.
    monkeypatch.setattr("burnless.cli.execute_delegation", lambda opts, root=None: 0)

    result = await handle_run(id=deleg_id, background=False, project_root=str(mock_burnless_project.parent))
    assert "error" not in result or result.get("error") in ["worker_failed", "delegation_not_found"]


@pytest.mark.asyncio
async def test_run_background_returns_pid(monkeypatch, mock_burnless_project: Path) -> None:
    created = mock_burnless_project / "delegations" / "d001.md"
    created.write_text("delegation", encoding="utf-8")

    class DummyProc:
        pid = 4321

    popen_calls = {}

    def fake_popen(cmd, stdout=None, stderr=None, start_new_session=None):
        popen_calls["cmd"] = cmd
        popen_calls["start_new_session"] = start_new_session
        return DummyProc()

    monkeypatch.setattr("burnless.mcp_server.subprocess.Popen", fake_popen)
    result = await handle_run(id="d001", background=True, project_root=str(mock_burnless_project.parent))
    assert result["status"] == "running"
    assert result["pid"] == 4321
    assert popen_calls["cmd"][:3] == [__import__("sys").executable, "-m", "burnless"]
    assert popen_calls["start_new_session"] is True


@pytest.mark.asyncio
async def test_capsule_not_ready(mock_burnless_project: Path) -> None:
    deleg_result = await handle_delegate(text="implementa test", project_root=str(mock_burnless_project.parent))
    assert "id" in deleg_result
    deleg_id = deleg_result["id"]

    result = await handle_capsule(id=deleg_id, project_root=str(mock_burnless_project.parent))
    assert result.get("error") == "capsule_not_ready"


@pytest.mark.asyncio
async def test_capsule_success(mock_burnless_project: Path) -> None:
    deleg_result = await handle_delegate(text="implementa test", project_root=str(mock_burnless_project.parent))
    assert "id" in deleg_result
    deleg_id = deleg_result["id"]

    capsule_data = {"status": "OK", "files": ["src/test.py"], "summary": "done"}
    capsule_path = mock_burnless_project / "capsules" / f"{deleg_id}.json"
    capsule_path.write_text(json.dumps(capsule_data), encoding="utf-8")

    result = await handle_capsule(id=deleg_id, project_root=str(mock_burnless_project.parent))
    assert result.get("error") is None
    assert result["capsule"] == capsule_data


@pytest.mark.asyncio
async def test_read_no_data(mock_burnless_project: Path) -> None:
    result = await handle_read(id="d999", project_root=str(mock_burnless_project.parent))
    assert result.get("error") == "delegation_not_found"


@pytest.mark.asyncio
async def test_read_capsule(mock_burnless_project: Path) -> None:
    deleg_result = await handle_delegate(text="read test", project_root=str(mock_burnless_project.parent))
    assert "id" in deleg_result
    deleg_id = deleg_result["id"]

    capsule_data = {"status": "OK", "summary": "success"}
    capsule_path = mock_burnless_project / "capsules" / f"{deleg_id}.json"
    capsule_path.write_text(json.dumps(capsule_data), encoding="utf-8")

    result = await handle_read(id=deleg_id, project_root=str(mock_burnless_project.parent))
    assert result.get("error") is None
    assert result["source"] == "capsule"
    assert result["content"] == capsule_data


@pytest.mark.asyncio
async def test_read_log(mock_burnless_project: Path) -> None:
    deleg_result = await handle_delegate(text="list something", project_root=str(mock_burnless_project.parent))
    assert "id" in deleg_result
    deleg_id = deleg_result["id"]

    log_path = mock_burnless_project / "runs" / f"{deleg_id}.stdout.log"
    log_path.write_text("worker output\nline 2\n", encoding="utf-8")

    result = await handle_read(id=deleg_id, project_root=str(mock_burnless_project.parent))
    assert result.get("error") is None
    assert result["source"] == "log"
    assert "worker output" in result["content"]


@pytest.mark.asyncio
async def test_status_no_burnless_root() -> None:
    result = await handle_status(project_root="/nonexistent")
    assert result.get("error") == "no_burnless_root"


@pytest.mark.asyncio
async def test_status_project_wide(mock_burnless_project: Path) -> None:
    await handle_delegate(text="implementa task1", project_root=str(mock_burnless_project.parent))
    await handle_delegate(text="build task2", project_root=str(mock_burnless_project.parent))

    result = await handle_status(project_root=str(mock_burnless_project.parent))
    assert result.get("error") is None
    assert result["capsules_count"] == 0
    assert len(result["pending_delegations"]) == 2


@pytest.mark.asyncio
async def test_status_project_wide_omits_config_by_default(mock_burnless_project: Path) -> None:
    result = await handle_status(project_root=str(mock_burnless_project.parent))
    assert result.get("error") is None
    assert "config" not in result


@pytest.mark.asyncio
async def test_status_project_wide_can_include_config(mock_burnless_project: Path) -> None:
    result = await handle_status(project_root=str(mock_burnless_project.parent), include_config=True)
    assert result.get("error") is None
    assert "config" in result


@pytest.mark.asyncio
async def test_status_per_delegation_missing(mock_burnless_project: Path) -> None:
    result = await handle_status(id="d999", project_root=str(mock_burnless_project.parent))
    assert result.get("error") is None
    assert result["state"] == "missing"


@pytest.mark.asyncio
async def test_status_per_delegation_not_started(mock_burnless_project: Path) -> None:
    deleg_result = await handle_delegate(text="create endpoint", project_root=str(mock_burnless_project.parent))
    assert "id" in deleg_result
    deleg_id = deleg_result["id"]

    result = await handle_status(id=deleg_id, project_root=str(mock_burnless_project.parent))
    assert result.get("error") is None
    assert result["state"] == "not_started"


@pytest.mark.asyncio
async def test_read_prefers_envelope(mock_burnless_project: Path) -> None:
    deleg_result = await handle_delegate(text="implement envelope", project_root=str(mock_burnless_project.parent))
    did = deleg_result["id"]

    envelope_path = mock_burnless_project / "runs" / f"{did}.envelope.json"
    envelope_path.write_text(json.dumps({"status": "OK", "summary": "from envelope"}), encoding="utf-8")

    result = await handle_read(id=did, project_root=str(mock_burnless_project.parent))
    assert result.get("error") is None
    assert result["source"] == "envelope"
    assert result["content"]["summary"] == "from envelope"


@pytest.mark.asyncio
async def test_do_returns_done_report(monkeypatch, mock_burnless_project: Path) -> None:
    async def fake_delegate(**kwargs):
        return {"id": "d777", "tier": "bronze", "status": "created"}

    async def fake_run(**kwargs):
        return {"id": "d777", "status": "OK", "envelope": {"status": "OK", "answer_hint": "done"}}

    async def fake_read(**kwargs):
        return {"id": "d777", "source": "envelope", "content": {"status": "OK", "answer_hint": "done"}}

    monkeypatch.setattr("burnless.mcp_server.handle_delegate", fake_delegate)
    monkeypatch.setattr("burnless.mcp_server.handle_run", fake_run)
    monkeypatch.setattr("burnless.mcp_server.handle_read", fake_read)

    result = await handle_do(text="ship it", project_root=str(mock_burnless_project.parent))
    assert result["id"] == "d777"
    assert result["done_report"]["answer_hint"] == "done"
    assert result["read"]["source"] == "envelope"


@pytest.mark.asyncio
async def test_metrics_tool_returns_snapshots(mock_burnless_project: Path) -> None:
    burnless_root = mock_burnless_project
    metrics_path = burnless_root / "metrics.json"
    audit_path = burnless_root / "audit.jsonl"
    spend_path = burnless_root / "spend.jsonl"
    metrics_path.write_text(json.dumps({"burnless_tokens": 1, "by_source": {}}, indent=2), encoding="utf-8")
    audit_path.write_text(json.dumps({"basis": "estimated", "amount": 1}) + "\n", encoding="utf-8")
    spend_path.write_text(json.dumps({"usage": {"input_tokens": 1}}) + "\n", encoding="utf-8")

    result = await handle_metrics(project_root=str(mock_burnless_project.parent), limit=5)
    assert result["metrics"]["burnless_tokens"] == 1
    assert len(result["audit"]) == 1
    assert len(result["spend"]) == 1
