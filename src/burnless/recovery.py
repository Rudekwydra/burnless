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
HOOK_ERROR_LOG_NAME = "hook_errors.log"
COMPACTION_LEASE_NAME = "compact.lease.json"

_RESTORE_PREFIX = "[BURNLESS RESTORE]"
_IGNORED_RESTORE_MARKERS = (
    _RESTORE_PREFIX,
    "## Trocas ainda não consolidadas",
    "[BURNLESS SEED]",
)

# RM-4C.4: when the host cannot provide a stable process_instance_id, an
# unclaimed handoff for the same project may still be claimed if fresh enough.
HANDOFF_CLAIM_TTL_SECONDS = 120
JOURNAL_RETENTION_RECORDS = 256


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


def _project_root(root: Path) -> Path:
    return root.parent if root.name == ".burnless" else root


def _state_dir(root: Path) -> Path:
    return Path.home() / ".burnless" / "state"


def _hook_error_log_path(root: Path | None = None) -> Path:
    # Hook errors are global because hook wiring is global; the path is stable
    # and does not depend on the project tree.
    return Path.home() / ".burnless" / "state" / HOOK_ERROR_LOG_NAME


def record_hook_error(
    root,
    *,
    hook: str,
    host: str,
    host_session_id: str | None = None,
    process_instance_id: str | None = None,
    error: str,
    source: str | None = None,
    transcript_path: str | None = None,
) -> dict[str, Any]:
    payload = {
        "schema": 1,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "hook": hook,
        "host": host,
        "host_session_id": host_session_id,
        "process_instance_id": process_instance_id,
        "source": source,
        "transcript_path": transcript_path,
        "error": error,
    }
    path = _hook_error_log_path(_root_path(root))
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.exists() and path.stat().st_size > 1048576:
            rotated = path.with_suffix(path.suffix + ".1")
            path.rename(rotated)
    except Exception:
        pass
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return payload


def _journal_retention_records(root: Path, host: str, host_session_id: str) -> int:
    try:
        from . import config as config_mod

        cfg = config_mod.load((_project_root(_root_path(root)) / ".burnless" / "config.yaml"))
        epochs_cfg = cfg.get("epochs", {}) if isinstance(cfg, dict) else {}
        raw = epochs_cfg.get("journal_max_records")
        if raw is None:
            raw = epochs_cfg.get("journal_retention_records")
        if raw is None:
            raw = JOURNAL_RETENTION_RECORDS
        value = int(raw)
        return max(1, value)
    except Exception:
        return JOURNAL_RETENTION_RECORDS


def _prune_journal(root: Path, host: str, host_session_id: str, *, keep_last: int | None = None) -> None:
    journal_dir = _journal_dir(root, host, host_session_id)
    files = _iter_json_files(journal_dir)
    if not files:
        return
    if keep_last is None:
        keep_last = _journal_retention_records(root, host, host_session_id)
    if keep_last <= 0 or len(files) <= keep_last:
        return
    for path in files[:-keep_last]:
        try:
            path.unlink()
        except Exception:
            pass


def _compaction_lease_ttl_seconds(root: Path) -> int:
    try:
        from . import config as config_mod

        cfg = config_mod.load((_project_root(root) / ".burnless" / "config.yaml"))
        encoder_cfg = cfg.get("encoder", {}) if isinstance(cfg, dict) else {}
        raw = encoder_cfg.get("timeout_s")
        if raw is not None:
            return max(60, int(float(raw)) + 30)
    except Exception:
        pass
    return 180


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


def _compaction_lease_path(root_path: Path, host: str, host_session_id: str) -> Path:
    return _canonical_session_root(root_path, host, host_session_id) / COMPACTION_LEASE_NAME


def _load_json_dict(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _lease_expired(payload: dict[str, Any], now: float) -> bool:
    try:
        expires_at = float(payload.get("expires_at") or 0)
    except (TypeError, ValueError):
        expires_at = 0.0
    return expires_at > 0 and now >= expires_at


def _acquire_compaction_lease(
    root_path: Path,
    *,
    host: str,
    host_session_id: str,
    owner: str,
    ttl_seconds: int,
) -> dict[str, Any] | None:
    lease_path = _compaction_lease_path(root_path, host, host_session_id)
    lease_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = lease_path.with_suffix(".lock")
    now = time.time()
    payload = {
        "schema": 1,
        "host": host,
        "host_session_id": host_session_id,
        "owner": owner,
        "generation": 0,
        "applied_through": 0,
        "journal_head": 0,
        "claimed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "expires_at": now + max(5, int(ttl_seconds)),
    }
    with _exclusive_lock(lock_path):
        current = _load_json_dict(lease_path)
        if current and current.get("owner") and not _lease_expired(current, now) and current.get("owner") != owner:
            return None
        if current and current.get("owner") == owner and not _lease_expired(current, now):
            payload.update(current)
            payload["expires_at"] = now + max(5, int(ttl_seconds))
        _atomic_json_write(lease_path, payload)
    return payload


def _refresh_compaction_lease(
    root_path: Path,
    *,
    host: str,
    host_session_id: str,
    owner: str,
    ttl_seconds: int,
) -> bool:
    lease_path = _compaction_lease_path(root_path, host, host_session_id)
    lock_path = lease_path.with_suffix(".lock")
    now = time.time()
    with _exclusive_lock(lock_path):
        current = _load_json_dict(lease_path)
        if not current or current.get("owner") != owner:
            return False
        if _lease_expired(current, now):
            return False
        current["expires_at"] = now + max(5, int(ttl_seconds))
        _atomic_json_write(lease_path, current)
        return True


def _release_compaction_lease(
    root_path: Path,
    *,
    host: str,
    host_session_id: str,
    owner: str,
) -> None:
    lease_path = _compaction_lease_path(root_path, host, host_session_id)
    lock_path = lease_path.with_suffix(".lock")
    with _exclusive_lock(lock_path):
        current = _load_json_dict(lease_path)
        if current and current.get("owner") == owner:
            lease_path.unlink(missing_ok=True)


def _latest_checkpoint_path(root_path: Path, host: str, host_session_id: str) -> Path | None:
    for cp in _checkpoint_paths(root_path, host, host_session_id):
        if cp.exists():
            return cp
    return None


def _latest_project_checkpoint(root_path: Path, host: str) -> tuple[str, dict[str, Any]] | None:
    sessions_root = root_path / "epochs" / SESSION_ROOT_NAME / _safe_part(host)
    if not sessions_root.exists():
        return None
    latest: tuple[tuple[str, float], str, dict[str, Any]] | None = None
    for session_root in sessions_root.iterdir():
        if not session_root.is_dir():
            continue
        checkpoint = _load_json_dict(session_root / CHECKPOINT_NAME)
        if not checkpoint:
            continue
        updated_at = checkpoint.get("updated_at")
        try:
            rank = (str(updated_at or ""), float(session_root.stat().st_mtime))
        except Exception:
            rank = ("", float(session_root.stat().st_mtime))
        session_id = str(checkpoint.get("host_session_id") or session_root.name)
        if latest is None or rank > latest[0]:
            latest = (rank, session_id, checkpoint)
    if latest is None:
        return None
    return latest[1], latest[2]


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
    _prune_journal(root_path, host, host_session_id)
    return payload


_SOURCE_TRUST_BLOCK = """## Aviso de confiança de fonte
O 'Documento anterior' abaixo é um RESUMO PRÉVIO gerado por máquina — pode conter erros. NUNCA o trate como transcript real, e NUNCA estenda narrativas a partir dele. A 'Nova troca/evento' é a ÚNICA fonte de fatos novos, e deve ser lida verbatim.

## Proibições
PROIBIDO: inventar PERGUNTA, RESPOSTA, testes, resultados ou números de seq que não estejam no input; responder à conversa; dirigir-se ao usuário; fazer perguntas. Sua saída é APENAS o documento markdown de memória (as seções descritas acima), nada mais."""


def should_compact(
    checkpoint: dict[str, Any],
    pending: list[dict[str, Any]],
    source: str | None,
    compact_cfg: dict[str, Any],
) -> str:
    """Retorna 'compact' ou 'skip'. Determinístico, sem LLM, sem I/O."""
    if source in ("clear", "end"):
        return "compact"

    from .epochs_v2 import _is_trivial_text

    min_nontrivial = int(compact_cfg.get("min_pending_nontrivial", 3))
    min_tokens = int(compact_cfg.get("min_pending_tokens", 1200))
    max_age_s = int(compact_cfg.get("max_pending_age_s", 900))

    nontrivial_count = sum(
        1 for r in pending if not _is_trivial_text(r.get("user_text") or "")
    )
    if nontrivial_count >= min_nontrivial:
        return "compact"

    total_tokens = sum(
        (len((r.get("user_text") or "") + (r.get("assistant_text") or "")) // 4)
        for r in pending
    )
    if total_tokens >= min_tokens:
        return "compact"

    oldest_captured_at = None
    for r in pending:
        ts = r.get("captured_at")
        if ts and (oldest_captured_at is None or ts < oldest_captured_at):
            oldest_captured_at = ts
    if oldest_captured_at:
        from datetime import datetime, timezone
        try:
            oldest_dt = datetime.strptime(oldest_captured_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            age_s = (datetime.now(timezone.utc) - oldest_dt).total_seconds()
            if age_s >= max_age_s:
                return "compact"
        except (ValueError, TypeError):
            pass

    return "skip"


def _build_compact_prompt(
    checkpoint: dict[str, Any],
    pending: list[dict[str, Any]],
    budget_tokens: int = 2500,
) -> str:
    from .epochs_v2 import living_rewrite_prompt_v3

    exchange_parts = ["## Trocas pendentes"]
    for record in pending:
        exchange_parts.append(f"### seq {record.get('seq')}")
        exchange_parts.append(f"exchange_id: {record.get('exchange_id')}")
        exchange_parts.append("PERGUNTA:")
        exchange_parts.append(record.get("user_text") or "")
        exchange_parts.append("")
        exchange_parts.append("RESPOSTA:")
        exchange_parts.append(record.get("assistant_text") or "")
        if record.get("files"):
            exchange_parts.append(f"files: {', '.join(record.get('files') or [])}")
    exchange = "\n".join(exchange_parts).strip() + "\n"

    prompt = living_rewrite_prompt_v3(
        prev_md=checkpoint.get("living_md") or "",
        exchange=exchange,
        budget_tokens=budget_tokens,
    )
    return prompt.strip() + "\n\n" + _SOURCE_TRUST_BLOCK + "\n"


_PHANTOM_SEQ_RE = re.compile(r"[Ss]eq\s+(\d+)")


def _entry_core(line: str) -> str:
    """Strip trust-boundary prefix ([doctrine]/[state]/[inflight]) and any
    trailing bracketed provenance tags ([chat:ID·tN], [seq N], etc) to get
    the semantic core of a living_md entry line."""
    s = line.strip()
    s = re.sub(r'^\[(?:doctrine|state|inflight)\]\s*', '', s)
    while True:
        m = re.search(r'\s*\[[^\]]*\]\s*$', s)
        if not m:
            break
        s = s[:m.start()]
    return s.strip()


def _validate_candidate(
    candidate: str,
    prev_md: str,
    pending: list[dict[str, Any]],
    *,
    require_seq_origem: bool = False,
) -> tuple[bool, str]:
    from .epochs_v2 import SECTIONS_V3, parse_living_v3

    parsed = parse_living_v3(candidate)
    if not any(parsed.get(section) for section in SECTIONS_V3):
        return False, "no_recognized_sections"

    for raw_line in candidate.split("\n"):
        line = raw_line.strip()
        if line in ("PERGUNTA:", "RESPOSTA:") or line.startswith("RESPOSTA"):
            return False, "chat_completion_markers"

    if "Aguardando a próxima instrução" in candidate:
        return False, "chat_completion_markers"
    if candidate.rstrip().endswith("?"):
        return False, "chat_completion_markers"

    known_seqs = {str(r.get("seq")) for r in pending if r.get("seq") is not None}
    for match in _PHANTOM_SEQ_RE.finditer(candidate):
        seq_value = match.group(1)
        if seq_value not in known_seqs and seq_value not in prev_md:
            return False, f"phantom_seq_{seq_value}"

    if require_seq_origem:
        from .epochs_v2 import parse_living_v3

        parsed_candidate = parse_living_v3(candidate)
        for section in ("Decisões", "Riscos", "Refs"):
            for line in parsed_candidate.get(section, []):
                core = _entry_core(line)
                if not core or core in prev_md:
                    continue
                if not re.search(r"\[seq \d+(?:-\d+)?\]", line):
                    return False, "missing_seq_origem"

    return True, ""


def compact_pending(
    root,
    *,
    host: str,
    host_session_id: str,
    process_instance_id: str,
    rewriter,
    budget_tokens: int = 2500,
    source: str | None = None,
) -> dict[str, Any]:
    root_path = _root_path(root)
    journal_dir = _journal_dir(root_path, host, host_session_id)
    lease_owner = f"{host_session_id}:{process_instance_id}:{os.getpid()}"
    lease_ttl = _compaction_lease_ttl_seconds(root_path)
    lease = _acquire_compaction_lease(
        root_path,
        host=host,
        host_session_id=host_session_id,
        owner=lease_owner,
        ttl_seconds=lease_ttl,
    )
    if lease is None:
        owner_loop.log_owner_event(
            root_path,
            {
                "phase": "recovery",
                "event": "compaction_deferred",
                "host": host,
                "host_session_id": host_session_id,
                "process_instance_id": process_instance_id,
                "reason": "lease_busy",
            },
        )
        return {"status": "busy", "reason": "lease_busy"}

    try:
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
        snapshot_generation = int(checkpoint.get("generation") or 0)
        snapshot_applied = int(checkpoint.get("applied_through") or 0)
        snapshot_head = int(journal_head)

        owner_loop.log_owner_event(
            root_path,
            {
                "phase": "recovery",
                "event": "compaction_started",
                "host": host,
                "host_session_id": host_session_id,
                "process_instance_id": process_instance_id,
                "lease_owner": lease_owner,
                "lease_ttl_s": lease_ttl,
                "generation": snapshot_generation,
                "applied_through": snapshot_applied,
                "journal_head": snapshot_head,
                "pending": len(pending),
            },
        )

        if not pending:
            return {
                "status": "noop",
                "journal_head": snapshot_head,
                "applied_through": snapshot_applied,
                "generation": snapshot_generation,
            }

        from . import config as _config, paths as _paths
        try:
            _cfg = _config.load(_paths.paths_for(root_path)["config"])
        except Exception:
            _cfg = {}
        compact_cfg = ((_cfg.get("epochs") or {}).get("compact") or {})

        decision = should_compact(checkpoint, pending, source, compact_cfg)
        if decision == "skip":
            owner_loop.log_owner_event(
                root_path,
                {
                    "phase": "recovery",
                    "event": "compaction_deferred",
                    "host": host,
                    "host_session_id": host_session_id,
                    "process_instance_id": process_instance_id,
                    "reason": "l0_skip",
                    "pending": len(pending),
                },
            )
            return {
                "status": "deferred",
                "reason": "l0_skip",
                "journal_head": snapshot_head,
                "applied_through": snapshot_applied,
                "generation": snapshot_generation,
            }

        prompt = _build_compact_prompt(checkpoint, pending, budget_tokens=budget_tokens)
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
                    "lease_owner": lease_owner,
                    "error": str(exc),
                },
            )
            return {
                "status": "failed",
                "error": str(exc),
                "journal_head": snapshot_head,
                "applied_through": snapshot_applied,
                "generation": snapshot_generation,
            }

        if not isinstance(candidate, str) or not candidate.strip():
            owner_loop.log_owner_event(
                root_path,
                {
                    "phase": "recovery",
                    "event": "compaction_failed",
                    "host": host,
                    "host_session_id": host_session_id,
                    "process_instance_id": process_instance_id,
                    "lease_owner": lease_owner,
                    "error": "empty output",
                },
            )
            return {
                "status": "failed",
                "error": "empty output",
                "journal_head": snapshot_head,
                "applied_through": snapshot_applied,
                "generation": snapshot_generation,
            }

        if not _refresh_compaction_lease(
            root_path,
            host=host,
            host_session_id=host_session_id,
            owner=lease_owner,
            ttl_seconds=lease_ttl,
        ):
            owner_loop.log_owner_event(
                root_path,
                {
                    "phase": "recovery",
                    "event": "compaction_failed",
                    "host": host,
                    "host_session_id": host_session_id,
                    "process_instance_id": process_instance_id,
                    "lease_owner": lease_owner,
                    "error": "lease_lost",
                },
            )
            return {
                "status": "stale",
                "error": "lease_lost",
                "journal_head": snapshot_head,
                "applied_through": snapshot_applied,
                "generation": snapshot_generation,
            }

        current_checkpoint = read_checkpoint(root_path, host, host_session_id) or checkpoint
        current_generation = int(current_checkpoint.get("generation") or 0)
        current_applied = int(current_checkpoint.get("applied_through") or 0)
        current_head = _journal_head(journal_dir)
        if (
            current_generation != snapshot_generation
            or current_applied != snapshot_applied
            or current_head != snapshot_head
        ):
            owner_loop.log_owner_event(
                root_path,
                {
                    "phase": "recovery",
                    "event": "compaction_failed",
                    "host": host,
                    "host_session_id": host_session_id,
                    "process_instance_id": process_instance_id,
                    "lease_owner": lease_owner,
                    "error": "stale_snapshot",
                    "generation": current_generation,
                    "applied_through": current_applied,
                    "journal_head": current_head,
                },
            )
            return {
                "status": "stale",
                "error": "stale_snapshot",
                "journal_head": current_head,
                "applied_through": current_applied,
                "generation": current_generation,
            }

        ok, reason = _validate_candidate(
            candidate,
            checkpoint.get("living_md") or "",
            pending,
            require_seq_origem=bool(compact_cfg.get("require_seq_origem", False)),
        )
        if not ok:
            owner_loop.log_owner_event(
                root_path,
                {
                    "phase": "recovery",
                    "event": "compaction_rejected",
                    "host": host,
                    "host_session_id": host_session_id,
                    "process_instance_id": process_instance_id,
                    "lease_owner": lease_owner,
                    "reason": reason,
                },
            )
            return {
                "status": "rejected",
                "reason": reason,
                "journal_head": snapshot_head,
                "applied_through": snapshot_applied,
                "generation": snapshot_generation,
            }

        try:
            from .epochs_v2 import enforce_budget_v3

            try:
                recoverables_cap = int(
                    (_cfg.get("epochs") or {}).get("recoverables_max_items", 12)
                )
            except (TypeError, ValueError):
                recoverables_cap = 12
            candidate = enforce_budget_v3(
                candidate,
                budget_tokens=budget_tokens,
                root=root_path,  # same sink as the other recovery owner events
                recoverables_max_items=recoverables_cap,
                event_context={
                    "host": host,
                    "host_session_id": host_session_id,
                    "process_instance_id": process_instance_id,
                },
            )
        except Exception:
            pass

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
            journal_head=snapshot_head,
        )
        # I2 metric: how pointer-shaped is the memory after this compaction?
        refs_lines = 0
        total_lines = 0
        refs_ratio = 0.0
        try:
            from .epochs_v2 import parse_living_v3

            parsed_candidate = parse_living_v3(candidate)
            refs_lines = len(parsed_candidate.get("Refs") or [])
            total_lines = sum(len(entries or []) for entries in parsed_candidate.values())
            refs_ratio = round(refs_lines / total_lines, 3) if total_lines else 0.0
        except Exception:
            pass
        owner_loop.log_owner_event(
            root_path,
            {
                "phase": "recovery",
                "event": "checkpoint_committed",
                "host": host,
                "host_session_id": host_session_id,
                "process_instance_id": process_instance_id,
                "lease_owner": lease_owner,
                "generation": committed["generation"],
                "applied_through": committed["applied_through"],
                "journal_head": committed["journal_head"],
                "refs_lines": refs_lines,
                "total_lines": total_lines,
                "refs_ratio": refs_ratio,
            },
        )
        return {
            "status": "committed",
            "checkpoint": committed,
            "journal_head": snapshot_head,
            "applied_through": committed["applied_through"],
            "generation": committed["generation"],
        }
    finally:
        _release_compaction_lease(
            root_path,
            host=host,
            host_session_id=host_session_id,
            owner=lease_owner,
        )


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


def inherit_checkpoint(
    root,
    *,
    host: str,
    new_session_id: str,
    process_instance_id: str,
    old_session_id: str | None = None,
) -> dict[str, Any] | None:
    """Bootstrap the NEW session's checkpoint from its predecessor's living_md.

    Without this, every rollover restarts the living doc from scratch: the old
    checkpoint is only injected as context (and correctly filtered from
    recapture as restore noise), so carried knowledge decays after ~1 rollover.
    Inheriting makes compaction EVOLVE one long-lived document across sessions
    ("memoria eterna"): applied_through starts at 0, so every new journal
    entry is still pending and gets folded into the inherited doc.

    Idempotent: never overwrites an existing checkpoint for the new session.
    """
    if not new_session_id:
        return None
    root_path = _root_path(root)
    if read_checkpoint(root_path, host, new_session_id) is not None:
        return None
    source_sid = old_session_id
    source_checkpoint: dict[str, Any] | None = None
    if source_sid and source_sid != new_session_id:
        source_checkpoint = read_checkpoint(root_path, host, source_sid)
    if source_checkpoint is None:
        latest = _latest_project_checkpoint(root_path, host)
        if latest is not None and latest[0] != new_session_id:
            source_sid, source_checkpoint = latest
    if not source_checkpoint:
        return None
    living_md = (source_checkpoint.get("living_md") or "").strip()
    if not living_md:
        return None
    harvested = source_checkpoint.get("harvested_state")
    if not isinstance(harvested, dict):
        harvested = {"contracts": [], "refs": [], "open_threads": []}
    committed = write_checkpoint(
        root_path,
        host=host,
        host_session_id=new_session_id,
        process_instance_id=process_instance_id,
        living_md=living_md,
        harvested_state=harvested,
        applied_through=0,
        journal_head=0,
    )
    owner_loop.log_owner_event(
        root_path,
        {
            "phase": "recovery",
            "event": "checkpoint_inherited",
            "host": host,
            "host_session_id": new_session_id,
            "process_instance_id": process_instance_id,
            "inherited_from": source_sid,
            "inherited_generation": int(source_checkpoint.get("generation") or 0),
            "living_chars": len(living_md),
        },
    )
    return committed


def _truncate_tail(text: str, max_chars: int, marker: str) -> str:
    """Truncate only the TAIL of ``text`` (never the middle) to fit
    ``max_chars`` including the marker. Cuts at a line boundary when one is
    close enough so entries stay whole."""
    if len(text) <= max_chars:
        return text
    keep = max(0, max_chars - len(marker) - 1)
    head = text[:keep]
    cut = head.rfind("\n")
    if cut > keep // 2:
        head = head[:cut]
    return head.rstrip() + "\n" + marker


DEFAULT_RESTORE_BUDGET_TOKENS = 4000
DEFAULT_STARTUP_BUDGET_TOKENS = 2000


def _resolve_budget_tokens(root_path: Path, source: str) -> int:
    """A2: budget comes from config when the caller does not pass one.

    epochs.restore_budget_tokens (default 4000) governs clear/rollover
    restores; epochs.startup_budget_tokens (default 2000) governs startup
    seeds. Hooks stopped hardcoding --budget-tokens; the flag remains an
    explicit override.
    """
    key = "startup_budget_tokens" if source == "startup" else "restore_budget_tokens"
    fallback = (
        DEFAULT_STARTUP_BUDGET_TOKENS if source == "startup" else DEFAULT_RESTORE_BUDGET_TOKENS
    )
    try:
        from . import config as config_mod

        cfg = config_mod.load(_project_root(root_path) / ".burnless" / "config.yaml")
        epochs_cfg = cfg.get("epochs", {}) if isinstance(cfg, dict) else {}
        value = int(epochs_cfg.get(key, fallback))
        return max(1, value)
    except Exception:
        return fallback


_RESTORE_POINTER_RULE = (
    "regra: Refs e Recuperáveis são PONTEIROS — use Read/grep sob demanda; "
    "não releia arquivos já conhecidos sem motivo."
)
_MANIFEST_HEADER = "## Manifesto (leia sob demanda, não tudo)"
_PENDING_HEADER = "## Trocas ainda não consolidadas"
_LIVING_TRUNCATED_MARKER = "[living_md truncado — leia o checkpoint completo no Manifesto]"

# Sections that carry "the thread" and are never truncated (A1 layer 2).
_PRIORITY_SECTIONS = ("Foco atual", "Threads abertas")
# Current decisions: inline durable memory (not pointers), protected above
# older pending exchanges and the rest of living_md (A1 layer 4).
_DECISOES_SECTION = ("Decisões",)


def _latest_export_path(root_path: Path) -> Path | None:
    """Newest `.burnless/exports/epoch-*.md` artifact, if any."""
    exports_dir = root_path / "exports"
    try:
        files = sorted(
            (p for p in exports_dir.glob("epoch-*.md") if p.is_file()),
            key=lambda p: (p.stat().st_mtime, p.name),
        )
        return files[-1] if files else None
    except Exception:
        return None


def _render_manifest(
    root_path: Path,
    checkpoint_path: Path | None,
    journal_dir: Path,
    journal_head: int,
    applied_through: int,
) -> str:
    """I1: fixed manifest block — ALWAYS present in a restore, never truncated.

    It is the map for the model to self-serve the rest of the memory on
    demand instead of pasting everything into context.
    """
    lines = [_MANIFEST_HEADER]
    if checkpoint_path is not None:
        lines.append(f"- checkpoint completo: {checkpoint_path}")
    lines.append(f"- journal: {journal_dir} (head={journal_head}, applied={applied_through})")
    export_path = _latest_export_path(root_path)
    if export_path is not None:
        lines.append(f"- exports da sessão anterior: {export_path}")
    lines.append(
        "- Refs do documento vivo: já no formato `path#Lx-y — why [seq N]` — "
        "leia só o que a tarefa atual pedir"
    )
    return "\n".join(lines)


def _render_pending_block(record: dict[str, Any]) -> str:
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
    return "\n".join(block)


def _pending_summary_line(record: dict[str, Any], max_chars: int = 120) -> str:
    """One-line summary for an old pending exchange that did not fit whole."""
    user_text = (record.get("user_text") or "").strip()
    first_line = ""
    for line in user_text.splitlines():
        if line.strip():
            first_line = line.strip()
            break
    if len(first_line) > max_chars:
        first_line = first_line[: max_chars - 1].rstrip() + "…"
    return f"- seq {record.get('seq')} · {first_line}"


def _render_v3_sections(parsed: dict[str, list[str]], names: Iterable[str]) -> str:
    out: list[str] = []
    for name in names:
        entries = parsed.get(name) or []
        if not entries:
            continue
        out.append(f"## {name}")
        for entry in entries:
            if "\n" in entry or entry.startswith("- ") or entry.startswith("#"):
                out.append(entry)
            else:
                out.append(f"- {entry}")
        out.append("")
    return "\n".join(out).rstrip()


def _assemble_restore_layers(
    header: str,
    manifest: str,
    living_md: str,
    pending: list[dict[str, Any]],
    max_chars: int,
) -> tuple[str, int, int]:
    """Priority-layered restore assembly (A1). Used when the naive full render
    exceeds the budget. Never truncates the MIDDLE of anything; instead it
    demotes what does not fit, in this priority order:

    1. metadata header (always whole)
    2. living_md 'Foco atual' + 'Threads abertas' (never truncated)
    3. the single most recent pending exchange WHOLE (continuity reserve —
       the thread's own last turn is served verbatim if it fits at all)
    4. living_md 'Decisões' (current decisions outrank everything below —
       they are inline durable memory, not pointers; tail-truncated only as a
       last resort if the decisions alone overflow the remaining budget)
    5. the remaining (older) pending exchanges WHOLE, newest first, while they
       fit
    6. the rest of living_md — Refs, Riscos, Contracts... (tail-truncated as a
       last resort, never the middle)
    7. old pending exchanges as one-line summaries
    8. manifest block (always whole — see _render_manifest)

    Returns (context, pending_whole_count, pending_summarized_count).
    """
    from .epochs_v2 import SECTIONS_V3, parse_living_v3

    # Fixed overhead: part separators, the pending-section header and the
    # one-line-summary label. Conservative so the final join stays in budget.
    budget = max_chars - len(header) - len(manifest) - 200
    pending_sorted = sorted(pending, key=lambda r: int(r.get("seq") or 0))

    parsed = parse_living_v3(living_md)
    is_v3 = any(parsed.get(section) for section in SECTIONS_V3)

    if is_v3:
        # Layer 2: Foco atual + Threads abertas — never truncated.
        priority_block = _render_v3_sections(parsed, _PRIORITY_SECTIONS)
        # Layer 4: current Decisões — protected above older pending / Refs.
        decisoes_block = _render_v3_sections(parsed, _DECISOES_SECTION)
        # Layer 6: the rest of living_md (Refs, Riscos, Contracts...).
        rest_names = [
            name
            for name in parsed.keys()
            if name not in _PRIORITY_SECTIONS and name not in _DECISOES_SECTION
        ]
        rest_block = _render_v3_sections(parsed, rest_names)
    else:
        # Fallback: living_md does not parse as V3 — serve it first, whole if
        # possible, tail-truncated (never the middle) if not.
        priority_block = living_md.rstrip()
        decisoes_block = ""
        rest_block = ""

    if priority_block:
        budget -= len(priority_block) + 2
        if not is_v3 and budget < 0:
            # Non-V3 docs have no untouchable sections: tail-truncate to keep
            # room for the most recent exchange (the thread itself).
            allowed = max(200, len(priority_block) + budget)
            priority_block = _truncate_tail(priority_block, allowed, _LIVING_TRUNCATED_MARKER)
            budget = 0

    whole_seqs: set[int] = set()

    # Layer 3: continuity reserve — the single most recent pending exchange is
    # served verbatim if it fits at all, BEFORE Decisões claim the budget. The
    # thread's last turn is the anchor for resuming work.
    if pending_sorted:
        newest = pending_sorted[-1]
        newest_cost = len(_render_pending_block(newest)) + 1
        if budget - newest_cost >= 0:
            budget -= newest_cost
            whole_seqs.add(int(newest.get("seq") or 0))

    # Reserve the cost of one-line summaries for every pending exchange that is
    # not (yet) whole, so a decisions block that overflows the budget can still
    # not starve the summaries — the only trace demoted exchanges leave behind.
    summaries_reserve = sum(
        len(_pending_summary_line(r)) + 1
        for r in pending_sorted
        if int(r.get("seq") or 0) not in whole_seqs
    )

    # Layer 4: current Decisões — whole if they fit, tail-truncated only as a
    # last resort. They rank above older pending exchanges and the rest of the
    # living_md because a live decision is inline memory, never a pointer.
    if decisoes_block:
        decisoes_allowance = budget - summaries_reserve
        if len(decisoes_block) + 2 <= budget:
            budget -= len(decisoes_block) + 2
        elif decisoes_allowance > 200:
            decisoes_block = _truncate_tail(
                decisoes_block, decisoes_allowance - 2, _LIVING_TRUNCATED_MARKER
            )
            budget -= len(decisoes_block) + 2
        else:
            decisoes_block = (
                _LIVING_TRUNCATED_MARKER if budget >= len(_LIVING_TRUNCATED_MARKER) else ""
            )
            budget -= len(decisoes_block)

    # Layer 5: remaining (older) pending exchanges whole, newest first, while
    # they fit — but never at the cost of the summaries reserve, so a demoted
    # exchange always leaves at least its one-line trace (no silent drops). A
    # promoted exchange no longer needs its own summary, so it frees that.
    reserve = summaries_reserve
    for record in reversed(pending_sorted):
        seq = int(record.get("seq") or 0)
        if seq in whole_seqs:
            continue
        block_cost = len(_render_pending_block(record)) + 1
        own_summary = len(_pending_summary_line(record)) + 1
        if budget - block_cost < reserve - own_summary:
            break
        budget -= block_cost
        reserve -= own_summary
        whole_seqs.add(seq)

    # Layer 7 cost is reserved up front: one-line summaries are small and
    # bounded, and they are the only trace left of demoted exchanges — the
    # rest of living_md (layer 6) expands into what remains AFTER them.
    summary_lines = [
        (int(r.get("seq") or 0), _pending_summary_line(r))
        for r in pending_sorted
        if int(r.get("seq") or 0) not in whole_seqs
    ]
    summaries_cost = sum(len(line) + 1 for _seq, line in summary_lines)

    # Layer 6: the rest of living_md, whole if it fits, tail-truncated if not.
    if rest_block:
        rest_allowance = budget - summaries_cost
        if len(rest_block) + 2 <= rest_allowance:
            budget -= len(rest_block) + 2
        elif rest_allowance > 200:
            rest_block = _truncate_tail(rest_block, rest_allowance - 2, _LIVING_TRUNCATED_MARKER)
            budget -= len(rest_block) + 2
        else:
            rest_block = _LIVING_TRUNCATED_MARKER if budget - summaries_cost >= len(_LIVING_TRUNCATED_MARKER) else ""
            budget -= len(rest_block)

    # Layer 7: old pending exchanges as one-line summaries (newest first while
    # they fit; presented in chronological order).
    summaries: list[tuple[int, str]] = []
    summarized = 0
    for seq, line in reversed(summary_lines):
        if budget - (len(line) + 1) < 0:
            continue
        budget -= len(line) + 1
        summaries.append((seq, line))
        summarized += 1
    summaries.sort(key=lambda item: item[0])

    parts = [header]
    if priority_block:
        parts += ["", priority_block]
    if decisoes_block:
        parts += ["", decisoes_block]
    if rest_block:
        parts += ["", rest_block]
    if pending_sorted:
        pending_lines: list[str] = ["", _PENDING_HEADER]
        if summaries:
            pending_lines.append("Trocas antigas (resumo de 1 linha; conteúdo no journal — ver Manifesto):")
            pending_lines.extend(line for _seq, line in summaries)
        for record in pending_sorted:
            if int(record.get("seq") or 0) in whole_seqs:
                pending_lines.append(_render_pending_block(record))
        parts.extend(pending_lines)
    parts += ["", manifest]
    context = "\n".join(parts).strip()
    return context, len(whole_seqs), summarized


def render_restore(
    root,
    *,
    host: str,
    host_session_id: str,
    process_instance_id: str,
    new_session_id: str,
    source: str,
    budget_tokens: int | None = None,
) -> dict[str, Any] | None:
    root_path = _root_path(root)
    if budget_tokens is None:
        budget_tokens = _resolve_budget_tokens(root_path, source)
    checkpoint_session_id = host_session_id
    checkpoint = read_checkpoint(root_path, host, checkpoint_session_id)
    if checkpoint is None and source == "startup":
        latest = _latest_project_checkpoint(root_path, host)
        if latest is not None:
            checkpoint_session_id, checkpoint = latest
    checkpoint = checkpoint or {
        "generation": 0,
        "living_md": "",
        "harvested_state": {"contracts": [], "refs": [], "open_threads": []},
        "applied_through": 0,
        "journal_head": 0,
    }
    journal_dir = _journal_dir(root_path, host, checkpoint_session_id)
    records = _read_journal(journal_dir)
    journal_head = _journal_head(journal_dir)
    applied_through = int(checkpoint.get("applied_through") or 0)
    pending = [r for r in records if int(r.get("seq") or 0) > applied_through]

    living_md = checkpoint.get("living_md") or ""
    if not living_md.strip() and not pending and applied_through <= 0 and journal_head <= 0:
        return None

    header_parts = [
        _RESTORE_PREFIX,
        f"host={host}",
        f"old_sid={checkpoint_session_id}",
        f"new_sid={new_session_id}",
        f"process_instance_id={process_instance_id}",
        f"checkpoint_generation={checkpoint.get('generation', 0)}",
        f"applied_through={applied_through}",
        f"journal_head={journal_head}",
        f"pending_count={len(pending)}",
        _RESTORE_POINTER_RULE,
    ]
    header = "\n".join(header_parts)

    checkpoint_chars = len(living_md.strip())
    max_chars = max(800, int(budget_tokens) * 4)
    selected_checkpoint_path = _latest_checkpoint_path(root_path, host, checkpoint_session_id)
    if selected_checkpoint_path is None:
        for cp in _checkpoint_paths(root_path, host, checkpoint_session_id):
            if cp.exists():
                selected_checkpoint_path = cp
                break
    manifest = _render_manifest(
        root_path, selected_checkpoint_path, journal_dir, journal_head, applied_through
    )

    pending_sorted = sorted(pending, key=lambda r: int(r.get("seq") or 0))
    full_parts = [header]
    if living_md.strip():
        full_parts += ["", living_md.rstrip()]
    if pending_sorted:
        full_parts += ["", _PENDING_HEADER]
        full_parts += [_render_pending_block(record) for record in pending_sorted]
    full_parts += ["", manifest]
    full_context = "\n".join(full_parts).strip()

    truncated = False
    pending_whole = len(pending_sorted)
    pending_summarized = 0
    if len(full_context) <= max_chars:
        # Small payload: single-pass render, everything whole (plus manifest).
        context = full_context
    else:
        # Over budget: priority-layered assembly (A1) — demote, never cut the middle.
        truncated = True
        context, pending_whole, pending_summarized = _assemble_restore_layers(
            header, manifest, living_md, pending_sorted, max_chars
        )

    owner_loop.log_owner_event(
        root_path,
        {
            "phase": "recovery",
            "event": "restore_served",
            "host": host,
            "host_session_id": checkpoint_session_id,
            "process_instance_id": process_instance_id,
            "new_session_id": new_session_id,
            "source": source,
            "checkpoint_generation": int(checkpoint.get("generation") or 0),
            "journal_head": journal_head,
            "applied_through": applied_through,
            "pending": len(pending),
            "pending_whole": pending_whole,
            "pending_summarized": pending_summarized,
            "watermark_gap": max(0, journal_head - applied_through),
            "truncated": truncated,
        },
    )
    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        },
        "recovery": {
            "host": host,
            "old_session": checkpoint_session_id,
            "new_session": new_session_id,
            "process_instance_id": process_instance_id,
            "source": source,
            "checkpoint_generation": int(checkpoint.get("generation") or 0),
            "checkpoint_chars": checkpoint_chars,
            "pending_count": len(pending),
            "journal_head": journal_head,
            "applied_through": applied_through,
            "watermark_gap": max(0, journal_head - applied_through),
            "truncated": truncated,
            "pending_whole": pending_whole,
            "pending_summarized": pending_summarized,
            "reference": str(selected_checkpoint_path) if selected_checkpoint_path else None,
        },
    }
