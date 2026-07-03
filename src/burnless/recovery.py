from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable

from . import owner_loop

SESSION_ROOT_NAME = "sessions"
ROLLING_ROOT_NAME = "_rolling"
HANDOFF_DIR_NAME = "handoffs"
JOURNAL_DIR_NAME = "journal"
CHECKPOINT_NAME = "checkpoint.json"
LIVING_MIRROR_NAME = "living.md"
STATE_MIRROR_NAME = "state.json"
RING_DIR_NAME = "ring"

_RESTORE_PREFIX = "[BURNLESS RESTORE]"
_IGNORED_RESTORE_MARKERS = (
    _RESTORE_PREFIX,
    "## Trocas ainda não consolidadas",
    "[BURNLESS SEED]",
)

# RM-4C.4: when the host cannot provide a stable process_instance_id, an
# unclaimed handoff for the same project may still be claimed if fresh enough.
HANDOFF_CLAIM_TTL_SECONDS = 120


@contextlib.contextmanager
def _exclusive_lock(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl

        with open(lock_path, "a", encoding="utf-8") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)
    except ImportError:
        acquired = False
        for _ in range(150):
            try:
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                acquired = True
                break
            except FileExistsError:
                time.sleep(0.02)
        if not acquired:
            raise RuntimeError(f"Could not acquire lock: {lock_path}")
        try:
            yield
        finally:
            lock_path.unlink(missing_ok=True)


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except Exception:
                pass


def _atomic_text_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        with tmp.open("w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except Exception:
                pass


def _stable_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _safe_part(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return value or "unknown"


def _root_path(root) -> Path:
    path = Path(root) if isinstance(root, str) else root
    if path.name != ".burnless" and (path / ".burnless").exists():
        return path / ".burnless"
    return path


def _canonical_session_root(root: Path, host: str, host_session_id: str) -> Path:
    return root / "epochs" / SESSION_ROOT_NAME / _safe_part(host) / _safe_part(host_session_id)


def _legacy_session_root(root: Path, host_session_id: str) -> Path:
    return root / "epochs" / _safe_part(host_session_id)


def _session_roots(root: Path, host: str, host_session_id: str) -> list[Path]:
    roots = [_canonical_session_root(root, host, host_session_id)]
    if host == "claude":
        roots.append(_legacy_session_root(root, host_session_id))
    return roots


def _rolling_root(root: Path) -> Path:
    return root / "epochs" / ROLLING_ROOT_NAME


def _journal_dir(root: Path, host: str, host_session_id: str) -> Path:
    return _canonical_session_root(root, host, host_session_id) / JOURNAL_DIR_NAME


def _journal_lock(root: Path, host: str, host_session_id: str) -> Path:
    return _canonical_session_root(root, host, host_session_id) / "journal.lock"


def _checkpoint_paths(root: Path, host: str, host_session_id: str) -> Iterable[Path]:
    for session_root in _session_roots(root, host, host_session_id):
        yield session_root / CHECKPOINT_NAME


def _mirror_paths(root: Path, host: str, host_session_id: str) -> Iterable[Path]:
    for session_root in _session_roots(root, host, host_session_id):
        yield session_root / LIVING_MIRROR_NAME
        yield session_root / STATE_MIRROR_NAME


def _iter_json_files(path: Path) -> list[Path]:
    if not path.exists():
        return []
    files = [p for p in path.glob("*.json") if p.is_file()]
    files.sort(key=lambda p: (int(p.stem.split("-", 1)[0]), p.name) if p.stem.split("-", 1)[0].isdigit() else (10**9, p.name))
    return files


def _record_identity(record: dict[str, Any], transcript_path: Path | None, line_no: int, fallback_role: str) -> str:
    candidates = [
        record.get("uuid"),
        record.get("id"),
        record.get("message", {}).get("id") if isinstance(record.get("message"), dict) else None,
        record.get("message", {}).get("uuid") if isinstance(record.get("message"), dict) else None,
    ]
    for cand in candidates:
        if cand:
            return str(cand)
    if transcript_path is not None:
        return f"{transcript_path}:{line_no}:{fallback_role}"
    return f"line:{line_no}:{fallback_role}"


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
                continue
            if block.get("type") == "tool_use":
                continue
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
        return "\n".join(parts)
    return ""


def _is_restore_noise(text: str) -> bool:
    blob = text or ""
    return any(marker in blob for marker in _IGNORED_RESTORE_MARKERS)


def _extract_files_from_content(content: Any) -> set[str]:
    files: set[str] = set()

    def _add_path(value: Any) -> None:
        if isinstance(value, str) and value:
            if value.startswith("/") or "/" in value:
                files.add(value)

    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            for key in ("file_path", "path", "filename", "file", "target", "src"):
                _add_path(block.get(key))
            input_obj = block.get("input")
            if isinstance(input_obj, dict):
                for key in ("file_path", "path", "filename", "file", "target", "src"):
                    _add_path(input_obj.get(key))
                for key in ("file_paths", "paths", "files"):
                    values = input_obj.get(key)
                    if isinstance(values, list):
                        for value in values:
                            _add_path(value)
    return files


def extract_exchange(
    transcript_path,
    *,
    host: str,
    host_session_id: str,
    process_instance_id: str,
    cwd: str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    path = Path(transcript_path)
    entries: list[dict[str, Any]] = []
    if path.exists():
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            for line_no, line in enumerate(f):
                text = line.strip()
                if not text:
                    continue
                try:
                    obj = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                obj["_line_no"] = line_no
                entries.append(obj)

    selected_user: dict[str, Any] | None = None
    selected_assistant: dict[str, Any] | None = None
    pending_user: dict[str, Any] | None = None
    files: set[str] = set()

    for obj in entries:
        message = obj.get("message") if isinstance(obj.get("message"), dict) else {}
        role = str(message.get("role") or obj.get("role") or obj.get("type") or "").strip().lower()
        content = message.get("content")
        text = _content_text(content)

        if _is_restore_noise(text):
            continue
        if obj.get("isSidechain") or message.get("isSidechain"):
            continue

        files |= _extract_files_from_content(content)

        if role == "user" and text.strip():
            pending_user = obj
            continue
        if role == "assistant" and text.strip():
            selected_user = pending_user
            selected_assistant = obj

    if selected_user is None or selected_assistant is None:
        selected_user = selected_user or (entries[-2] if len(entries) >= 2 else None)
        selected_assistant = selected_assistant or (entries[-1] if entries else None)

    user_msg = selected_user.get("message", {}) if selected_user else {}
    assistant_msg = selected_assistant.get("message", {}) if selected_assistant else {}
    user_text = _content_text(user_msg.get("content")) if isinstance(user_msg, dict) else ""
    assistant_text = _content_text(assistant_msg.get("content")) if isinstance(assistant_msg, dict) else ""

    if _is_restore_noise(user_text):
        user_text = ""
    if _is_restore_noise(assistant_text):
        assistant_text = ""

    user_identity = _record_identity(selected_user or {}, path, int((selected_user or {}).get("_line_no", 0)), "user")
    assistant_identity = _record_identity(
        selected_assistant or {},
        path,
        int((selected_assistant or {}).get("_line_no", 0)),
        "assistant",
    )

    exchange_fingerprint = {
        "host": host,
        "host_session_id": host_session_id,
        "process_instance_id": process_instance_id,
        "user_record": user_identity,
        "assistant_record": assistant_identity,
    }
    exchange_id = "sha256:" + hashlib.sha256(_stable_json(exchange_fingerprint).encode("utf-8")).hexdigest()

    return {
        "schema": 1,
        "host": host,
        "host_session_id": host_session_id,
        "process_instance_id": process_instance_id,
        "cwd": cwd,
        "source": source,
        "exchange_id": exchange_id,
        "transcript_path": str(path),
        "user_record": user_identity,
        "assistant_record": assistant_identity,
        "user_text": user_text,
        "assistant_text": assistant_text,
        "files": sorted(files),
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _load_checkpoint_at(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if isinstance(data, dict):
        return data
    return None


def read_checkpoint(root, host: str, host_session_id: str) -> dict[str, Any] | None:
    root_path = _root_path(root)
    for cp in _checkpoint_paths(root_path, host, host_session_id):
        data = _load_checkpoint_at(cp)
        if data is not None:
            return data
    return None


def _journal_head(journal_dir: Path) -> int:
    seqs = []
    for path in _iter_json_files(journal_dir):
        stem = path.stem
        seq_part = stem.split("-", 1)[0]
        if seq_part.isdigit():
            seqs.append(int(seq_part))
    return max(seqs) if seqs else 0


def _read_journal(journal_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in _iter_json_files(journal_dir):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(record, dict):
            record["_path"] = str(path)
            records.append(record)
    records.sort(key=lambda r: int(r.get("seq") or 0))
    return records


def _find_journal_record(journal_dir: Path, exchange_id: str) -> dict[str, Any] | None:
    for path in _iter_json_files(journal_dir):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(record, dict) and record.get("exchange_id") == exchange_id:
            record["_path"] = str(path)
            return record
    return None


def journal_append(root, envelope: dict[str, Any]) -> dict[str, Any]:
    root_path = _root_path(root)
    host = str(envelope.get("host") or "claude")
    host_session_id = str(envelope.get("host_session_id") or envelope.get("session_id") or "")
    process_instance_id = str(envelope.get("process_instance_id") or host_session_id or "")
    if not host_session_id:
        raise ValueError("host_session_id is required")

    journal_dir = _journal_dir(root_path, host, host_session_id)
    journal_dir.mkdir(parents=True, exist_ok=True)
    lock_path = _journal_lock(root_path, host, host_session_id)

    with _exclusive_lock(lock_path):
        existing = _find_journal_record(journal_dir, str(envelope.get("exchange_id") or ""))
        if existing is not None:
            return existing

        journal_head = _journal_head(journal_dir)
        seq = journal_head + 1
        exchange_id = str(envelope.get("exchange_id") or "")
        if not exchange_id:
            raise ValueError("exchange_id is required")

        record = dict(envelope)
        record.update(
            {
                "schema": int(record.get("schema") or 1),
                "seq": seq,
                "host": host,
                "host_session_id": host_session_id,
                "process_instance_id": process_instance_id,
                "journal_head": seq,
                "captured_at": record.get("captured_at") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        )

        file_path = journal_dir / f"{seq:06d}-{_safe_part(exchange_id)}.json"
        _atomic_json_write(file_path, record)
        owner_loop.log_owner_event(
            root_path,
            {
                "phase": "recovery",
                "event": "journal_appended",
                "host": host,
                "host_session_id": host_session_id,
                "process_instance_id": process_instance_id,
                "seq": seq,
                "exchange_id": exchange_id,
            },
        )
        return record


def _checkpoint_payload(
    *,
    host: str,
    host_session_id: str,
    process_instance_id: str,
    living_md: str,
    harvested_state: dict[str, Any],
    applied_through: int,
    journal_head: int,
    generation: int,
) -> dict[str, Any]:
    content_hash = "sha256:" + hashlib.sha256(living_md.encode("utf-8")).hexdigest()
    return {
        "schema": 1,
        "generation": generation,
        "host": host,
        "host_session_id": host_session_id,
        "process_instance_id": process_instance_id,
        "living_md": living_md,
        "harvested_state": harvested_state,
        "applied_through": applied_through,
        "journal_head": journal_head,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "content_hash": content_hash,
    }


def write_checkpoint(
    root,
    *,
    host: str,
    host_session_id: str,
    process_instance_id: str,
    living_md: str,
    harvested_state: dict[str, Any],
    applied_through: int,
    journal_head: int | None = None,
) -> dict[str, Any]:
    root_path = _root_path(root)
    canonical_root = _canonical_session_root(root_path, host, host_session_id)
    canonical_root.mkdir(parents=True, exist_ok=True)
    if journal_head is None:
        journal_head = applied_through

    current = read_checkpoint(root_path, host, host_session_id)
    generation = int((current or {}).get("generation") or 0) + 1
    payload = _checkpoint_payload(
        host=host,
        host_session_id=host_session_id,
        process_instance_id=process_instance_id,
        living_md=living_md,
        harvested_state=harvested_state,
        applied_through=applied_through,
        journal_head=journal_head,
        generation=generation,
    )
    for checkpoint_path in _checkpoint_paths(root_path, host, host_session_id):
        _atomic_json_write(checkpoint_path, payload)
    for mirror_path in _mirror_paths(root_path, host, host_session_id):
        if mirror_path.name == LIVING_MIRROR_NAME:
            _atomic_text_write(mirror_path, living_md)
        else:
            _atomic_json_write(mirror_path, harvested_state)
    legacy_root = _legacy_session_root(root_path, host_session_id) if host == "claude" else None
    if legacy_root is not None:
        legacy_root.mkdir(parents=True, exist_ok=True)
        _atomic_json_write(legacy_root / CHECKPOINT_NAME, payload)
    return payload


def _build_compact_prompt(checkpoint: dict[str, Any], pending: list[dict[str, Any]]) -> str:
    parts = [checkpoint.get("living_md") or ""]
    parts.append("## Trocas pendentes")
    for record in pending:
        parts.append(f"### seq {record.get('seq')}")
        parts.append(f"exchange_id: {record.get('exchange_id')}")
        parts.append("PERGUNTA:")
        parts.append(record.get("user_text") or "")
        parts.append("")
        parts.append("RESPOSTA:")
        parts.append(record.get("assistant_text") or "")
        if record.get("files"):
            parts.append(f"files: {', '.join(record.get('files') or [])}")
    return "\n".join(parts).strip() + "\n"


def compact_pending(
    root,
    *,
    host: str,
    host_session_id: str,
    process_instance_id: str,
    rewriter,
    budget_tokens: int = 2500,
) -> dict[str, Any]:
    root_path = _root_path(root)
    journal_dir = _journal_dir(root_path, host, host_session_id)
    checkpoint = read_checkpoint(root_path, host, host_session_id) or {
        "generation": 0,
        "living_md": "",
        "harvested_state": {"contracts": [], "refs": [], "open_threads": []},
        "applied_through": 0,
        "journal_head": 0,
    }
    records = _read_journal(journal_dir)
    journal_head = _journal_head(journal_dir)
    pending = [r for r in records if int(r.get("seq") or 0) > int(checkpoint.get("applied_through") or 0)]

    owner_loop.log_owner_event(
        root_path,
        {
            "phase": "recovery",
            "event": "compaction_started",
            "host": host,
            "host_session_id": host_session_id,
            "process_instance_id": process_instance_id,
            "journal_head": journal_head,
            "applied_through": int(checkpoint.get("applied_through") or 0),
            "pending": len(pending),
        },
    )

    if not pending:
        return {"status": "noop", "journal_head": journal_head, "applied_through": int(checkpoint.get("applied_through") or 0)}

    prompt = _build_compact_prompt(checkpoint, pending)
    try:
        candidate = rewriter(prompt)
    except Exception as exc:
        owner_loop.log_owner_event(
            root_path,
            {
                "phase": "recovery",
                "event": "compaction_failed",
                "host": host,
                "host_session_id": host_session_id,
                "process_instance_id": process_instance_id,
                "error": str(exc),
            },
        )
        return {"status": "failed", "error": str(exc), "journal_head": journal_head, "applied_through": int(checkpoint.get("applied_through") or 0)}

    if not isinstance(candidate, str) or not candidate.strip():
        owner_loop.log_owner_event(
            root_path,
            {
                "phase": "recovery",
                "event": "compaction_failed",
                "host": host,
                "host_session_id": host_session_id,
                "process_instance_id": process_instance_id,
                "error": "empty output",
            },
        )
        return {"status": "failed", "error": "empty output", "journal_head": journal_head, "applied_through": int(checkpoint.get("applied_through") or 0)}

    harvested_state = {
        "contracts": [],
        "refs": [],
        "open_threads": [],
    }
    try:
        from .epochs_v2 import harvest_state

        harvested_state = harvest_state(candidate)
    except Exception:
        pass

    committed = write_checkpoint(
        root_path,
        host=host,
        host_session_id=host_session_id,
        process_instance_id=process_instance_id,
        living_md=candidate.strip(),
        harvested_state=harvested_state,
        applied_through=max(int(r.get("seq") or 0) for r in pending),
        journal_head=journal_head,
    )
    owner_loop.log_owner_event(
        root_path,
        {
            "phase": "recovery",
            "event": "checkpoint_committed",
            "host": host,
            "host_session_id": host_session_id,
            "process_instance_id": process_instance_id,
            "applied_through": committed["applied_through"],
            "journal_head": committed["journal_head"],
        },
    )
    return {"status": "committed", "checkpoint": committed, "journal_head": journal_head, "applied_through": committed["applied_through"]}


def write_handoff(
    root,
    *,
    host: str,
    host_session_id: str,
    process_instance_id: str,
    claimed_by: str | None = None,
) -> dict[str, Any]:
    root_path = _root_path(root)
    journal_dir = _journal_dir(root_path, host, host_session_id)
    journal_head = _journal_head(journal_dir)
    handoff_dir = _rolling_root(root_path) / HANDOFF_DIR_NAME
    handoff_dir.mkdir(parents=True, exist_ok=True)
    path = handoff_dir / f"{_safe_part(host_session_id)}.json"
    payload = {
        "schema": 1,
        "host": host,
        "host_session_id": host_session_id,
        "old_sid": host_session_id,
        "process_instance_id": process_instance_id,
        "root": str(root_path),
        "journal_head": journal_head,
        "claimed_by": claimed_by,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "claimed_at": None,
    }
    if path.exists():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            current = {}
        if isinstance(current, dict):
            payload.update({k: v for k, v in current.items() if k in payload or k in {"claimed_at", "claimed_by"}})
            payload["journal_head"] = journal_head or int(current.get("journal_head") or 0)
            payload["claimed_by"] = current.get("claimed_by") if current.get("claimed_by") is not None else claimed_by
            payload["old_sid"] = payload.get("old_sid") or payload.get("host_session_id")
    _atomic_json_write(path, payload)
    owner_loop.log_owner_event(
        root_path,
        {
            "phase": "recovery",
            "event": "handoff_written",
            "host": host,
            "host_session_id": host_session_id,
            "process_instance_id": process_instance_id,
            "journal_head": journal_head,
        },
    )
    return payload


def claim_handoff(
    root,
    *,
    host: str,
    process_instance_id: str,
    new_session_id: str,
    ttl_seconds: int = HANDOFF_CLAIM_TTL_SECONDS,
) -> dict[str, Any] | None:
    root_path = _root_path(root)
    handoff_dir = _rolling_root(root_path) / HANDOFF_DIR_NAME
    handoff_dir.mkdir(parents=True, exist_ok=True)
    lock_path = handoff_dir / "handoff.lock"
    now = time.time()
    with _exclusive_lock(lock_path):
        pid_matches: list[tuple[float, Path, dict[str, Any]]] = []
        fresh_unclaimed: list[tuple[float, Path, dict[str, Any]]] = []
        for path in handoff_dir.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            if payload.get("host") != host:
                continue
            if payload.get("claimed_by"):
                continue
            mtime = path.stat().st_mtime
            if str(payload.get("process_instance_id") or "") == str(process_instance_id):
                pid_matches.append((mtime, path, payload))
            elif ttl_seconds > 0 and (now - mtime) <= ttl_seconds:
                # RM-4C.4 fallback: host did not carry a stable
                # process_instance_id across SessionEnd -> SessionStart
                # (e.g. real Claude Code hook payloads). A fresh unclaimed
                # handoff for the same project is the weaker-but-real lineage.
                fresh_unclaimed.append((mtime, path, payload))
        claim_mode = "pid"
        candidates = pid_matches
        if not candidates:
            claim_mode = "ttl_fallback"
            candidates = fresh_unclaimed
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        _, path, payload = candidates[0]
        payload = dict(payload)
        payload["claimed_by"] = new_session_id
        payload["claimed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        payload["old_sid"] = payload.get("old_sid") or payload.get("host_session_id")
        payload["claim_mode"] = claim_mode
        _atomic_json_write(path, payload)
        owner_loop.log_owner_event(
            root_path,
            {
                "phase": "recovery",
                "event": "handoff_claimed",
                "host": host,
                "process_instance_id": process_instance_id,
                "new_session_id": new_session_id,
                "old_sid": payload.get("host_session_id"),
                "claim_mode": claim_mode,
            },
        )
        return payload


def _truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    head = max_chars // 2
    tail = max_chars - head - 64
    if tail < 0:
        tail = 0
    return text[:head] + "\n...\n[truncated]\n...\n" + text[-tail:]


def render_restore(
    root,
    *,
    host: str,
    host_session_id: str,
    process_instance_id: str,
    new_session_id: str,
    source: str,
    budget_tokens: int = 2000,
) -> dict[str, Any] | None:
    root_path = _root_path(root)
    checkpoint = read_checkpoint(root_path, host, host_session_id) or {
        "generation": 0,
        "living_md": "",
        "harvested_state": {"contracts": [], "refs": [], "open_threads": []},
        "applied_through": 0,
        "journal_head": 0,
    }
    journal_dir = _journal_dir(root_path, host, host_session_id)
    records = _read_journal(journal_dir)
    journal_head = _journal_head(journal_dir)
    pending = [r for r in records if int(r.get("seq") or 0) > int(checkpoint.get("applied_through") or 0)]

    living_md = checkpoint.get("living_md") or ""
    if not living_md.strip() and not pending and int(checkpoint.get("applied_through") or 0) <= 0 and journal_head <= 0:
        return None

    body_parts = [
        _RESTORE_PREFIX,
        f"host={host}",
        f"old_sid={host_session_id}",
        f"new_sid={new_session_id}",
        f"process_instance_id={process_instance_id}",
        f"checkpoint_generation={checkpoint.get('generation', 0)}",
        f"applied_through={checkpoint.get('applied_through', 0)}",
        f"journal_head={journal_head}",
        f"pending_count={len(pending)}",
    ]
    if living_md.strip():
        body_parts.append("")
        body_parts.append(living_md.rstrip())

    if pending:
        body_parts.append("")
        body_parts.append("## Trocas ainda não consolidadas")
        for record in pending:
            block = [
                f"### seq {record.get('seq')}",
                f"exchange_id: {record.get('exchange_id')}",
                "PERGUNTA:",
                record.get("user_text") or "",
                "",
                "RESPOSTA:",
                record.get("assistant_text") or "",
            ]
            files = record.get("files") or []
            if files:
                block.append(f"files: {', '.join(files)}")
            body_parts.extend(block)

    checkpoint_chars = len(living_md.strip())
    context = "\n".join(body_parts).strip()
    max_chars = max(800, int(budget_tokens) * 4)
    context = _truncate_text(context, max_chars)

    owner_loop.log_owner_event(
        root_path,
        {
            "phase": "recovery",
            "event": "restore_served",
            "host": host,
            "host_session_id": host_session_id,
            "process_instance_id": process_instance_id,
            "new_session_id": new_session_id,
            "journal_head": journal_head,
            "applied_through": int(checkpoint.get("applied_through") or 0),
            "pending": len(pending),
        },
    )
    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        },
        "recovery": {
            "host": host,
            "old_session": host_session_id,
            "new_session": new_session_id,
            "process_instance_id": process_instance_id,
            "source": source,
            "checkpoint_chars": checkpoint_chars,
            "pending_count": len(pending),
            "journal_head": journal_head,
            "applied_through": int(checkpoint.get("applied_through") or 0),
        },
    }
