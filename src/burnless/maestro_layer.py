"""3-layer pipeline: Maestro subprocess management (v1 engine path).

User flow:
  IDE Haiku (encoder/decoder layer) → mcp__burnless__maestro(envelope) → this module
  → MaestroSession (warm base + persistent fork) → response_envelope → back to IDE Haiku

Cache strategy: warm maestro base seeded once; each MCP call resumes the persisted
fork (mcp_fork.json) so context accumulates across calls within a project.
"""
from __future__ import annotations
import functools
import json
import re
from pathlib import Path


def _load_fork(burnless_root: Path, model: str) -> str | None:
    fork_file = burnless_root / "maestro" / "mcp_fork.json"
    if not fork_file.exists():
        return None
    try:
        data = json.loads(fork_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if data.get("model") != model:
        return None
    return data.get("fork_session_id") or None


def _save_fork(burnless_root: Path, model: str, fork_id: str | None) -> None:
    if not fork_id:
        return
    fork_dir = burnless_root / "maestro"
    fork_dir.mkdir(parents=True, exist_ok=True)
    fork_file = fork_dir / "mcp_fork.json"
    fork_file.write_text(
        json.dumps({"model": model, "fork_session_id": fork_id}, ensure_ascii=False),
        encoding="utf-8",
    )


def _try_extract_envelope_json(text: str) -> dict | None:
    """Best-effort extract the final JSON envelope from Maestro output."""
    if not text:
        return None
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1))
        except json.JSONDecodeError:
            pass
    brace_start = text.rfind("{")
    while brace_start != -1:
        try:
            return json.loads(text[brace_start:])
        except json.JSONDecodeError:
            brace_start = text.rfind("{", 0, brace_start)
    return None


def process_envelope(
    envelope: str,
    project_root: Path,
    compression_mode: str = "tight",
    model: str | None = None,
    timeout: int = 180,
) -> dict:
    """Send envelope to persistent Maestro session via engine; return structured result + decoder hint."""
    from . import config as _config, state as _state, paths as _paths
    from .maestro.base import maestro_base_init, maestro_iso_cwd
    from .maestro.session_runner import MaestroSession
    from .maestro.runners import runner_claude_json
    from .maestro import dispatcher
    from . import warm_session

    burnless_root = project_root / ".burnless"

    if model is None:
        try:
            st = _state.load(_paths.paths_for(burnless_root)["state"])
            model = st.get("brain_model") or _config.DEFAULT_TIER_MODELS["bronze"]
        except Exception:
            model = _config.DEFAULT_TIER_MODELS["bronze"]

    try:
        base_uuid = maestro_base_init(burnless_root, model)
    except RuntimeError as exc:
        return {
            "error": "maestro_unavailable",
            "detail": str(exc),
            "decoder_hint": "Tell the user the Maestro is unavailable.",
        }

    try:
        _cfg = _config.load(_paths.paths_for(burnless_root)["config"])
    except Exception:
        _cfg = {}
    from . import epochs as _ep
    _epochs_enabled = _ep.is_enabled(project_root, _cfg)
    _chat_id = f"maestro-{model}"

    session = MaestroSession(
        base_uuid=base_uuid,
        model=model,
        claude_bin=warm_session._claude_binary() or "claude",
        fork_session_id=_load_fork(burnless_root, model),
    )
    runner = functools.partial(
        runner_claude_json,
        timeout=timeout,
        cwd=maestro_iso_cwd(burnless_root, model),
    )

    _reseed = None
    if _epochs_enabled and session.fork_session_id is None:
        try:
            from . import epochs as _ep
            _chain = _ep.active_chain(project_root, _chat_id)
            if _chain:
                _reseed = "\n\n".join(p.read_text(encoding="utf-8") for p in _chain)
        except Exception:
            _reseed = None
    text, _ = session.send(envelope, runner=runner, rewind_capsule=_reseed)

    lines = text.splitlines()
    has_delegates = any(
        dispatcher.DELEGATE_RE.match(l.strip()) or dispatcher.DELEGATE_SHORT_RE.match(l.strip())
        for l in lines
    )
    final_text = text
    if has_delegates:
        try:
            cfg = _config.load(_paths.paths_for(burnless_root)["config"])
        except Exception:
            cfg = {}
        capsules = dispatcher.run_all(
            lines,
            burnless_root=burnless_root,
            project_root=project_root,
            config=cfg,
        )
        if capsules:
            text2, _ = session.send("\n".join(capsules), runner=runner)
            final_text = text2 or text

    if _epochs_enabled:
        try:
            from . import epochs as _ep
            _summ = _ep.epoch_summarizer(project_root)
            _s = _summ(f"PERGUNTA:\n{envelope}\n\nRESPOSTA:\n{final_text}")
            if _s:
                _ep.append_epoch(project_root, _chat_id, _s)
                _rotated = False
                for _lvl in range(0, len(_ep.LEVEL_PREFIXES) - 1):
                    if _ep.needs_consolidation(project_root, _chat_id, _lvl):
                        if _ep.consolidate_level(project_root, _chat_id, _lvl, _summ) and _lvl == 0:
                            _rotated = True
                if _rotated:
                    session.rewind()
                    (burnless_root / "maestro" / "mcp_fork.json").unlink(missing_ok=True)
        except Exception:
            pass

    _save_fork(burnless_root, model, session.fork_session_id)

    response_envelope_json = _try_extract_envelope_json(final_text)

    decoder_hint = (
        "Translate the envelope to natural language for the user. "
        "Be terse. Preserve tone markers. Respect trauma_block if set. "
        "Do not add commentary or filler."
    )
    if compression_mode == "loose":
        decoder_hint += " You may expand with light context where helpful."

    resp_text = json.dumps(response_envelope_json) if response_envelope_json else (final_text or "")
    compression_telemetry = {
        "envelope_chars": len(envelope),
        "response_chars": len(resp_text),
        "maestro_model": model,
        "ratio": round(len(envelope) / max(len(resp_text), 1), 3),
    }

    agg_usage: dict = {
        "input_tokens": sum(int(u.get("input_tokens", 0) or 0) for u in session.usages),
        "output_tokens": sum(int(u.get("output_tokens", 0) or 0) for u in session.usages),
        "cache_creation_input_tokens": sum(int(u.get("cache_creation_input_tokens", 0) or 0) for u in session.usages),
        "cache_read_input_tokens": sum(int(u.get("cache_read_input_tokens", 0) or 0) for u in session.usages),
        "model": model,
    }

    return {
        "response_envelope": response_envelope_json or {"raw_text": final_text},
        "decoder_hint": decoder_hint,
        "compression_mode": compression_mode,
        "maestro_session_id": session.fork_session_id,
        "maestro_exit_code": 0,
        "usage": agg_usage,
        "compression": compression_telemetry,
    }
