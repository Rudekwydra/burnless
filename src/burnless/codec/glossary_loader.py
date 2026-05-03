from __future__ import annotations

import logging
from pathlib import Path

from .. import paths as paths_mod

log = logging.getLogger(__name__)

_CACHE_TEXT: str | None = None
_CACHE_SIG: tuple[tuple[str, int | None, int | None], ...] | None = None


def load_glossary(project_root: Path | None = None) -> str:
    """Load the stable Maestro glossary.

    Tenant glossaries are detected for Day 1 but intentionally not merged yet,
    so the returned core remains byte-identical and cache-friendly.
    """
    global _CACHE_TEXT, _CACHE_SIG

    repo_root = _repo_root(project_root)
    core_path = repo_root / "_design" / "maestro_v1" / "glossary.md"
    candidates = [
        core_path,
        Path.home() / ".burnless" / "tenant_glossary.yaml",
    ]
    burnless_root = paths_mod.find_root(repo_root)
    if burnless_root is not None:
        candidates.append(burnless_root / "tenant_glossary.yaml")

    sig = tuple((str(p), _mtime_ns(p), _size(p)) for p in candidates)
    if _CACHE_TEXT is not None and sig == _CACHE_SIG:
        return _CACHE_TEXT

    text = core_path.read_bytes().decode("utf-8")
    for tenant_path in candidates[1:]:
        if tenant_path.exists():
            log.info("found tenant glossary: %s", tenant_path)

    _CACHE_TEXT = text
    _CACHE_SIG = sig
    return text


def _repo_root(project_root: Path | None = None) -> Path:
    if project_root is not None:
        root = project_root
    else:
        root = Path(__file__).resolve()
        for parent in root.parents:
            if (parent / "_design" / "maestro_v1" / "glossary.md").exists():
                return parent
        root = Path.cwd()
    root = root.resolve()
    if root.name == ".burnless":
        return root.parent
    if (root / ".burnless").exists() or (root / "_design").exists():
        return root
    return root.parent


def _mtime_ns(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def _size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None
