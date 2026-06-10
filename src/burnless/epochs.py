from __future__ import annotations

import re
import shutil
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
