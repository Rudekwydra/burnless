import json
import hashlib
from pathlib import Path


SCHEMA_VERSION = 1


def build_record(
    *,
    delegation_id,
    status,
    worker_status="",
    audit_status="",
    verify_status="",
    commands=None,
    files_declared=None,
    files_verified=None,
    file_hashes_before=None,
    file_hashes_after=None,
    tests_declared=None,
    tests_seen=None,
    capsule_ref="",
    raw_log_ref="",
    created_at="",
    files_changed=None,
    diff_stats=None,
    suspicious=False,
):
    return {
        "schema_version": SCHEMA_VERSION,
        "delegation_id": delegation_id,
        "status": status,
        "worker_status": worker_status,
        "audit_status": audit_status,
        "verify_status": verify_status,
        "commands": commands if commands is not None else [],
        "files_declared": files_declared if files_declared is not None else [],
        "files_verified": files_verified if files_verified is not None else [],
        "file_hashes_before": file_hashes_before if file_hashes_before is not None else {},
        "file_hashes_after": file_hashes_after if file_hashes_after is not None else {},
        "tests_declared": tests_declared if tests_declared is not None else [],
        "tests_seen": tests_seen if tests_seen is not None else [],
        "capsule_ref": capsule_ref,
        "raw_log_ref": raw_log_ref,
        "created_at": created_at,
        "files_changed": files_changed if files_changed is not None else [],
        "diff_stats": diff_stats if diff_stats is not None else {},
        "suspicious": bool(suspicious),
    }


def audit_graph_path(root):
    return Path(root) / ".burnless" / "audit_graph.jsonl"


def append_record(root, record):
    try:
        path = audit_graph_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps(record) + "\n")
        return True
    except Exception:
        return False


def read_records(root, delegation_id=None):
    path = audit_graph_path(root)
    if not path.exists():
        return []

    records = []
    try:
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    if delegation_id is None or record.get("delegation_id") == delegation_id:
                        records.append(record)
                except (json.JSONDecodeError, ValueError):
                    continue
    except Exception:
        pass

    return records


def hash_file(path, max_bytes=5_000_000):
    try:
        p = Path(path)
        if not p.exists() or not p.is_file():
            return None
        if p.stat().st_size > max_bytes:
            return None

        hasher = hashlib.sha256()
        with open(p, "rb") as f:
            hasher.update(f.read())
        return "sha256:" + hasher.hexdigest()[:16]
    except Exception:
        return None


def render_one(record):
    delegation_id = record.get("delegation_id", "?")
    status = record.get("status", "?")
    verify_status = record.get("verify_status", "")
    files_declared = len(record.get("files_declared", []))
    files_verified = len(record.get("files_verified", []))
    commands = len(record.get("commands", []))

    parts = [f"{delegation_id} {status}"]

    if verify_status:
        parts.append(f"verify:{verify_status}")

    if files_declared > 0 or files_verified > 0:
        parts.append(f"files {files_verified}/{files_declared}")

    if commands > 0:
        parts.append(f"cmds {commands}")

    out = " · ".join(parts)

    diff_stats = record.get("diff_stats") or {}
    if isinstance(diff_stats, dict):
        ins = diff_stats.get("insertions", 0) or 0
        dels = diff_stats.get("deletions", 0) or 0
        if ins or dels:
            out += f" +{ins}/-{dels}"

    if record.get("suspicious"):
        out += " ⚠SUSPICIOUS"

    return out


def render(records):
    if not records:
        return ""
    return "\n".join(render_one(r) for r in records)
