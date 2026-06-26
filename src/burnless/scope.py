from __future__ import annotations

import hashlib
from pathlib import Path


def stable_project_hash(project_root) -> str:
    """Hash of resolved project path. Deterministic and always starts with 'sha256:'."""
    resolved = str(Path(project_root).resolve())
    digest = hashlib.sha256(resolved.encode()).hexdigest()
    return f"sha256:{digest}"


def project_scope(project_root, *, session_id=None, chat_id=None, source="cli") -> dict:
    """Provenance dict for a project scope."""
    return {
        "project_root": str(Path(project_root).resolve()),
        "project_root_hash": stable_project_hash(project_root),
        "session_id": session_id,
        "chat_id": chat_id,
        "source": source,
    }


def assert_same_project(record, project_root) -> bool:
    """Check if record belongs to same project (backward-compat: no hash -> True)."""
    record_hash = None

    if isinstance(record, dict):
        if "scope" in record and isinstance(record["scope"], dict):
            record_hash = record["scope"].get("project_root_hash")

        if record_hash is None:
            record_hash = record.get("project_root_hash")

    if record_hash is None:
        return True

    return record_hash == stable_project_hash(project_root)
