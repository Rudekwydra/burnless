from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from .core import ContextUsage
from .cadence import CadenceConfig
from .cadence_controller import CadenceController
from .compact_detect import detect_compact_summaries


def resolve_transcript_path(project_root: Path, run_id: str) -> Path | None:
    """Resolve the Claude JSONL transcript for a pilot run via the events log's transcript_ref."""
    from .events import events_path

    path = events_path(Path(project_root), run_id)
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
    return Path(transcript_ref)


def backlog_turns_since_last_compact(transcript_path: Path | None) -> int:
    """Count assistant records after the last isCompactSummary line (all assistant records if none)."""
    if transcript_path is None or not transcript_path.exists():
        return 0
    summaries = detect_compact_summaries(transcript_path)
    after_line = summaries[-1].line_index if summaries else -1
    count = 0
    try:
        with transcript_path.open("r", encoding="utf-8", errors="replace") as f:
            for line_index, line in enumerate(f):
                if line_index <= after_line:
                    continue
                s = line.strip()
                if not s:
                    continue
                try:
                    rec = json.loads(s)
                except json.JSONDecodeError:
                    continue
                if rec.get("type") == "assistant":
                    count += 1
    except OSError:
        return 0
    return count


def epoch_focus(project_root: Path, chat_id: str) -> str:
    """Best-effort current-focus string for /compact focus; empty string when unavailable (v1)."""
    return ""


def build_cadence_controller(
    *,
    adapter,
    project_root: Path,
    run_id: str,
    host_session_id: str,
    cfg: dict | None = None,
) -> CadenceController:
    """Assemble a CadenceController wired to the real host adapter + transcript providers."""
    cfg = cfg or {}

    def _session():
        s = adapter.locate_session(host_session_id)
        if s is not None and getattr(s, "cwd", None) != str(project_root):
            try:
                s = replace(s, cwd=str(project_root))
            except Exception:
                pass
        return s

    def _usage() -> ContextUsage:
        try:
            return adapter.context_usage(_session())
        except Exception:
            return ContextUsage(current=None, limit=None)

    def _idle() -> bool:
        try:
            return bool(adapter.is_turn_idle(_session()))
        except Exception:
            return False

    def _backlog() -> int:
        return backlog_turns_since_last_compact(resolve_transcript_path(project_root, run_id))

    def _focus() -> str:
        return epoch_focus(project_root, run_id)

    cad_cfg = CadenceConfig(
        min_backlog_turns=int(cfg.get("min_backlog_turns", 4)),
        soft_ceiling_ratio=float(cfg.get("soft_ceiling_ratio", 0.70)),
        hard_ceiling_ratio=float(cfg.get("hard_ceiling_ratio", 0.88)),
        backlog_forces_turns=int(cfg.get("backlog_forces_turns", 12)),
    )
    return CadenceController(
        usage_provider=_usage,
        idle_provider=_idle,
        backlog_provider=_backlog,
        focus_provider=_focus,
        cfg=cad_cfg,
        poll_interval_s=float(cfg.get("poll_interval_s", 3.0)),
        cooldown_s=float(cfg.get("cooldown_s", 30.0)),
    )
