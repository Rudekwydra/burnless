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
    with open(index_path, "a", encoding="utf-8") as f:
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
    with open(index_path, "r", encoding="utf-8") as f:
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


def _looks_like_spine_ref(value: str | None) -> str | None:
    """A spine ref is the 12-hex hash a capsule ends with, accepted bare or
    with a ref:/spine: prefix. Returns the bare hash, or None."""
    if not value:
        return None
    v = value.strip().lower()
    for prefix in ("spine:", "ref:"):
        if v.startswith(prefix):
            v = v[len(prefix):]
    if 8 <= len(v) <= 64 and all(c in "0123456789abcdef" for c in v):
        return v
    return None


def _spine_records(
    root: pathlib.Path | str,
    *,
    query: str | None = None,
    file: str | None = None,
    entity: str | None = None,
    ref: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Frozen spine capsules (proxy rolling memory) as retrieve records.

    Live conversation history answers the same question delegation evidence
    does — one search covers both. Reads .burnless/proxy/ directly; no proxy
    dir means no rolling memory, and that is an empty result, not an error.
    """
    proxy_dir = Path(root) / "proxy"
    capsules_dir = proxy_dir / "capsules"
    if not capsules_dir.is_dir():
        return []
    from .proxy.store import resolve_ref

    def as_record(h: str, capsule: str, path: Path | None) -> dict:
        exchange_path = proxy_dir / "exchanges" / f"{h}.json"
        try:
            created = datetime.datetime.fromtimestamp(
                path.stat().st_mtime, timezone.utc
            ).isoformat() if path else None
        except OSError:
            created = None
        return {
            "schema_version": 1,
            "ref_id": f"spine:{h}",
            "kind": "spine",
            "status": "frozen",
            "raw_ref": str(exchange_path) if exchange_path.exists() else None,
            "capsule_ref": str(capsules_dir / f"{h}.txt"),
            "entities": [],
            "files": [],
            "created_at": created,
            "token_estimate": len(capsule) // 4,
        }

    try:
        if ref:
            rec = resolve_ref(proxy_dir, ref)
            if rec is None or rec["capsule"] is None:
                return []
            return [as_record(rec["ref"], rec["capsule"], capsules_dir / f"{rec['ref']}.txt")]
        needles = [n.lower() for n in (query, file, entity) if n]
        out: list[dict] = []
        for path in sorted(capsules_dir.glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            haystack = text.lower()
            if needles and not all(n in haystack for n in needles):
                continue
            out.append(as_record(path.stem, text, path))
            if len(out) >= limit:
                break
        return out
    except Exception:
        return []  # fail-open: spine search must never break delegation retrieve


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

    results = list(reversed(records))

    # Rolling memory answers the same question — merge spine capsules in.
    # A ref-shaped id/query resolves directly; otherwise text-match capsules.
    spine_ref = _looks_like_spine_ref(delegation_id) or _looks_like_spine_ref(query)
    if spine_ref:
        results += _spine_records(root, ref=spine_ref)
    elif delegation_id is None and (query or file or entity):
        results += _spine_records(root, query=query, file=file, entity=entity)

    return results


def snippet(
    root: pathlib.Path | str,
    ref_id: str,
    *,
    max_chars: int = 4000,
    full: bool = False,
) -> str:
    if ref_id.startswith("spine:"):
        from .proxy.store import resolve_ref

        rec = resolve_ref(Path(root) / "proxy", ref_id[len("spine:"):])
        if rec is None:
            return ""
        text = rec["capsule"] or ""
        if full and rec["exchange"] is not None:
            text += "\n\n" + json.dumps(rec["exchange"], indent=2, ensure_ascii=False)
        return text if full else text[:max_chars]

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
