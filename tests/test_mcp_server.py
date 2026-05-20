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
)
from burnless import paths, state as state_mod


@pytest.fixture
def burnless_root(tmp_path: Path) -> Path:
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
tiers:
  bronze:
    agent: claude-haiku-4-5
  silver:
    agent: claude-sonnet-4-6
  gold:
    agent: claude-opus-4-7
routing:
  gold:
    - refactor
    - architecture
""",
        encoding="utf-8",
    )
    return root


@pytest.mark.asyncio
async def test_delegate_success(burnless_root: Path) -> None:
    result = await handle_delegate(text="implement endpoint", project_root=str(burnless_root.parent))
    assert result.get("error") is None
    assert "id" in result
    assert result["tier"] == "bronze"
    assert result["agent"] == "claude-haiku-4-5"
    assert (burnless_root / "delegations" / f"{result['id']}.md").exists()


@pytest.mark.asyncio
async def test_delegate_explicit_tier(burnless_root: Path) -> None:
    result = await handle_delegate(
        text="refactor architecture",
        tier="gold",
        project_root=str(burnless_root.parent),
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
async def test_route_success(burnless_root: Path) -> None:
    result = await handle_route(text="refactor code", project_root=str(burnless_root.parent))
    assert result.get("error") is None
    assert result["tier"] == "gold"
    assert result["matched_keyword"] == "refactor"


@pytest.mark.asyncio
async def test_route_default_bronze(burnless_root: Path) -> None:
    result = await handle_route(text="some random task", project_root=str(burnless_root.parent))
    assert result.get("error") is None
    assert result["tier"] == "bronze"
    assert result["matched_keyword"] is None
    assert result["default_used"] is True


@pytest.mark.asyncio
async def test_route_no_burnless_root() -> None:
    result = await handle_route(text="test", project_root="/nonexistent")
    assert result.get("error") == "no_burnless_root"


@pytest.mark.asyncio
async def test_run_delegation_not_found(burnless_root: Path) -> None:
    result = await handle_run(id="d999", project_root=str(burnless_root.parent))
    assert result.get("error") == "delegation_not_found"


@pytest.mark.asyncio
async def test_run_sync(burnless_root: Path) -> None:
    deleg_result = await handle_delegate(text="test task", project_root=str(burnless_root.parent))
    deleg_id = deleg_result["id"]

    capsule_path = burnless_root / "capsules" / f"{deleg_id}.json"
    capsule_path.write_text(json.dumps({"status": "OK", "files": []}), encoding="utf-8")

    result = await handle_run(id=deleg_id, background=False, project_root=str(burnless_root.parent))
    assert "error" not in result or result.get("error") == "worker_failed"


@pytest.mark.asyncio
async def test_capsule_not_ready(burnless_root: Path) -> None:
    deleg_result = await handle_delegate(text="test", project_root=str(burnless_root.parent))
    deleg_id = deleg_result["id"]

    result = await handle_capsule(id=deleg_id, project_root=str(burnless_root.parent))
    assert result.get("error") == "capsule_not_ready"


@pytest.mark.asyncio
async def test_capsule_success(burnless_root: Path) -> None:
    deleg_result = await handle_delegate(text="test", project_root=str(burnless_root.parent))
    deleg_id = deleg_result["id"]

    capsule_data = {"status": "OK", "files": ["src/test.py"], "summary": "done"}
    capsule_path = burnless_root / "capsules" / f"{deleg_id}.json"
    capsule_path.write_text(json.dumps(capsule_data), encoding="utf-8")

    result = await handle_capsule(id=deleg_id, project_root=str(burnless_root.parent))
    assert result.get("error") is None
    assert result["capsule"] == capsule_data


@pytest.mark.asyncio
async def test_read_no_data(burnless_root: Path) -> None:
    result = await handle_read(id="d999", project_root=str(burnless_root.parent))
    assert result.get("error") == "delegation_not_found"


@pytest.mark.asyncio
async def test_read_capsule(burnless_root: Path) -> None:
    deleg_result = await handle_delegate(text="test", project_root=str(burnless_root.parent))
    deleg_id = deleg_result["id"]

    capsule_data = {"status": "OK", "summary": "success"}
    capsule_path = burnless_root / "capsules" / f"{deleg_id}.json"
    capsule_path.write_text(json.dumps(capsule_data), encoding="utf-8")

    result = await handle_read(id=deleg_id, project_root=str(burnless_root.parent))
    assert result.get("error") is None
    assert result["source"] == "capsule"
    assert result["content"] == capsule_data


@pytest.mark.asyncio
async def test_read_log(burnless_root: Path) -> None:
    deleg_result = await handle_delegate(text="test", project_root=str(burnless_root.parent))
    deleg_id = deleg_result["id"]

    log_path = burnless_root / "runs" / f"{deleg_id}.stdout.log"
    log_path.write_text("worker output\nline 2\n", encoding="utf-8")

    result = await handle_read(id=deleg_id, project_root=str(burnless_root.parent))
    assert result.get("error") is None
    assert result["source"] == "log"
    assert "worker output" in result["content"]


@pytest.mark.asyncio
async def test_status_no_burnless_root() -> None:
    result = await handle_status(project_root="/nonexistent")
    assert result.get("error") == "no_burnless_root"


@pytest.mark.asyncio
async def test_status_project_wide(burnless_root: Path) -> None:
    await handle_delegate(text="task 1", project_root=str(burnless_root.parent))
    await handle_delegate(text="task 2", project_root=str(burnless_root.parent))

    result = await handle_status(project_root=str(burnless_root.parent))
    assert result.get("error") is None
    assert result["capsules_count"] == 0
    assert len(result["pending_delegations"]) == 2


@pytest.mark.asyncio
async def test_status_per_delegation_missing(burnless_root: Path) -> None:
    result = await handle_status(id="d999", project_root=str(burnless_root.parent))
    assert result.get("error") is None
    assert result["state"] == "missing"


@pytest.mark.asyncio
async def test_status_per_delegation_not_started(burnless_root: Path) -> None:
    deleg_result = await handle_delegate(text="test", project_root=str(burnless_root.parent))
    deleg_id = deleg_result["id"]

    result = await handle_status(id=deleg_id, project_root=str(burnless_root.parent))
    assert result.get("error") is None
    assert result["state"] == "not_started"
