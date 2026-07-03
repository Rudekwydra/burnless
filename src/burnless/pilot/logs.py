from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..usage_meter import claude_usage_delta
from .core import ContextUsage


def _normalize_path(value: str | Path | None) -> Path | None:
    if value is None:
        return None
    try:
        return Path(value).expanduser().resolve()
    except Exception:
        return None


def _estimated_claude_limit(_cwd: Path | None) -> int:
    # Claude logs here do not carry a context-window field; use a conservative
    # project-agnostic estimate so rollover can still make a decision.
    return 200_000


def claude_context_usage(cwd: str | Path | None) -> ContextUsage:
    project = _normalize_path(cwd)
    delta = claude_usage_delta(cwd=project)
    if delta.calls <= 0:
        return ContextUsage(current=None, limit=None, confidence="unknown")
    current = max(0, int(delta.cold_baseline_input_tokens))
    return ContextUsage(current=current, limit=_estimated_claude_limit(project), confidence="estimated")


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
