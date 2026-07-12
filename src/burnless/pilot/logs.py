from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..usage_meter import claude_project_dir
from .core import ContextUsage


def _normalize_path(value: str | Path | None) -> Path | None:
    if value is None:
        return None
    try:
        return Path(value).expanduser().resolve()
    except Exception:
        return None


def _last_assistant_usage_tokens(transcript_path: Path) -> int | None:
    """Scan a Claude JSONL transcript and return the last assistant record's usage sum."""
    if not transcript_path.exists():
        return None
    last_usage = None
    try:
        with transcript_path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") != "assistant":
                    continue
                message = rec.get("message") or {}
                usage = message.get("usage")
                if isinstance(usage, dict):
                    last_usage = usage
    except OSError:
        return None
    if last_usage is None:
        return None
    return (
        int(last_usage.get("input_tokens") or 0)
        + int(last_usage.get("cache_read_input_tokens") or 0)
        + int(last_usage.get("cache_creation_input_tokens") or 0)
        + int(last_usage.get("output_tokens") or 0)
    )


def _estimated_claude_limit(_cwd: Path | None) -> int:
    # Claude logs here do not carry a context-window field; use a conservative
    # project-agnostic estimate so rollover can still make a decision.
    return 200_000


def _claude_context_usage_from_transcript(root: Path, run_id: str) -> "ContextUsage | None":
    from .events import events_path

    path = events_path(Path(root), run_id)
    if not path.exists():
        return None
    transcript_ref = None
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ref = obj.get("transcript_ref")
                if ref:
                    transcript_ref = ref
    except OSError:
        return None
    if not transcript_ref:
        return None
    transcript_path = Path(transcript_ref)
    current = _last_assistant_usage_tokens(transcript_path)
    if current is None:
        return None
    return ContextUsage(current=current, limit=_estimated_claude_limit(root), confidence="exact")


def claude_context_usage(
    cwd: str | Path | None,
    *,
    root: str | Path | None = None,
    run_id: str | None = None,
) -> ContextUsage:
    if root is not None and run_id:
        exact = _claude_context_usage_from_transcript(Path(root), run_id)
        if exact is not None:
            return exact
    project = _normalize_path(cwd)
    try:
        project_dir = claude_project_dir(cwd=project)
        if project_dir.exists():
            jsonl_files = list(project_dir.glob("*.jsonl"))
            if jsonl_files:
                jsonl_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                newest = jsonl_files[0]
                current = _last_assistant_usage_tokens(newest)
                if current is not None:
                    return ContextUsage(current=current, limit=_estimated_claude_limit(project), confidence="estimated")
    except OSError:
        pass
    return ContextUsage(current=None, limit=None, confidence="unknown")


def _iter_codex_logs() -> list[Path]:
    root = Path.home() / ".codex" / "sessions"
    if not root.exists():
        return []
    files = [p for p in root.rglob("*.jsonl") if p.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files


def _codex_log_matches_cwd(path: Path, cwd: Path | None) -> bool:
    if cwd is None:
        return False
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") != "session_meta":
                    continue
                payload = obj.get("payload") or {}
                if not isinstance(payload, dict):
                    continue
                payload_cwd = payload.get("cwd")
                if not payload_cwd:
                    continue
                return _normalize_path(payload_cwd) == cwd
    except OSError:
        return False
    return False


def _codex_usage_from_log(path: Path) -> ContextUsage:
    current = None
    limit = None
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload = obj.get("payload") or {}
                if not isinstance(payload, dict):
                    continue
                if obj.get("type") == "event_msg" and payload.get("type") == "token_count":
                    info = payload.get("info") or {}
                    if isinstance(info, dict):
                        total = info.get("total_token_usage") or {}
                        if isinstance(total, dict):
                            try:
                                current = int(total.get("total_tokens") or 0)
                            except (TypeError, ValueError):
                                current = current
                        try:
                            limit = int(info.get("model_context_window") or 0) or limit
                        except (TypeError, ValueError):
                            pass
                elif obj.get("type") == "turn.completed":
                    usage = payload.get("usage") or {}
                    if isinstance(usage, dict):
                        try:
                            current = int(usage.get("total_tokens") or usage.get("input_tokens") or current or 0)
                        except (TypeError, ValueError):
                            pass
                        try:
                            limit = int(usage.get("model_context_window") or 0) or limit
                        except (TypeError, ValueError):
                            pass
    except OSError:
        return ContextUsage(current=None, limit=None, confidence="unknown")

    if current is None:
        return ContextUsage(current=None, limit=None, confidence="unknown")
    return ContextUsage(current=current, limit=limit, confidence="exact" if limit else "estimated")


def codex_context_usage(cwd: str | Path | None) -> ContextUsage:
    project = _normalize_path(cwd)
    if project is None:
        return ContextUsage(current=None, limit=None, confidence="unknown")
    for path in _iter_codex_logs():
        if _codex_log_matches_cwd(path, project):
            usage = _codex_usage_from_log(path)
            if usage.current is not None:
                return usage
    return ContextUsage(current=None, limit=None, confidence="unknown")
