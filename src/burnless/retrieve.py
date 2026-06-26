from __future__ import annotations

import datetime
import hashlib
import json
from datetime import timezone
from pathlib import Path
import pathlib

from . import epochs_v2 as epochs_mod
from . import scope as scope_mod


def _retrieve_dir(root: pathlib.Path | str) -> Path:
    return Path(root) / "retrieve"


def _index_path(root: pathlib.Path | str) -> Path:
    return _retrieve_dir(root) / "index.jsonl"


def _snippets_dir(root: pathlib.Path | str) -> Path:
    return _retrieve_dir(root) / "snippets"


def index_record(
    root: pathlib.Path | str,
    *,
    delegation_id: str,
    kind: str,
    raw_ref: str | None = None,
    capsule_ref: str | None = None,
    entities: list[str] | set[str] | None = None,
    files: list[str] | set[str] | None = None,
    status: str | None = None,
    session_id: str | None = None,
    content: str | None = None,
    token_estimate: int | None = None,
) -> dict:
    root = Path(root)
    project_root = str(root.resolve().parent)
    project_root_hash = scope_mod.stable_project_hash(project_root)

    if entities is None:
        entities = []
    else:
        entities = sorted(list(entities))

    if files is None:
        files = []
    else:
        files = sorted(list(files))

    if content is not None:
        content_hash = "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
    elif raw_ref is not None:
        raw_path = Path(raw_ref)
        if raw_path.exists():
            content_hash = "sha256:" + hashlib.sha256(raw_path.read_bytes()).hexdigest()
        else:
            content_hash = "sha256:" + hashlib.sha256(b"").hexdigest()
    else:
        content_hash = "sha256:" + hashlib.sha256(b"").hexdigest()

    hash_suffix = content_hash.split(":", 1)[1][:16]
    ref_id = f"{delegation_id}:{kind}:{hash_suffix}"

    if token_estimate is None:
        if content is not None:
            token_estimate = len(content) // 4
        else:
            token_estimate = 0

    created_at = datetime.datetime.now(timezone.utc).isoformat()

    record = {
        "schema_version": 1,
        "ref_id": ref_id,
        "capsule_id": delegation_id,
        "delegation_id": delegation_id,
        "raw_ref": raw_ref,
        "capsule_ref": capsule_ref,
        "kind": kind,
        "project_root": project_root,
        "project_root_hash": project_root_hash,
        "session_id": session_id,
        "entities": entities,
        "files": files,
        "status": status,
        "created_at": created_at,
        "token_estimate": token_estimate,
        "content_hash": content_hash,
    }

    index_path = _index_path(root)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with open(index_path, "a") as f:
        f.write(json.dumps(record) + "\n")

    if content is not None:
        snippets_path = _snippets_dir(root)
        snippets_path.mkdir(parents=True, exist_ok=True)

        snippet_filename = ref_id.replace(":", "_").replace("/", "_") + ".txt"
        snippet_file = snippets_path / snippet_filename

        snippet_content = content[:8000]
        snippet_file.write_text(snippet_content)

    return record


def read_index(root: pathlib.Path | str) -> list[dict]:
    index_path = _index_path(root)

    if not index_path.exists():
        return []

    records = []
    with open(index_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                records.append(record)
            except json.JSONDecodeError:
                continue

    return records


def search(
    root: pathlib.Path | str,
    *,
    query: str | None = None,
    file: str | None = None,
    entity: str | None = None,
    delegation_id: str | None = None,
    project_scoped: bool = True,
) -> list[dict]:
    root = Path(root)
    project_root = str(root.resolve().parent)

    records = read_index(root)

    if project_scoped:
        records = [r for r in records if scope_mod.assert_same_project(r, project_root)]

    if delegation_id is not None:
        records = [
            r for r in records
            if r.get("delegation_id") == delegation_id or r.get("capsule_id") == delegation_id
        ]

    if file is not None:
        records = [r for r in records if file in r.get("files", [])]

    if entity is not None:
        records = [r for r in records if entity in r.get("entities", [])]

    if query is not None:
        query_lower = query.lower()
        query_entities = epochs_mod.extract_entities(query)

        filtered = []
        for r in records:
            haystack_parts = [
                r.get("ref_id", ""),
                r.get("status") or "",
                r.get("kind", ""),
            ]
            haystack_parts.extend(r.get("entities", []))
            haystack_parts.extend(r.get("files", []))
            haystack = " ".join(haystack_parts).lower()

            if query_lower in haystack or any(e in r.get("entities", []) for e in query_entities):
                filtered.append(r)

        records = filtered

    return list(reversed(records))


def snippet(
    root: pathlib.Path | str,
    ref_id: str,
    *,
    max_chars: int = 4000,
    full: bool = False,
) -> str:
    snippets_path = _snippets_dir(root)

    snippet_filename = ref_id.replace(":", "_").replace("/", "_") + ".txt"
    snippet_file = snippets_path / snippet_filename

    if not snippet_file.exists():
        return ""

    content = snippet_file.read_text()

    if full:
        return content
    else:
        return content[:max_chars]
