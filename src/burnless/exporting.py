"""Epoch export: persist the consolidated living_md as a neutral on-disk artifact.

On SessionEnd the hot memory (living_md V3) is exported verbatim to
``<project>/.burnless/exports/epoch-<host>-<sid8>-<UTCts>.md`` with a small
front-matter header (schema ``burnless-epoch-export/v1``, see PROTOCOL.md).

The export is a ONE-WAY artifact: burnless only writes files here. Any
external consumer pulls these files on its own schedule and keeps its own
ledger — burnless never calls another app, and consumers must never write
inside ``.burnless/``.
"""

from __future__ import annotations

import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from .markers import to_en_markers

SCHEMA = "burnless-epoch-export/v1"
EXPORTS_DIRNAME = "exports"
DEFAULT_EXPORTS_KEEP = 30


def _exports_keep(root_path: Path) -> int:
    """Read epochs.exports_keep from the project config.yaml (default 30)."""
    try:
        cfg_path = root_path / "config.yaml"
        if not cfg_path.exists():
            return DEFAULT_EXPORTS_KEEP
        import yaml

        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        keep = (data.get("epochs") or {}).get("exports_keep", DEFAULT_EXPORTS_KEEP)
        return max(1, int(keep))
    except Exception:
        return DEFAULT_EXPORTS_KEEP


def _index_enabled(root_path: Path) -> bool:
    """Read epochs.index from config.yaml (default False). Fail-closed."""
    try:
        cfg_path = root_path / "config.yaml"
        if not cfg_path.exists():
            return False
        import yaml

        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        return bool((data.get("epochs") or {}).get("index", False))
    except Exception:
        return False


def _export_title(content: str) -> str:
    """Extract title from export content. Find '## Current focus' or '## Foco atual',
    then return the first non-empty line following it, stripped of markers."""
    lines = content.splitlines()
    focus_idx = -1
    for i, line in enumerate(lines):
        if line.strip() in ("## Current focus", "## Foco atual"):
            focus_idx = i
            break
    if focus_idx < 0 or focus_idx >= len(lines) - 1:
        return ""
    for i in range(focus_idx + 1, len(lines)):
        candidate = lines[i].strip()
        if not candidate:
            continue
        candidate = re.sub(r"^\- ", "", candidate)
        candidate = re.sub(r"^\[(?:state|inflight)\]\s*", "", candidate)
        candidate = re.sub(r"\s*\[chat:[^\]]*\]", "", candidate)
        candidate = candidate[:100]
        return candidate
    return ""


def update_epoch_index(root_path, *, created: str, host: str, sid8: str, generation, export_filename: str, title: str) -> None:
    """Update per-project INDEX.md with epoch export record. Append-only, idempotent."""
    try:
        epochs_dir = root_path / "epochs"
        index_path = epochs_dir / "INDEX.md"
        if export_filename in (index_path.read_text(encoding="utf-8") if index_path.exists() else ""):
            return
        epochs_dir.mkdir(parents=True, exist_ok=True)
        project = root_path.parent.name
        if index_path.exists():
            current = index_path.read_text(encoding="utf-8")
        else:
            current = f"# Epoch index — {project}\n\n"
        title_display = title if title else "(sem título)"
        line = f"- {created} · {host} · {sid8} · gen{generation} · {title_display} · exports/{export_filename}\n"
        _atomic_write(index_path, current + line)
    except Exception:
        pass


def backfill_epoch_index(root, host: str = "", host_session_id: str = "") -> dict:
    """Backfill INDEX.md from existing exports. Returns status dict."""
    try:
        from . import recovery

        root_path = recovery._root_path(root)
        exports_dir = root_path / EXPORTS_DIRNAME
        if not exports_dir.exists():
            return {"status": "indexed", "added": 0, "total": 0}

        index_path = root_path / "epochs" / "INDEX.md"
        before_entries = 0
        if index_path.exists():
            content = index_path.read_text(encoding="utf-8")
            before_entries = sum(1 for line in content.splitlines() if line.startswith("- "))

        exports = sorted(
            (p for p in exports_dir.glob("epoch-*.md") if p.is_file()),
            key=lambda p: p.stat().st_mtime
        )

        for export_file in exports:
            try:
                content = export_file.read_text(encoding="utf-8")
                lines = content.splitlines()
                front_matter = {}
                in_front = False
                for line in lines:
                    if line.strip() == "---":
                        if not in_front:
                            in_front = True
                        else:
                            break
                    elif in_front and ":" in line:
                        key, val = line.split(":", 1)
                        front_matter[key.strip()] = val.strip()

                created = front_matter.get("created", "")
                host_fm = front_matter.get("host", host or "")
                host_session_id_fm = front_matter.get("host_session_id", host_session_id or "")
                generation = front_matter.get("generation", "")
                sid8 = host_session_id_fm[:8] if host_session_id_fm else (host_session_id or "")[:8]
                title = _export_title(content)
                update_epoch_index(root_path, created=created, host=host_fm, sid8=sid8, generation=generation, export_filename=export_file.name, title=title)
            except Exception:
                pass

        after_entries = 0
        if index_path.exists():
            content = index_path.read_text(encoding="utf-8")
            after_entries = sum(1 for line in content.splitlines() if line.startswith("- "))

        added = max(0, after_entries - before_entries)
        return {"status": "indexed", "added": added, "total": len(exports)}
    except Exception:
        return {"status": "indexed", "added": 0, "total": 0}


def _atomic_write(path: Path, content: str) -> None:
    """tmp + fsync + os.replace so a crash never leaves a partial export."""
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
    try:  # best-effort directory fsync so the rename itself is durable
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass


def _gc_exports(exports_dir: Path, keep: int) -> list[str]:
    """Remove the oldest exports beyond `keep`. Returns removed filenames."""
    removed: list[str] = []
    try:
        files = sorted(
            (p for p in exports_dir.glob("epoch-*.md") if p.is_file()),
            key=lambda p: (p.stat().st_mtime, p.name),
        )
        excess = len(files) - max(1, keep)
        for stale in files[: max(0, excess)]:
            try:
                stale.unlink()
                removed.append(stale.name)
            except OSError:
                pass
    except Exception:
        pass
    return removed


def render_export(project: str, host: str, host_session_id: str,
                  checkpoint: dict[str, Any], created: str) -> str:
    """Front-matter (burnless-epoch-export/v1) + living_md verbatim."""
    living_md = checkpoint.get("living_md") or ""
    front = "\n".join(
        [
            "---",
            f"schema: {SCHEMA}",
            f"project: {project}",
            f"host: {host}",
            f"host_session_id: {host_session_id}",
            f"generation: {checkpoint.get('generation')}",
            f"applied_through: {checkpoint.get('applied_through')}",
            f"journal_head: {checkpoint.get('journal_head')}",
            f"created: {created}",
            "---",
            "",
        ]
    )
    return front + living_md


def export_epoch(root, host: str, host_session_id: str) -> dict[str, Any]:
    """Export the consolidated living_md to `.burnless/exports/`.

    Fail-open: any error here must never affect the hot checkpoint. Always
    returns a status dict, never raises. Skips when living_md is empty.
    """
    try:
        from . import recovery

        root_path = recovery._root_path(root)
        checkpoint = recovery.read_checkpoint(root_path, host, host_session_id)
        living_md = (checkpoint or {}).get("living_md") or ""
        if not living_md.strip():
            return {"status": "export_skipped", "reason": "empty_living_md"}

        project = recovery._project_root(root_path).name
        sid8 = host_session_id[:8]
        created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        ts = created.replace("-", "").replace(":", "")
        exports_dir = root_path / EXPORTS_DIRNAME
        exports_dir.mkdir(parents=True, exist_ok=True)

        target = exports_dir / f"epoch-{host}-{sid8}-{ts}.md"
        content = render_export(project, host, host_session_id, checkpoint, created)
        if recovery._format_en_markers(root_path):
            content = to_en_markers(content)
        _atomic_write(target, content)
        if _index_enabled(root_path):
            title = _export_title(content)
            update_epoch_index(root_path, created=created, host=host, sid8=sid8, generation=checkpoint.get("generation"), export_filename=target.name, title=title)
        removed = _gc_exports(exports_dir, _exports_keep(root_path))
        return {"status": "exported", "path": str(target), "gc_removed": removed}
    except Exception as exc:
        return {"status": "export_skipped", "reason": str(exc)}
