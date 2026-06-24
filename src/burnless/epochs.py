from __future__ import annotations

import json
import os
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
                cfg = config.load(paths.paths_for(Path(project_root) / ".burnless")["config"])
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
            "o que foi FEITO/DECIDIDO, refs (paths/IDs/commits), e pendências. "
            "Se o trecho tocou/definiu código, acrescente no fim uma seção iniciada por ## Contracts "
            "listando as assinaturas/shapes-chave no formato arquivo:linha (tipos, exports, schemas, contratos de função) "
            "— só o essencial pra retomar cirúrgico sem reler o arquivo inteiro. Sem pensamento/debate. "
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
                with urllib.request.urlopen(req, timeout=20) as resp:
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
                    timeout=60,
                    env={**os.environ, "BURNLESS_NO_EPOCH": "1"},
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


def _disabled_marker(project_root) -> Path:
    return Path(project_root) / ".burnless" / "epochs.off"


def is_enabled(project_root, cfg=None) -> bool:
    "Opt-out: ON by default. OFF only if .off marker exists or cfg epochs.enabled is explicitly False. Fail-open True."
    try:
        if cfg is not None and (cfg.get("epochs") or {}).get("enabled", None) is False:
            return False
        return not _disabled_marker(project_root).exists()
    except Exception:
        return True


def set_enabled(project_root, on: bool) -> bool:
    "Opt-out marker. on=True removes the .off marker; on=False creates it. Fail-open."
    m = _disabled_marker(project_root)
    try:
        if on:
            if m.exists():
                m.unlink()
        else:
            m.parent.mkdir(parents=True, exist_ok=True); m.write_text("off", encoding="utf-8")
    except Exception:
        pass
    return is_enabled(project_root)


def resolve_root(cwd, workspace=None, transcript=None) -> Path | None:
    """Resolve project root from cwd using canonical logic.

    Returns (first match):
    (a) Closest ancestor of cwd with .burnless/config.yaml (excluding workspace itself)
    (b) workspace/<first_component> if cwd is strictly inside workspace
    (c) _detect_from_transcript or freshest_project_root if cwd == workspace or cwd == home
    (d) cwd itself
    Returns None on any error.
    """
    try:
        cwd = Path(cwd) if isinstance(cwd, str) else cwd
        workspace = Path(workspace) if isinstance(workspace, str) else workspace

        current = cwd
        while current != Path(current.root):
            if workspace is not None and current.parent == workspace:
                break
            config_file = current / ".burnless" / "config.yaml"
            if config_file.exists():
                return current
            current = current.parent

        if workspace is not None and cwd != workspace:
            try:
                cwd_rel = cwd.relative_to(workspace)
                first_component = cwd_rel.parts[0] if cwd_rel.parts else None
                if first_component:
                    return workspace / first_component
            except ValueError:
                pass

        if cwd == workspace or cwd == Path.home():
            if transcript is not None:
                detected = _detect_from_transcript(transcript, workspace)
                if detected:
                    return detected
            return freshest_project_root(workspace)

        return cwd
    except Exception:
        return None


def freshest_project_root(workspace) -> Path | None:
    """Find project dir with newest .burnless/epochs/_rolling/seed.md.

    Scans only one level under workspace; excludes workspace itself.
    Returns None if no seed files found.
    """
    try:
        if workspace is None:
            return None
        workspace = Path(workspace) if isinstance(workspace, str) else workspace
        if not workspace.exists():
            return None

        freshest = None
        freshest_mtime = 0

        for proj_dir in workspace.iterdir():
            if not proj_dir.is_dir():
                continue
            seed_file = proj_dir / ".burnless" / "epochs" / "_rolling" / "seed.md"
            if seed_file.exists():
                mtime = seed_file.stat().st_mtime
                if mtime > freshest_mtime:
                    freshest_mtime = mtime
                    freshest = proj_dir

        return freshest
    except Exception:
        return None


def _detect_from_transcript(transcript, workspace) -> Path | None:
    """Detect dominant project from transcript file references.

    Counts references matching <workspace>/<proj>/<file>.
    Returns <workspace>/<proj> if most common proj has ≥5 hits.
    Returns None otherwise.
    """
    try:
        if transcript is None or workspace is None:
            return None

        transcript_path = Path(transcript) if isinstance(transcript, str) else transcript
        workspace = Path(workspace) if isinstance(workspace, str) else workspace

        if not transcript_path.exists():
            return None

        proj_counts = {}
        text = transcript_path.read_text(encoding='utf-8', errors='ignore')

        for line in text.split('\n'):
            try:
                if workspace.as_posix() in line:
                    parts = line.split(workspace.as_posix() + '/')
                    if len(parts) > 1:
                        remainder = parts[1]
                        proj_name = remainder.split('/')[0] if '/' in remainder else remainder.split()[0]
                        if proj_name and not proj_name.startswith('.'):
                            proj_counts[proj_name] = proj_counts.get(proj_name, 0) + 1
            except Exception:
                continue

        if not proj_counts:
            return None

        most_common_proj = max(proj_counts, key=proj_counts.get)
        if proj_counts[most_common_proj] >= 5:
            return workspace / most_common_proj

        return None
    except Exception:
        return None


def carry_forward_chain(root, current_chat_id=None) -> str:
    """Render carry-forward memory chain from predecessor chats or rolling seed.

    Returns (first match):
    (a) Active chain from newest predecessor chat (not _rolling, not current_chat_id)
    (b) Content of _rolling/seed.md if exists
    (c) Empty string
    """
    try:
        root = Path(root) if isinstance(root, str) else root

        if not is_enabled(root):
            return ""

        epochs_dir = root / ".burnless" / "epochs"
        if not epochs_dir.exists():
            return ""

        newest_chat = None
        newest_mtime = 0

        for chat_dir in epochs_dir.iterdir():
            if not chat_dir.is_dir():
                continue
            if chat_dir.name == "_rolling" or chat_dir.name == current_chat_id:
                continue

            chain = active_chain(root, chat_dir.name)
            if not chain:
                continue

            mtime = chat_dir.stat().st_mtime
            if mtime > newest_mtime:
                newest_mtime = mtime
                newest_chat = chat_dir.name

        if newest_chat is not None:
            chain = active_chain(root, newest_chat)
            if chain:
                # Newest-first: clear-resume trunca o seed injetado pelo topo,
                # entao o checkpoint vivo (ultimo epoch) tem que liderar.
                # Ler de cima pra baixo = mais novo -> mais velho.
                result = ["> ordem: mais NOVO primeiro (topo = ultimo checkpoint vivo)\n\n"]
                for f in reversed(chain):
                    result.append(f"# {f.name}\n")
                    result.append(f.read_text(encoding='utf-8'))
                    result.append("\n")
                return "".join(result)

        seed_file = epochs_dir / "_rolling" / "seed.md"
        if seed_file.exists():
            content = seed_file.read_text(encoding='utf-8')
            if content.strip():
                return content

        return ""
    except Exception:
        return ""
