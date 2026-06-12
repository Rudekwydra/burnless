from __future__ import annotations

import json
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

LEVEL_PREFIXES = ["", "a", "b", "c", "d", "e", "f", "g", "h"]
SLOT_RE = re.compile(r"^([a-h]?)(\d{2,3})\.md$")


def epoch_dir(root: Path, chat_id: str) -> Path:
    return root / ".burnless" / "epochs" / chat_id


def _slot_name(level: int, n: int) -> str:
    if level == 0:
        return f"{n:03d}.md"
    else:
        return f"{LEVEL_PREFIXES[level]}{n:02d}.md"


def _level_files(d: Path, level: int) -> list[Path]:
    if not d.exists():
        return []

    prefix = LEVEL_PREFIXES[level]
    matching = []

    for f in d.iterdir():
        if f.is_file():
            m = SLOT_RE.match(f.name)
            if m and m.group(1) == prefix:
                num = int(m.group(2))
                matching.append((num, f))

    matching.sort(key=lambda x: x[0])
    return [f for _, f in matching]


def append_epoch(root: Path, chat_id: str, summary_md: str) -> Path:
    d = epoch_dir(root, chat_id)
    d.mkdir(parents=True, exist_ok=True)

    level0_files = _level_files(d, 0)
    next_n = len(level0_files) + 1

    slot = _slot_name(0, next_n)
    path = d / slot
    path.write_text(summary_md, encoding='utf-8')

    return path


def needs_consolidation(root: Path, chat_id: str, level: int) -> bool:
    d = epoch_dir(root, chat_id)
    if not d.exists():
        return False

    level_files = _level_files(d, level)
    return len(level_files) == 10 and level + 1 < len(LEVEL_PREFIXES)


def consolidate_level(root: Path, chat_id: str, level: int, summarizer) -> Path | None:
    d = epoch_dir(root, chat_id)

    if not needs_consolidation(root, chat_id, level):
        return None

    level_files = _level_files(d, level)

    texts = [f.read_text(encoding='utf-8') for f in level_files]
    concat = "\n\n---\n\n".join(texts)

    consolidated = summarizer(concat)

    if consolidated is None or (isinstance(consolidated, str) and not consolidated.strip()):
        return None

    next_level = level + 1
    next_level_files = _level_files(d, next_level)
    next_n = len(next_level_files) + 1

    slot = _slot_name(next_level, next_n)
    path = d / slot
    path.write_text(consolidated, encoding='utf-8')

    originais_dir = d / "originais"
    originais_dir.mkdir(parents=True, exist_ok=True)

    for f in level_files:
        f.rename(originais_dir / f.name)

    return path


def active_chain(root: Path, chat_id: str) -> list[Path]:
    d = epoch_dir(root, chat_id)

    if not d.exists():
        return []

    active = []
    for level in range(len(LEVEL_PREFIXES)):
        level_files = _level_files(d, level)
        active.extend(level_files)

    def sort_key(p):
        m = SLOT_RE.match(p.name)
        prefix = m.group(1)
        num = int(m.group(2))

        if prefix == "":
            level = 0
        else:
            level = LEVEL_PREFIXES.index(prefix)

        return (-level, num)

    active.sort(key=sort_key)
    return active


def cleanup_originais(root: Path, chat_id: str) -> int:
    d = epoch_dir(root, chat_id)
    originais_dir = d / "originais"

    if not originais_dir.exists():
        return 0

    count = sum(1 for f in originais_dir.rglob('*') if f.is_file())

    shutil.rmtree(originais_dir)

    return count


def epoch_summarizer(project_root: Path):
    """Returns Callable[[str], str|None] that summarizes text into a dense epoch summary via the configured
    encoder (gemma/anthropic), reusing telegram_compact's transport. Fail-open: returns None on any error."""
    def _summarize(text: str) -> str | None:
        try:
            from . import config, paths
            try:
                cfg = config.load(paths.paths_for(project_root)["config"])
            except Exception:
                cfg = {}
            enc = cfg.get("encoder") or {}
            provider = (enc.get("provider") or "anthropic").strip()
            model = enc.get("model") or config.DEFAULT_TIER_MODELS["bronze"]
        except Exception:
            return None

        if provider == "passthrough" or model == "passthrough":
            return None

        prompt = (
            "Resuma o trecho de conversa abaixo num resumo DENSO em português: o que foi PEDIDO, "
            "o que foi FEITO/DECIDIDO, refs (paths/IDs/commits), e pendências. Sem pensamento/debate. "
            "Markdown curto.\n\n" + text
        )

        try:
            if provider == "ollama-local":
                data = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode()
                req = urllib.request.Request(
                    "http://localhost:11434/api/generate",
                    data=data,
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=8) as resp:
                    body = json.loads(resp.read())
                out = body["response"]
                from .compression import _strip_gemma_channels
                out = _strip_gemma_channels(out)
            else:
                try:
                    from .warm_session import _claude_binary
                    claude_bin = _claude_binary() or "claude"
                except Exception:
                    claude_bin = "claude"
                result = subprocess.run(
                    [claude_bin, "-p", "--model", model, "--permission-mode", "bypassPermissions",
                     "--allowedTools", "", "--output-format", "json"],
                    input=prompt,
                    capture_output=True,
                    text=True,
                    timeout=8,
                )
                data = json.loads(result.stdout)
                out = data["result"]

            out = out.strip()
            if out.startswith("```"):
                lines = out.split("\n")
                out = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])

            return out if out else None
        except Exception:
            return None

    return _summarize


def _ollama(model: str, prompt: str, *, timeout: int = 30, host: str = "http://localhost:11434") -> str | None:
    """Call ollama /api/generate. Returns stripped response text or None on any error."""
    try:
        data = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode()
        req = urllib.request.Request(
            f"{host}/api/generate",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read())
        out = body.get("response", "")
        from .compression import _strip_gemma_channels
        return _strip_gemma_channels(out).strip() or None
    except Exception:
        return None


def _enabled_marker(project_root) -> Path:
    return Path(project_root) / ".burnless" / "epochs.on"


def is_enabled(project_root, cfg=None) -> bool:
    "True if config epochs.enabled OR the marker file exists. Fail-open False."
    try:
        if cfg and bool((cfg.get("epochs") or {}).get("enabled", False)):
            return True
        return _enabled_marker(project_root).exists()
    except Exception:
        return False


def set_enabled(project_root, on: bool) -> bool:
    "Create/remove the marker. Returns the new state. Fail-open."
    m = _enabled_marker(project_root)
    try:
        if on:
            m.parent.mkdir(parents=True, exist_ok=True); m.write_text("on", encoding="utf-8")
        elif m.exists():
            m.unlink()
    except Exception:
        pass
    return is_enabled(project_root)
