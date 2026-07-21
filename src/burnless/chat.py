"""burnless chat — stitch a chain's session transcripts into one continuous timeline.

CHAT-1: read-only viewer. Parallelizes the native rolling-memory/recovery flow —
it never re-derives chain or checkpoint logic, only reads the artifacts those
modules already write (chain.json, checkpoint.json, handoff.json) and the
Claude Code session transcripts under ~/.claude/projects/.

Guarantees: 100% read-only (no writes anywhere), no LLM calls, no network,
no locks held. `--follow` polls mtime/size on a 1s cadence, single thread.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Iterator

from . import epochs as epochs_mod
from . import recovery as recovery_mod
from . import warm_session as warm_session_mod

HOST = "claude"
POLL_INTERVAL_S = 1.0
_JSON_TURN_FIELDS = ("chain_id", "session_id", "seq", "role", "ts", "text")


# ---------------------------------------------------------------------------
# Root / chain resolution — reuses the same resolver the hooks use.
# ---------------------------------------------------------------------------

def resolve_chat_root(cwd: str | None = None) -> Path | None:
    """Resolve the project root exactly like the hooks do, including the
    orphan store — but read-only: an orphan root is only used if it already
    exists on disk (created earlier by a real hook session), never created
    here (that would be a write, which this viewer never performs)."""
    cwd = cwd if cwd is not None else os.getcwd()
    root = epochs_mod.resolve_root(cwd, workspace=None, transcript=None)
    if root is not None:
        return root
    if not str(cwd or "").strip() or os.environ.get("BURNLESS_NO_ORPHAN"):
        return None
    orphan = epochs_mod.orphan_root_for(cwd)
    return orphan if orphan.exists() else None


def _burnless_dir(project_root: Path) -> Path:
    return recovery_mod._root_path(project_root)


# ---------------------------------------------------------------------------
# Chain listing / session membership
# ---------------------------------------------------------------------------

def list_chains_for_root(project_root: Path, host: str = HOST) -> list[dict[str, Any]]:
    """Live (non-archived) chains for this project, newest chain.json mtime first."""
    burnless_root = _burnless_dir(project_root)
    chains_root = recovery_mod._chains_root(burnless_root)
    out: list[dict[str, Any]] = []
    if not chains_root.exists():
        return out
    for meta_path in chains_root.glob("*/" + recovery_mod.CHAIN_META_NAME):
        if meta_path.parent.parent != chains_root:
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(meta, dict) or meta.get("host") != host:
            continue
        chain_id = str(meta.get("chain_id") or meta_path.parent.name)
        try:
            mtime = meta_path.stat().st_mtime
        except OSError:
            mtime = 0.0
        out.append({"chain_id": chain_id, "meta": meta, "mtime": mtime})
    out.sort(key=lambda c: c["mtime"], reverse=True)
    return out


def newest_chain_id(project_root: Path, host: str = HOST) -> str | None:
    chains = list_chains_for_root(project_root, host=host)
    return chains[0]["chain_id"] if chains else None


def sessions_for_chain(project_root: Path, chain_id: str, host: str = HOST) -> list[dict[str, Any]]:
    """All sessions known to belong to chain_id, oldest -> newest.

    Reuses the checkpoint.json every session already writes (write_checkpoint
    stamps chain_id there) instead of re-deriving chain membership. Falls
    back to the chain's current handoff.json for a session that hasn't
    reached a checkpoint yet (e.g. a very short session)."""
    burnless_root = _burnless_dir(project_root)
    sessions: dict[str, str] = {}

    sessions_root = burnless_root / "epochs" / recovery_mod.SESSION_ROOT_NAME / recovery_mod._safe_part(host)
    if sessions_root.exists():
        for cp_path in sessions_root.glob(f"*/{recovery_mod.CHECKPOINT_NAME}"):
            try:
                payload = json.loads(cp_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            if str(payload.get("chain_id") or "") != chain_id:
                continue
            sid = str(payload.get("host_session_id") or cp_path.parent.name)
            sessions[sid] = str(payload.get("updated_at") or "")

    chain_dir = recovery_mod._chain_dir(burnless_root, chain_id)
    handoff_path = chain_dir / recovery_mod.CHAIN_HANDOFF_NAME
    if handoff_path.exists():
        try:
            hp = json.loads(handoff_path.read_text(encoding="utf-8"))
        except Exception:
            hp = None
        if isinstance(hp, dict):
            for sid_key, ts_key in (("host_session_id", "created_at"), ("claimed_by", "claimed_at")):
                sid = hp.get(sid_key)
                if sid and str(sid) not in sessions:
                    sessions[str(sid)] = str(hp.get(ts_key) or "")

    ordered = sorted(sessions.items(), key=lambda kv: kv[1] or "")
    return [{"session_id": sid, "updated_at": ts} for sid, ts in ordered]


def _context_note_for(project_root: Path, session_id: str, host: str = HOST) -> str | None:
    """Optional context-usage metric from the session's handoff artifact, if
    one was ever recorded. No such field exists in current handoff payloads
    as of this writing — this looks for a few plausible keys defensively and
    returns None (never a fabricated number) when absent."""
    burnless_root = _burnless_dir(project_root)
    handoff_path = recovery_mod._rolling_root(burnless_root) / recovery_mod.HANDOFF_DIR_NAME / f"{recovery_mod._safe_part(session_id)}.json"
    if not handoff_path.exists():
        return None
    try:
        payload = json.loads(handoff_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    for key in ("context_pct", "context_percent", "ctx_pct", "context_used_pct"):
        val = payload.get(key)
        if isinstance(val, (int, float)):
            return f"~{val:.0f}% contexto"
    return None


# ---------------------------------------------------------------------------
# Transcript resolution + parsing
# ---------------------------------------------------------------------------

def find_transcript(session_id: str, projects_root: Path | None = None) -> Path | None:
    if projects_root is None:
        matches = warm_session_mod.find_transcript_paths(session_id)
    else:
        matches = list(projects_root.glob(f"*/{session_id}.jsonl")) if projects_root.exists() else []
    if not matches:
        return None
    try:
        return max(matches, key=lambda p: p.stat().st_mtime)
    except OSError:
        return matches[0]


def _parse_transcript_turn(record: dict[str, Any]) -> dict[str, Any] | None:
    message = record.get("message")
    if not isinstance(message, dict):
        return None
    role = str(message.get("role") or "").strip().lower()
    if role not in ("user", "assistant"):
        return None
    if record.get("isSidechain") or message.get("isSidechain"):
        return None

    content = message.get("content")
    text_parts: list[str] = []
    tool_names: list[str] = []
    if isinstance(content, str):
        text_parts.append(content)
    elif isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                t = block.get("text")
                if isinstance(t, str):
                    text_parts.append(t)
            elif btype == "tool_use":
                tool_names.append(str(block.get("name") or "tool"))
            elif btype == "tool_result":
                tool_names.append("result")

    text = "\n".join(p for p in text_parts if p).strip()
    if not text and not tool_names:
        return None
    return {"role": role, "ts": str(record.get("timestamp") or ""), "text": text, "tool_names": tool_names}


def _parse_transcript_line(line: str) -> dict[str, Any] | None:
    line = line.strip()
    if not line:
        return None
    try:
        record = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    return _parse_transcript_turn(record)


def read_transcript_turns(path: Path) -> list[dict[str, Any]]:
    """Every user/assistant turn in a transcript. Malformed/partial JSONL
    lines (the file may be actively written by a live session) are skipped
    silently, never raised."""
    turns: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                turn = _parse_transcript_line(line)
                if turn is not None:
                    turns.append(turn)
    except OSError:
        pass
    return turns


def _read_new_turns(path: Path, offset: int) -> tuple[int, list[dict[str, Any]]]:
    """Incremental read from a byte offset, for --follow. A trailing line
    without a newline yet (writer mid-flush) is left unconsumed and retried
    on the next poll."""
    turns: list[dict[str, Any]] = []
    new_offset = offset
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            f.seek(offset)
            while True:
                line_start = f.tell()
                line = f.readline()
                if not line:
                    break
                if not line.endswith("\n"):
                    new_offset = line_start
                    break
                turn = _parse_transcript_line(line)
                if turn is not None:
                    turns.append(turn)
                new_offset = f.tell()
    except OSError:
        return offset, []
    return new_offset, turns


# ---------------------------------------------------------------------------
# Timeline stitching
# ---------------------------------------------------------------------------

def stitch_events(
    project_root: Path,
    chain_id: str,
    host: str = HOST,
    projects_root: Path | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield the chain's continuous timeline, oldest first: `boundary` events
    mark a session rollover (never emitted for the chain's first/opening
    session — that's the start of the chat, not a rollover), `missing`
    events mark a session whose transcript could not be found, and `turn`
    events carry one user/assistant exchange each with a chain-wide seq."""
    sessions = sessions_for_chain(project_root, chain_id, host=host)
    seq = 0
    for idx, entry in enumerate(sessions, start=1):
        sid = entry["session_id"]
        path = find_transcript(sid, projects_root=projects_root)
        turns = read_transcript_turns(path) if path is not None else []

        if idx > 1:
            boundary_ts = turns[0]["ts"] if turns else (entry.get("updated_at") or "")
            yield {
                "kind": "boundary",
                "session_id": sid,
                "index": idx,
                "ts": boundary_ts,
                "context_note": _context_note_for(project_root, sid, host=host),
            }

        if path is None:
            yield {"kind": "missing", "session_id": sid}
            continue

        for turn in turns:
            seq += 1
            yield {
                "kind": "turn",
                "chain_id": chain_id,
                "session_id": sid,
                "seq": seq,
                "role": turn["role"],
                "ts": turn["ts"],
                "text": turn["text"],
                "tool_names": turn["tool_names"],
            }


def current_follow_target(
    sessions: list[dict[str, Any]],
    transcript_paths: dict[str, Path],
) -> Path | None:
    """Pure decision: given the chain's sessions (oldest -> newest) and a
    session_id -> resolved transcript path map, which file should --follow
    be tailing right now? The newest session with a transcript on disk."""
    for entry in reversed(sessions):
        p = transcript_paths.get(entry["session_id"])
        if p is not None:
            return p
    return None


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _hhmm(ts: str) -> str:
    if not ts or "T" not in ts:
        return "--:--"
    try:
        return ts.split("T", 1)[1][:5]
    except Exception:
        return "--:--"


def _format_boundary(index: int, session_id: str, ts: str, context_note: str | None) -> str:
    sid_short = (session_id or "")[:8]
    parts = ["nova janela", f"{index}ª da chain", sid_short, ts or "--"]
    if context_note:
        parts.append(context_note)
    return "── " + " · ".join(parts) + " ──"


def _format_turn(ev: dict[str, Any], verbose: bool = False) -> str:
    lines = [f"[{_hhmm(ev['ts'])}] {ev['role']}"]
    if ev.get("text"):
        lines.append(ev["text"])
    tool_names = ev.get("tool_names") or []
    if tool_names:
        n = len(tool_names)
        label = "ferramenta" if n == 1 else "ferramentas"
        if verbose:
            lines.append(f"  ({n} {label}: {', '.join(tool_names)})")
        else:
            lines.append(f"  ({n} {label})")
    lines.append("")
    return "\n".join(lines) + "\n"


def turn_to_json(ev: dict[str, Any]) -> str:
    return json.dumps({k: ev[k] for k in _JSON_TURN_FIELDS}, ensure_ascii=False)


def emit_event(ev: dict[str, Any], out, as_json: bool = False, verbose: bool = False) -> None:
    if as_json:
        if ev["kind"] == "turn":
            out.write(turn_to_json(ev) + "\n")
        return
    if ev["kind"] == "boundary":
        out.write(_format_boundary(ev["index"], ev["session_id"], ev["ts"], ev.get("context_note")) + "\n")
    elif ev["kind"] == "missing":
        out.write(f"(transcript não encontrado: {(ev['session_id'] or '')[:8]})\n")
    elif ev["kind"] == "turn":
        out.write(_format_turn(ev, verbose=verbose))
    out.flush()


# ---------------------------------------------------------------------------
# CLI entry points
# ---------------------------------------------------------------------------

def _cmd_list(root: Path, host: str = HOST, out=sys.stdout) -> int:
    chains = list_chains_for_root(root, host=host)
    if not chains:
        out.write("(nenhuma chain encontrada)\n")
        return 0
    for c in chains:
        sessions = sessions_for_chain(root, c["chain_id"], host=host)
        last_seen = c["meta"].get("last_seen") or ""
        out.write(f"{c['chain_id']}  sessions={len(sessions)}  last_seen={last_seen}\n")
    return 0


def _follow(
    root: Path,
    chain_id: str,
    host: str = HOST,
    as_json: bool = False,
    verbose: bool = False,
    seq_start: int = 0,
    initial_offsets: dict[str, int] | None = None,
    out=sys.stdout,
) -> int:
    offsets: dict[str, int] = dict(initial_offsets or {})
    seq = seq_start
    try:
        while True:
            time.sleep(POLL_INTERVAL_S)
            sessions = sessions_for_chain(root, chain_id, host=host)
            if not sessions:
                continue
            transcript_paths: dict[str, Path] = {}
            for entry in sessions:
                p = find_transcript(entry["session_id"])
                if p is not None:
                    transcript_paths[entry["session_id"]] = p
            target = current_follow_target(sessions, transcript_paths)
            if target is None:
                continue
            sid = next(s["session_id"] for s in sessions if transcript_paths.get(s["session_id"]) == target)

            if sid not in offsets:
                idx = next(i for i, s in enumerate(sessions, start=1) if s["session_id"] == sid)
                seen = read_transcript_turns(target)
                boundary_ts = seen[0]["ts"] if seen else ""
                emit_event(
                    {"kind": "boundary", "session_id": sid, "index": idx, "ts": boundary_ts, "context_note": None},
                    out, as_json=as_json, verbose=verbose,
                )
                offsets[sid] = 0

            new_offset, new_turns = _read_new_turns(target, offsets[sid])
            offsets[sid] = new_offset
            for turn in new_turns:
                seq += 1
                emit_event(
                    {"kind": "turn", "chain_id": chain_id, "session_id": sid, "seq": seq, **turn},
                    out, as_json=as_json, verbose=verbose,
                )
    except KeyboardInterrupt:
        out.write("\n")
        return 0


def main(args: Any) -> int:
    cwd = getattr(args, "cwd", None) or os.getcwd()
    root = resolve_chat_root(cwd)
    if root is None:
        print("burnless chat: could not resolve a project root for this cwd", file=sys.stderr)
        return 1

    host = HOST

    if getattr(args, "list", False):
        return _cmd_list(root, host=host)

    chain_id = getattr(args, "chain", None) or newest_chain_id(root, host=host)
    if not chain_id:
        print("burnless chat: no chains found for this project", file=sys.stderr)
        return 1

    if getattr(args, "serve", None) is not None:
        from . import chat_serve
        return chat_serve.serve_chat(root, chain_id, args.serve)

    as_json = bool(getattr(args, "json", False))
    verbose = bool(getattr(args, "verbose", False))
    follow = bool(getattr(args, "follow", False))

    events = list(stitch_events(root, chain_id, host=host))
    for ev in events:
        emit_event(ev, sys.stdout, as_json=as_json, verbose=verbose)

    if not follow:
        return 0

    seq = max((ev["seq"] for ev in events if ev["kind"] == "turn"), default=0)
    sessions = sessions_for_chain(root, chain_id, host=host)
    offsets: dict[str, int] = {}
    if sessions:
        last_sid = sessions[-1]["session_id"]
        last_path = find_transcript(last_sid)
        if last_path is not None:
            try:
                offsets[last_sid] = last_path.stat().st_size
            except OSError:
                offsets[last_sid] = 0

    return _follow(root, chain_id, host=host, as_json=as_json, verbose=verbose, seq_start=seq, initial_offsets=offsets)
