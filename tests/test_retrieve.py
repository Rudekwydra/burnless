import json
import pytest
from pathlib import Path

import burnless.retrieve as retrieve_mod


def test_index_record_structure(tmp_path):
    """Test that index_record returns dict with exactly 16 keys in order."""
    root = tmp_path / ".burnless"
    root.mkdir()

    rec = retrieve_mod.index_record(
        root,
        delegation_id="d001",
        kind="worker_log",
        content="test content",
    )

    expected_keys = [
        "schema_version",
        "ref_id",
        "capsule_id",
        "delegation_id",
        "raw_ref",
        "capsule_ref",
        "kind",
        "project_root",
        "project_root_hash",
        "session_id",
        "entities",
        "files",
        "status",
        "created_at",
        "token_estimate",
        "content_hash",
    ]

    assert list(rec.keys()) == expected_keys
    assert rec["schema_version"] == 1
    assert rec["delegation_id"] == "d001"
    assert rec["kind"] == "worker_log"


def test_ref_id_format(tmp_path):
    """Test that ref_id has correct format."""
    root = tmp_path / ".burnless"
    root.mkdir()

    rec = retrieve_mod.index_record(
        root,
        delegation_id="d123",
        kind="capsule",
        content="test",
    )

    assert rec["ref_id"].startswith("d123:")
    assert ":capsule:" in rec["ref_id"]


def test_content_hash_and_token_estimate(tmp_path):
    """Test content_hash and token_estimate."""
    root = tmp_path / ".burnless"
    root.mkdir()

    content = "x" * 400
    rec = retrieve_mod.index_record(
        root,
        delegation_id="d001",
        kind="worker_log",
        content=content,
    )

    assert rec["content_hash"].startswith("sha256:")
    assert rec["token_estimate"] == 400 // 4


def test_read_index_empty(tmp_path):
    """Test read_index on missing root."""
    root = tmp_path / ".burnless"
    root.mkdir()

    records = retrieve_mod.read_index(root)
    assert records == []


def test_read_index_with_records(tmp_path):
    """Test read_index returns appended records."""
    root = tmp_path / ".burnless"
    root.mkdir()

    rec1 = retrieve_mod.index_record(
        root,
        delegation_id="d001",
        kind="worker_log",
        content="content1",
    )

    rec2 = retrieve_mod.index_record(
        root,
        delegation_id="d002",
        kind="capsule",
        content="content2",
    )

    records = retrieve_mod.read_index(root)
    assert len(records) == 2
    assert records[0]["delegation_id"] == "d001"
    assert records[1]["delegation_id"] == "d002"


def test_read_index_skip_malformed(tmp_path):
    """Test read_index skips malformed JSON lines."""
    root = tmp_path / ".burnless"
    root.mkdir()

    rec = retrieve_mod.index_record(
        root,
        delegation_id="d001",
        kind="worker_log",
        content="valid",
    )

    index_path = retrieve_mod._index_path(root)
    with open(index_path, "a") as f:
        f.write("not json\n")

    rec2 = retrieve_mod.index_record(
        root,
        delegation_id="d002",
        kind="capsule",
        content="also valid",
    )

    records = retrieve_mod.read_index(root)
    assert len(records) == 2
    assert records[0]["delegation_id"] == "d001"
    assert records[1]["delegation_id"] == "d002"


def test_search_by_delegation_id(tmp_path):
    """Test search by delegation_id."""
    root = tmp_path / ".burnless"
    root.mkdir()

    retrieve_mod.index_record(root, delegation_id="d123", kind="worker_log", content="a")
    retrieve_mod.index_record(root, delegation_id="d456", kind="capsule", content="b")

    results = retrieve_mod.search(root, delegation_id="d123")
    assert len(results) == 1
    assert results[0]["delegation_id"] == "d123"


def test_search_by_file(tmp_path):
    """Test search by file."""
    root = tmp_path / ".burnless"
    root.mkdir()

    retrieve_mod.index_record(
        root,
        delegation_id="d001",
        kind="worker_log",
        files=["/path/to/cli.py"],
        content="a",
    )
    retrieve_mod.index_record(
        root,
        delegation_id="d002",
        kind="capsule",
        files=["/path/to/other.py"],
        content="b",
    )

    results = retrieve_mod.search(root, file="/path/to/cli.py")
    assert len(results) == 1
    assert results[0]["delegation_id"] == "d001"


def test_search_by_entity(tmp_path):
    """Test search by entity."""
    root = tmp_path / ".burnless"
    root.mkdir()

    retrieve_mod.index_record(
        root,
        delegation_id="d001",
        kind="worker_log",
        entities=["foo"],
        content="a",
    )
    retrieve_mod.index_record(
        root,
        delegation_id="d002",
        kind="capsule",
        entities=["bar"],
        content="b",
    )

    results = retrieve_mod.search(root, entity="foo")
    assert len(results) == 1
    assert results[0]["delegation_id"] == "d001"


def test_search_by_query_substring(tmp_path):
    """Test search by query with substring match."""
    root = tmp_path / ".burnless"
    root.mkdir()

    retrieve_mod.index_record(
        root,
        delegation_id="d001",
        kind="worker_log",
        files=["/path/to/cli.py"],
        content="a",
    )
    retrieve_mod.index_record(
        root,
        delegation_id="d002",
        kind="capsule",
        files=["/path/to/other.py"],
        content="b",
    )

    results = retrieve_mod.search(root, query="cli")
    assert len(results) == 1
    assert results[0]["delegation_id"] == "d001"


def test_search_newest_first(tmp_path):
    """Test search returns newest first."""
    root = tmp_path / ".burnless"
    root.mkdir()

    retrieve_mod.index_record(root, delegation_id="d1", kind="worker_log", content="a")
    retrieve_mod.index_record(root, delegation_id="d2", kind="capsule", content="b")

    results = retrieve_mod.search(root)
    assert results[0]["delegation_id"] == "d2"
    assert results[1]["delegation_id"] == "d1"


def test_snippet_bounded(tmp_path):
    """Test snippet respects max_chars."""
    root = tmp_path / ".burnless"
    root.mkdir()

    content = "x" * 5000
    rec = retrieve_mod.index_record(
        root,
        delegation_id="d001",
        kind="worker_log",
        content=content,
    )

    snippet_text = retrieve_mod.snippet(root, rec["ref_id"], max_chars=100)
    assert len(snippet_text) == 100
    assert snippet_text == "x" * 100


def test_snippet_full(tmp_path):
    """Test snippet with full=True."""
    root = tmp_path / ".burnless"
    root.mkdir()

    content = "x" * 5000
    rec = retrieve_mod.index_record(
        root,
        delegation_id="d001",
        kind="worker_log",
        content=content,
    )

    snippet_text = retrieve_mod.snippet(root, rec["ref_id"], full=True)
    assert len(snippet_text) == 5000
    assert snippet_text == content


def test_snippet_unknown_ref(tmp_path):
    """Test snippet for unknown ref_id returns empty string."""
    root = tmp_path / ".burnless"
    root.mkdir()

    snippet_text = retrieve_mod.snippet(root, "unknown:ref:id")
    assert snippet_text == ""


def test_project_scoping(tmp_path):
    """Test project scoping excludes out-of-project records."""
    root = tmp_path / ".burnless"
    root.mkdir()

    rec1 = retrieve_mod.index_record(
        root,
        delegation_id="d001",
        kind="worker_log",
        content="valid",
    )

    index_path = retrieve_mod._index_path(root)
    bogus_rec = {
        "schema_version": 1,
        "ref_id": "d002:capsule:abc123",
        "capsule_id": "d002",
        "delegation_id": "d002",
        "raw_ref": None,
        "capsule_ref": None,
        "kind": "capsule",
        "project_root": "/some/other/project",
        "project_root_hash": "sha256:bogusashash",
        "session_id": None,
        "entities": [],
        "files": [],
        "status": None,
        "created_at": "2026-01-01T00:00:00+00:00",
        "token_estimate": 0,
        "content_hash": "sha256:abc123",
    }
    with open(index_path, "a") as f:
        f.write(json.dumps(bogus_rec) + "\n")

    results = retrieve_mod.search(root, project_scoped=True)
    assert len(results) == 1
    assert results[0]["delegation_id"] == "d001"

    results_all = retrieve_mod.search(root, project_scoped=False)
    assert len(results_all) == 2


def test_legacy_record_included(tmp_path):
    """Test legacy records with no hash are included."""
    root = tmp_path / ".burnless"
    root.mkdir()

    rec1 = retrieve_mod.index_record(
        root,
        delegation_id="d001",
        kind="worker_log",
        content="valid",
    )

    index_path = retrieve_mod._index_path(root)
    legacy_rec = {
        "schema_version": 1,
        "ref_id": "d002:capsule:abc123",
        "capsule_id": "d002",
        "delegation_id": "d002",
        "raw_ref": None,
        "capsule_ref": None,
        "kind": "capsule",
        "project_root": str(root.resolve().parent),
        "session_id": None,
        "entities": [],
        "files": [],
        "status": None,
        "created_at": "2026-01-01T00:00:00+00:00",
        "token_estimate": 0,
        "content_hash": "sha256:abc123",
    }
    with open(index_path, "a") as f:
        f.write(json.dumps(legacy_rec) + "\n")

    results = retrieve_mod.search(root, project_scoped=True)
    assert len(results) == 2
