from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from burnless.mcp_server import (
    handle_retrieve,
    handle_search_capsules,
    handle_explain_capsule,
)
from burnless import retrieve as retrieve_mod, state as state_mod


@pytest.fixture
def mock_burnless_project(tmp_path: Path) -> Path:
    """Create mock .burnless project with config, state, and retrieve index."""
    root = tmp_path / ".burnless"
    root.mkdir(parents=True, exist_ok=True)
    (root / "delegations").mkdir(exist_ok=True)
    (root / "capsules").mkdir(exist_ok=True)
    (root / "runs").mkdir(exist_ok=True)
    (root / "retrieve").mkdir(exist_ok=True)

    state_path = root / "state.json"
    state_mod.save(state_path, state_mod.DEFAULT_STATE.copy())

    config_path = root / "config.yaml"
    config_path.write_text(
        """
agents:
  bronze:
    name: haiku
    command: claude --model haiku
routing:
  bronze:
    - read
""",
        encoding="utf-8",
    )
    return root


@pytest.mark.asyncio
async def test_handle_retrieve_no_burnless_root() -> None:
    result = await handle_retrieve(project_root="/nonexistent")
    assert result.get("error") == "no_burnless_root"


@pytest.mark.asyncio
async def test_handle_retrieve_privacy_gate_disabled(mock_burnless_project: Path) -> None:
    config_path = mock_burnless_project / "config.yaml"
    config_path.write_text(
        """
privacy:
  raw_retention: none
""",
        encoding="utf-8",
    )

    result = await handle_retrieve(project_root=str(mock_burnless_project.parent))
    assert result.get("error") == "raw_retention_disabled"
    assert result.get("capsule_available") is True


@pytest.mark.asyncio
async def test_handle_retrieve_empty_index(mock_burnless_project: Path) -> None:
    result = await handle_retrieve(query="test", project_root=str(mock_burnless_project.parent))
    assert result.get("error") is None
    assert result["count"] == 0
    assert result["results"] == []


@pytest.mark.asyncio
async def test_handle_retrieve_with_snippets(mock_burnless_project: Path) -> None:
    retrieve_mod.index_record(
        mock_burnless_project,
        delegation_id="d001",
        kind="input",
        raw_ref="/tmp/spec.md",
        content="This is a test spec content",
        entities=["testspec"],
        files=["spec.md"],
    )

    result = await handle_retrieve(
        query="testspec",
        project_root=str(mock_burnless_project.parent),
    )
    assert result.get("error") is None
    assert result["count"] == 1
    assert len(result["results"]) == 1
    assert "snippet" in result["results"][0]
    assert "This is a test" in result["results"][0]["snippet"]


@pytest.mark.asyncio
async def test_handle_retrieve_max_chars_limit(mock_burnless_project: Path) -> None:
    long_content = "x" * 10000
    retrieve_mod.index_record(
        mock_burnless_project,
        delegation_id="d002",
        kind="output",
        content=long_content,
        entities=["longoutput"],
    )

    result = await handle_retrieve(
        query="longoutput",
        project_root=str(mock_burnless_project.parent),
        max_chars=50,
    )
    assert result.get("error") is None
    assert len(result["results"]) == 1
    snippet = result["results"][0]["snippet"]
    assert len(snippet) <= 50


@pytest.mark.asyncio
async def test_handle_retrieve_filter_by_delegation_id(mock_burnless_project: Path) -> None:
    retrieve_mod.index_record(
        mock_burnless_project,
        delegation_id="d001",
        kind="input",
        content="content for d001",
        entities=["e1"],
    )
    retrieve_mod.index_record(
        mock_burnless_project,
        delegation_id="d002",
        kind="input",
        content="content for d002",
        entities=["e2"],
    )

    result = await handle_retrieve(
        id="d001",
        project_root=str(mock_burnless_project.parent),
    )
    assert result.get("error") is None
    assert result["count"] == 1
    assert result["results"][0]["delegation_id"] == "d001"


@pytest.mark.asyncio
async def test_handle_retrieve_filter_by_entity(mock_burnless_project: Path) -> None:
    retrieve_mod.index_record(
        mock_burnless_project,
        delegation_id="d001",
        kind="evidence",
        content="evidence1",
        entities=["auth", "model"],
    )
    retrieve_mod.index_record(
        mock_burnless_project,
        delegation_id="d002",
        kind="evidence",
        content="evidence2",
        entities=["api"],
    )

    result = await handle_retrieve(
        entity="auth",
        project_root=str(mock_burnless_project.parent),
    )
    assert result.get("error") is None
    assert result["count"] == 1
    assert "auth" in result["results"][0]["entities"]


@pytest.mark.asyncio
async def test_handle_retrieve_filter_by_file(mock_burnless_project: Path) -> None:
    retrieve_mod.index_record(
        mock_burnless_project,
        delegation_id="d001",
        kind="evidence",
        content="file1 evidence",
        files=["main.py", "utils.py"],
    )
    retrieve_mod.index_record(
        mock_burnless_project,
        delegation_id="d002",
        kind="evidence",
        content="file2 evidence",
        files=["test.py"],
    )

    result = await handle_retrieve(
        file="main.py",
        project_root=str(mock_burnless_project.parent),
    )
    assert result.get("error") is None
    assert result["count"] == 1
    assert "main.py" in result["results"][0]["files"]


@pytest.mark.asyncio
async def test_handle_retrieve_logs_event(mock_burnless_project: Path) -> None:
    retrieve_mod.index_record(
        mock_burnless_project,
        delegation_id="d001",
        kind="input",
        content="test content",
        entities=["testcontent"],
    )

    result = await handle_retrieve(
        query="testcontent",
        project_root=str(mock_burnless_project.parent),
    )
    assert result.get("error") is None

    events_file = mock_burnless_project / "events.jsonl"
    assert events_file.exists()
    with open(events_file) as f:
        events = [json.loads(line) for line in f if line.strip()]
    retrieve_events = [e for e in events if e["event_type"] == "retrieve_called"]
    assert len(retrieve_events) > 0
    assert retrieve_events[-1]["data"]["count"] == 1


@pytest.mark.asyncio
async def test_handle_search_capsules_no_burnless_root() -> None:
    result = await handle_search_capsules(query="test", project_root="/nonexistent")
    assert result.get("error") == "no_burnless_root"


@pytest.mark.asyncio
async def test_handle_search_capsules_returns_only_capsules(mock_burnless_project: Path) -> None:
    retrieve_mod.index_record(
        mock_burnless_project,
        delegation_id="d001",
        kind="input",
        content="input content",
    )
    retrieve_mod.index_record(
        mock_burnless_project,
        delegation_id="d002",
        kind="capsule",
        content="capsule content",
        entities=["search_test"],
    )
    retrieve_mod.index_record(
        mock_burnless_project,
        delegation_id="d003",
        kind="evidence",
        content="evidence content",
    )

    result = await handle_search_capsules(
        query="search_test",
        project_root=str(mock_burnless_project.parent),
    )
    assert result.get("error") is None
    assert result["count"] == 1
    assert all(r["kind"] == "capsule" for r in result["results"])


@pytest.mark.asyncio
async def test_handle_search_capsules_respects_limit(mock_burnless_project: Path) -> None:
    for i in range(15):
        retrieve_mod.index_record(
            mock_burnless_project,
            delegation_id=f"d{i:03d}",
            kind="capsule",
            content=f"capsule {i}",
        )

    result = await handle_search_capsules(
        query="capsule",
        limit=5,
        project_root=str(mock_burnless_project.parent),
    )
    assert result.get("error") is None
    assert result["count"] <= 5


@pytest.mark.asyncio
async def test_handle_search_capsules_logs_event(mock_burnless_project: Path) -> None:
    retrieve_mod.index_record(
        mock_burnless_project,
        delegation_id="d001",
        kind="capsule",
        content="capsule data",
    )

    result = await handle_search_capsules(
        query="capsule",
        project_root=str(mock_burnless_project.parent),
    )
    assert result.get("error") is None

    events_file = mock_burnless_project / "events.jsonl"
    assert events_file.exists()
    with open(events_file) as f:
        events = [json.loads(line) for line in f if line.strip()]
    retrieve_events = [e for e in events if e["event_type"] == "retrieve_called"]
    assert len(retrieve_events) > 0
    assert "search_capsules" in retrieve_events[-1]["data"]


@pytest.mark.asyncio
async def test_handle_explain_capsule_no_burnless_root() -> None:
    result = await handle_explain_capsule(id="d001", project_root="/nonexistent")
    assert result.get("error") == "no_burnless_root"


@pytest.mark.asyncio
async def test_handle_explain_capsule_no_evidence(mock_burnless_project: Path) -> None:
    result = await handle_explain_capsule(
        id="d999",
        project_root=str(mock_burnless_project.parent),
    )
    assert result.get("error") is None
    assert result["id"] == "d999"
    assert result["evidence"] == []
    assert result["capsule"] is None


@pytest.mark.asyncio
async def test_handle_explain_capsule_with_evidence(mock_burnless_project: Path) -> None:
    retrieve_mod.index_record(
        mock_burnless_project,
        delegation_id="d001",
        kind="input",
        raw_ref="/tmp/spec.md",
        content="spec input",
        entities=["spec"],
        files=["spec.md"],
    )
    retrieve_mod.index_record(
        mock_burnless_project,
        delegation_id="d001",
        kind="output",
        content="output result",
        entities=["result"],
        files=["output.txt"],
        status="OK",
    )

    result = await handle_explain_capsule(
        id="d001",
        project_root=str(mock_burnless_project.parent),
    )
    assert result.get("error") is None
    assert result["id"] == "d001"
    assert len(result["evidence"]) == 2
    assert all("snippet" not in e for e in result["evidence"])
    assert all("ref_id" in e and "kind" in e for e in result["evidence"])


@pytest.mark.asyncio
async def test_handle_explain_capsule_with_capsule_file(mock_burnless_project: Path) -> None:
    capsule_data = {
        "status": "OK",
        "files": ["src/main.py"],
        "summary": "Implementation complete",
    }
    capsule_path = mock_burnless_project / "capsules" / "d001.json"
    capsule_path.write_text(json.dumps(capsule_data), encoding="utf-8")

    result = await handle_explain_capsule(
        id="d001",
        project_root=str(mock_burnless_project.parent),
    )
    assert result.get("error") is None
    assert result["capsule"] == capsule_data


@pytest.mark.asyncio
async def test_handle_explain_capsule_metadata_only(mock_burnless_project: Path) -> None:
    retrieve_mod.index_record(
        mock_burnless_project,
        delegation_id="d001",
        kind="evidence",
        raw_ref="/path/to/file",
        capsule_ref="cap_ref_123",
        content="large content",
        status="indexed",
    )

    result = await handle_explain_capsule(
        id="d001",
        project_root=str(mock_burnless_project.parent),
    )
    assert result.get("error") is None
    evidence_rec = result["evidence"][0]
    assert evidence_rec["ref_id"] is not None
    assert evidence_rec["kind"] == "evidence"
    assert evidence_rec["raw_ref"] == "/path/to/file"
    assert evidence_rec["capsule_ref"] == "cap_ref_123"
    assert evidence_rec["status"] == "indexed"
    assert "snippet" not in evidence_rec
