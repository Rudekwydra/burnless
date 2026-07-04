from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

LEVEL_PREFIXES = ["", "a", "b", "c", "d", "e", "f", "g", "h"]
SLOT_RE = re.compile(r"^([a-h]?)(\d{2,3})\.md$")
_CHAT_ID_UNSAFE_RE = re.compile(r"[^A-Za-z0-9_-]")


def epoch_dir(root: Path, chat_id: str) -> Path:
    safe_chat_id = _CHAT_ID_UNSAFE_RE.sub("_", chat_id)
    return root / ".burnless" / "epochs" / safe_chat_id


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
    next_n = (int(level0_files[-1].stem) if level0_files else 0) + 1

    slot = _slot_name(0, next_n)
    path = d / slot
    tmp = tempfile.NamedTemporaryFile(mode='w', dir=d, delete=False, encoding='utf-8')
    try:
        tmp.write(summary_md)
        tmp.close()
        os.replace(tmp.name, path)
    except Exception:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass
        raise

    return path


def needs_consolidation(root: Path, chat_id: str, level: int) -> bool:
    d = epoch_dir(root, chat_id)
    if not d.exists():
        return False

    level_files = _level_files(d, level)
    return len(level_files) >= 10 and level + 1 < len(LEVEL_PREFIXES)


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
    next_n = (int(next_level_files[-1].stem[1:]) if next_level_files else 0) + 1

    slot = _slot_name(next_level, next_n)
    path = d / slot
    tmp = tempfile.NamedTemporaryFile(mode='w', dir=d, delete=False, encoding='utf-8')
    try:
        tmp.write(consolidated)
        tmp.close()
        os.replace(tmp.name, path)
    except Exception:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass
        raise

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


def _effective_epochs_version(root) -> int:
    try:
        from . import epochs_v2

        return int(epochs_v2._epochs_version(root))
    except Exception:
        return 2


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
            if current == Path.home():
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

        _SAFE_PROJ_NAME_RE = re.compile(r'^[A-Za-z0-9_.-]+$')
        for line in text.split('\n'):
            try:
                if workspace.as_posix() in line:
                    parts = line.split(workspace.as_posix() + '/')
                    if len(parts) > 1:
                        remainder = parts[1]
                        proj_name = remainder.split('/')[0] if '/' in remainder else remainder.split()[0]
                        if proj_name and not proj_name.startswith('.') and _SAFE_PROJ_NAME_RE.match(proj_name):
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


def _commits_since_mtime(root, mtime: float, cap: int = 15) -> str:
    """Reconciliation aid: commits that landed after the living-doc was frozen.
    Closes the 'trailing by 1 commit' gap — a git commit is not a captured
    conversational exchange, so the living-doc never learns the work landed.
    Deterministic, zero-LLM, fail-open (git error / non-repo -> empty string)."""
    try:
        import subprocess
        import datetime
        since = datetime.datetime.fromtimestamp(mtime).isoformat()
        out = subprocess.run(
            ["git", "-C", str(root), "log", "--since=" + since,
             "--pretty=format:%h %s", "-" + str(cap)],
            capture_output=True, text=True, timeout=5,
        )
        lines = [l for l in out.stdout.splitlines() if l.strip()]
        if not lines:
            return ""
        body = "\n".join("- " + l for l in lines)
        return ("\n## Commits apos o checkpoint (reconciliar vs Threads abertas)\n"
                + body + "\n")
    except Exception:
        return ""


def _semantic_recon(root, base_body, recon_text):
    """Fold post-checkpoint commits into the freshest living-doc via the encoder
    model so 'Foco atual'/'Decisões' reflect what actually landed. Returns the
    updated markdown, or None on any failure / when no model is configured
    (fail-open: caller falls back to the raw commit append)."""
    try:
        from . import epochs_v2
        rewrite = epochs_v2.living_rewriter(root)
        exchange = (
            "## Commits recem-aterrissados (fonte de verdade do que JA foi concluido)\n"
            + recon_text
            + "\n\nInstrucao: atualize 'Foco atual' e 'Decisoes' para refletir que "
            "estes commits JA foram feitos/commitados. Nao invente nada alem do que "
            "os assuntos dos commits indicam."
        )
        prompt = epochs_v2.living_rewrite_prompt(base_body, exchange)
        folded = rewrite(prompt)
        folded = (folded or "").strip()
        return folded or None
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

        CARRY_FORWARD_CAP = 8000

        # V2 living-doc: serve predecessor chats' living.md (newest-first) before
        # the V1 NNN.md chain. Without this, capture writes V2 but resume serves
        # V1 — the dense living-doc is written and never read (commit 5492569).
        if _effective_epochs_version(root) >= 3:
            from . import epochs_v2, owner_cache, owner_loop
            v2_cand = []
            for chat_dir in epochs_dir.iterdir():
                if not chat_dir.is_dir():
                    continue
                if chat_dir.name == "_rolling" or chat_dir.name == current_chat_id:
                    continue
                lp = epochs_v2.living_path(root, chat_dir.name)
                if lp.exists() and lp.read_text(encoding='utf-8').strip():
                    v2_cand.append((lp.stat().st_mtime, chat_dir.name, lp))
            if v2_cand:
                v2_cand.sort(key=lambda c: c[0], reverse=True)
                # Cache read (step 3b): serve refined seed if fingerprint matches, else floor
                try:
                    predecessors = [(name, lp.read_text(encoding='utf-8')) for _, name, lp in v2_cand]
                    owner_model_for_fp = ""
                    try:
                        from . import config as _cfg, paths as _paths
                        _c = _cfg.load(_paths.paths_for(root / ".burnless")["config"])
                        _enc = _c.get("encoder") or {}
                        owner_model_for_fp = (_enc.get("model") or "").strip()
                    except Exception:
                        owner_model_for_fp = ""
                    fp = owner_cache.compute_base_fingerprint(predecessors, owner_model=owner_model_for_fp, prompt_version="v3")
                    cache_path = str(epochs_dir / "_rolling" / "refined_seed.json")
                    cached = owner_cache.read_valid_refined_seed(cache_path, fp)
                    if cached:
                        try:
                            owner_loop.log_owner_event(root, {"phase": "carry_forward", "served": "refined", "cache_hit": True, "fingerprint": fp})
                        except Exception:
                            pass
                        return cached
                    try:
                        owner_loop.log_owner_event(root, {"phase": "carry_forward", "served": "floor", "cache_hit": False, "fingerprint": fp})
                    except Exception:
                        pass
                except Exception:
                    pass  # Fail-closed: continue to deterministic floor
                # Slot-merge into ONE consolidated living-doc instead of stacking
                # N whole docs newest-first. The old stack buried the live thread
                # 4th in the pile and made the new session inherit the freshest
                # checkpoint's indecision (each doc carried its own `## Foco
                # atual`, `## Decisões`, ...). We keep the SAME chains/ranking/cap
                # (anti-orphan merge of all recent chains stays) and only change
                # the OUTPUT FORMAT: parse each predecessor living-doc into V3
                # slots and fuse PER SLOT — one `## Foco atual`, one `## Decisões`,
                # ... with newest-chain entries first inside each slot, exact-line
                # deduped. Mechanical/deterministic, zero-LLM (10s hook path).
                merged = {section: [] for section in epochs_v2.SECTIONS_V3}
                extra_order = []
                seen_lines = {}
                seen_docs = set()
                source_names = []  # provenance: predecessor chat-ids actually merged, newest-first
                for _, name, lp in v2_cand:
                    body = lp.read_text(encoding='utf-8').strip()
                    if not body or body in seen_docs:
                        continue
                    seen_docs.add(body)
                    source_names.append(name)
                    parsed = epochs_v2.parse_living_v3(body)
                    for section, entries in parsed.items():
                        if section not in merged:
                            merged[section] = []
                            extra_order.append(section)
                        bucket = seen_lines.setdefault(section, set())
                        for entry in entries:
                            key = entry.strip()
                            if not key or key in bucket:
                                continue
                            # DROP placeholder entries (d000: <contrato>, d001: [contrato])
                            if re.match(r"^d\d+\s*:\s*[<\[]", key):
                                continue
                            # CAP Foco atual: keep only the 3 most recent (newest-first)
                            if section == "Foco atual" and len(merged[section]) >= 3:
                                continue
                            bucket.add(key)
                            merged[section].append(entry)

                def _render(slots):
                    # Local rebuild that SUPPRESSES empty sections (defeito 5):
                    # an empty `## Riscos` wastes budget and signals empty-schema
                    # as state. Local only — never mutate epochs_v2._rebuild_md_v3
                    # (other callers rely on the always-8-headers contract).
                    lines = []
                    for section in list(epochs_v2.SECTIONS_V3) + list(extra_order):
                        entries = slots.get(section, [])
                        if not entries:
                            continue
                        lines.append(f'## {section}')
                        for body_line in entries:
                            lines.append(body_line if body_line.startswith('- ') else f'- {body_line}')
                        lines.append('')
                    return '\n'.join(lines)

                # Cap newest-first per slot-entry: trim OLDEST entries (slot tail)
                # from least-critical sections first, preserving the clear-resume
                # top-truncation guarantee. Align with enforce_budget_v3
                # (epochs_v2.py): trim ONLY Decisões, Refs, Riscos (oldest first),
                # then extra sections. NEVER trim Foco atual / Threads abertas /
                # Contracts / Última validação / Recuperáveis (defeito 2). Entries
                # are appended newest-chain-first, so pop() removes the oldest.
                trim_order = ['Decisões', 'Refs', 'Riscos'] + list(extra_order)
                while len(_render(merged)) > CARRY_FORWARD_CAP:
                    removed = False
                    for sec in trim_order:
                        if merged.get(sec):
                            merged[sec].pop()
                            removed = True
                            break
                    if not removed:
                        break

                consolidated = _render(merged)

                # Provenance footer (defeito 3): keep a pointer to the recoverable
                # raw predecessor docs without re-stacking them. One compact line
                # of chat-ids, newest-first.
                sources_block = ""
                if source_names:
                    src_ids = ", ".join(n[:8] for n in source_names)
                    sources_block = f"\n## Sources\n- {src_ids}\n"

                header = "> ordem: documento vivo (living-doc v2) consolidado por slot — entradas mais NOVAS primeiro em cada secao\n\n"

                # Reconcile post-checkpoint commits.
                recon = _commits_since_mtime(root, v2_cand[0][0])
                core = consolidated
                recon_block = ""
                if recon:
                    mode = "raw"
                    try:
                        from . import config as _cfg, paths as _paths
                        _c = _cfg.load(_paths.paths_for(root / ".burnless")["config"])
                        mode = str((_c.get("epoch") or {}).get("resume_recon") or "raw").strip().lower()
                    except Exception:
                        mode = "raw"
                    folded = None
                    if mode == "semantic":
                        # Feed recon the CONSOLIDATED doc, not v2_cand[0] raw
                        # (defeito 1): otherwise the multi-chain consolidation is
                        # discarded and the anti-orphan merge is defeated.
                        folded = _semantic_recon(root, consolidated, recon)
                    if folded:
                        core = f"# living:reconciled\n{folded}"
                    else:
                        recon_block = recon

                # Apply CARRY_FORWARD_CAP to the FINAL payload (defeito 4): the
                # consolidated doc + provenance has priority and is never trimmed
                # here; only the appended recon block absorbs the overflow.
                fixed = header + core + "\n\n" + sources_block
                if recon_block:
                    budget_left = CARRY_FORWARD_CAP - len(fixed)
                    if budget_left <= 0:
                        recon_block = ""
                    elif len(recon_block) > budget_left:
                        recon_block = recon_block[:budget_left]
                return fixed + recon_block
            # no living docs yet -> fall through to V1 chain (backward compat)

        # Merge ALL recent predecessor chains, newest-first, deduped, capped.
        # Single-newest-dir selection orphaned a deep working chain whenever a
        # thin throwaway session (1 epoch) happened to have a newer dir mtime —
        # the freshest checkpoint silently lost to noise. Ranking each chain by
        # its freshest epoch and merging keeps every recent chain alive; the cap
        # (newest-first) preserves the clear-resume top-truncation guarantee, and
        # dedup drops the seed.md / repeated-summary echoes.
        candidates = []
        for chat_dir in epochs_dir.iterdir():
            if not chat_dir.is_dir():
                continue
            if chat_dir.name == "_rolling" or chat_dir.name == current_chat_id:
                continue

            chain = active_chain(root, chat_dir.name)
            if not chain:
                continue

            last_mtime = max(f.stat().st_mtime for f in chain)
            candidates.append((last_mtime, chain))

        if candidates:
            candidates.sort(key=lambda c: c[0], reverse=True)
            result = ["> ordem: mais NOVO primeiro (topo = ultimo checkpoint vivo)\n\n"]
            seen = set()
            total = 0
            for _, chain in candidates:
                for f in reversed(chain):
                    body = f.read_text(encoding='utf-8')
                    key = body.strip()
                    if key in seen:
                        continue
                    seen.add(key)
                    block = f"# {f.name}\n{body}\n"
                    if total > 0 and total + len(block) > CARRY_FORWARD_CAP:
                        return "".join(result)
                    result.append(block)
                    total += len(block)
            return "".join(result)

        seed_file = epochs_dir / "_rolling" / "seed.md"
        if seed_file.exists():
            content = seed_file.read_text(encoding='utf-8')
            if content.strip():
                return content

        return ""
    except Exception:
        return ""


def build_refine_owner_candidates(root, current_chat_id=None) -> tuple[list[tuple[str, str]], str] | None:
    """Build predecessors list and deterministic floor for owner-loop refine_seed.

    Returns:
        (predecessors, floor_md) tuple where:
        - predecessors: list of (chat_id, living_doc_text) tuples, newest-first
        - floor_md: consolidated living-doc markdown (deterministic floor)
    None if the project is not on epochs version 3 or no V3 predecessors exist.
    """
    if _effective_epochs_version(root) < 3:
        return None

    try:
        root = Path(root) if isinstance(root, str) else root

        if not is_enabled(root):
            return None

        epochs_dir = root / ".burnless" / "epochs"
        if not epochs_dir.exists():
            return None

        from . import epochs_v2

        v2_cand = []
        for chat_dir in epochs_dir.iterdir():
            if not chat_dir.is_dir():
                continue
            if chat_dir.name == "_rolling":
                continue
            lp = epochs_v2.living_path(root, chat_dir.name)
            if lp.exists() and lp.read_text(encoding='utf-8').strip():
                v2_cand.append((lp.stat().st_mtime, chat_dir.name, lp))

        if not v2_cand:
            return None

        v2_cand.sort(key=lambda c: c[0], reverse=True)
        predecessors = [(name, lp.read_text(encoding='utf-8')) for _, name, lp in v2_cand]

        merged = {section: [] for section in epochs_v2.SECTIONS_V3}
        extra_order = []
        seen_lines = {}
        seen_docs = set()

        for _, name, lp in v2_cand:
            body = lp.read_text(encoding='utf-8').strip()
            if not body or body in seen_docs:
                continue
            seen_docs.add(body)
            parsed = epochs_v2.parse_living_v3(body)
            for section, entries in parsed.items():
                if section not in merged:
                    merged[section] = []
                    extra_order.append(section)
                bucket = seen_lines.setdefault(section, set())
                for entry in entries:
                    key = entry.strip()
                    if not key or key in bucket:
                        continue
                    # DROP placeholder entries (d000: <contrato>, d001: [contrato])
                    if re.match(r"^d\d+\s*:\s*[<\[]", key):
                        continue
                    # CAP Foco atual: keep only the 3 most recent (newest-first)
                    if section == "Foco atual" and len(merged[section]) >= 3:
                        continue
                    bucket.add(key)
                    merged[section].append(entry)

        def _render(slots):
            lines = []
            for section in list(epochs_v2.SECTIONS_V3) + list(extra_order):
                entries = slots.get(section, [])
                if not entries:
                    continue
                lines.append(f'## {section}')
                for body_line in entries:
                    lines.append(body_line if body_line.startswith('- ') else f'- {body_line}')
                lines.append('')
            return '\n'.join(lines)

        CARRY_FORWARD_CAP = 8000
        trim_order = ['Decisões', 'Refs', 'Riscos'] + list(extra_order)
        while len(_render(merged)) > CARRY_FORWARD_CAP:
            removed = False
            for sec in trim_order:
                if merged.get(sec):
                    merged[sec].pop()
                    removed = True
                    break
            if not removed:
                break

        floor_md = _render(merged)
        return (predecessors, floor_md)

    except Exception:
        return None
