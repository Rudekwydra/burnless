from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path


PATH_RE = re.compile(r"(?P<path>(?:~|/|\./|\.\./)[^\s]+)")
LOCAL_INTENT_HINTS = {
    "app",
    "aplicativo",
    "projeto",
    "project",
    "repo",
    "repositorio",
    "repository",
    "codigo",
    "código",
    "pasta",
    "diretorio",
    "diretório",
    "arquivo",
    "memoria",
    "memória",
    "anotacoes",
    "anotações",
}
STOPWORDS = {
    "como",
    "esta",
    "está",
    "esse",
    "essa",
    "isso",
    "para",
    "pelo",
    "pela",
    "pode",
    "poderia",
    "ver",
    "olha",
    "olhar",
    "veja",
    "encontra",
    "encontrar",
    "disco",
    "meus",
    "minhas",
    "meu",
    "minha",
    "das",
    "dos",
    "com",
    "conforme",
    "feito",
    "tudo",
    "se",
    "foi",
    "dá",
    "dar",
    "seus",
    "suas",
    "palpites",
}
SKIP_DIRS = {
    ".git",
    ".burnless",
    ".next",
    ".venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "target",
    ".pytest_cache",
}


@dataclass(frozen=True)
class PlannedObjective:
    original: str
    task: str
    candidates: tuple[Path, ...] = ()
    explicit_paths: tuple[Path, ...] = ()

    @property
    def changed(self) -> bool:
        return self.task != self.original


def plan_objective(
    objective: str,
    *,
    project_root: Path,
    max_candidates: int = 5,
) -> PlannedObjective:
    """Turn conversational shell input into an operational Worker task."""
    original = " ".join((objective or "").split())
    if not original:
        return PlannedObjective(original="", task="")

    explicit_paths = _extract_paths(original)
    candidates: tuple[Path, ...] = ()
    if not explicit_paths and _looks_local(original):
        candidates = tuple(_find_candidates(original, project_root=project_root, limit=max_candidates))

    if not explicit_paths and not candidates and not _looks_local(original):
        return PlannedObjective(original=original, task=original)

    lines = [
        original,
        "",
        "## Natural Language Preflight",
        "",
        "The user wrote a conversational request. Resolve it operationally before returning BLK.",
    ]
    if explicit_paths:
        lines.append("")
        lines.append("Explicit path(s) from the request:")
        for path in explicit_paths:
            exists = "exists" if path.exists() else "not found"
            lines.append(f"- {path} ({exists})")
        lines.append("Inspect explicit paths first.")
    elif candidates:
        lines.append("")
        lines.append("Local candidate project/repository paths found before delegation:")
        for path in candidates:
            lines.append(f"- {path}")
        lines.append("Start with the strongest matching candidate. If multiple candidates fit, inspect enough to choose and mention the choice.")
    else:
        lines.append("")
        lines.append(
            "No path was provided. Search likely local project roots before blocking: "
            "~/antigravity, ~/projects, ~/Projects, and the current working tree."
        )

    if _mentions_memory(original):
        lines.append("")
        lines.append(
            "The request mentions memory/notes. Look for project notes in the target repo and common AI memory folders "
            "(~/.claude/projects, ~/.claude/memory, ~/.codex, ~/.config/claude) when available."
        )

    lines.append("")
    lines.append("Expected output: concrete status, issues, what appears done/not done, and next actions.")
    return PlannedObjective(
        original=original,
        task="\n".join(lines),
        candidates=candidates,
        explicit_paths=tuple(explicit_paths),
    )


def _extract_paths(text: str) -> list[Path]:
    paths: list[Path] = []
    for match in PATH_RE.finditer(text):
        raw = match.group("path").rstrip(".,;:)")
        paths.append(Path(raw).expanduser())
    return paths


def _looks_local(text: str) -> bool:
    haystack = _normalize(text)
    return any(_normalize(hint) in haystack for hint in LOCAL_INTENT_HINTS)


def _mentions_memory(text: str) -> bool:
    haystack = _normalize(text)
    return any(term in haystack for term in ("memoria", "anotacoes", "notes", "memory"))


def _find_candidates(text: str, *, project_root: Path, limit: int) -> list[Path]:
    terms = _query_terms(text)
    if not terms:
        return []
    scored: list[tuple[int, str, Path]] = []
    seen: set[Path] = set()
    for root in _search_roots(project_root):
        for path in _walk_dirs(root, max_depth=3):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            score = _score_path(path, terms)
            if score > 0:
                scored.append((score, str(path), path))
    scored.sort(key=lambda item: (-item[0], len(item[1]), item[1]))
    return [path for _score, _name, path in scored[:limit]]


def _search_roots(project_root: Path) -> list[Path]:
    roots = [
        project_root,
        Path.home() / "antigravity",
        Path.home() / "projects",
        Path.home() / "Projects",
    ]
    out: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        try:
            resolved = root.expanduser().resolve()
        except OSError:
            continue
        if resolved in seen or not resolved.exists() or not resolved.is_dir():
            continue
        seen.add(resolved)
        out.append(resolved)
    return out


def _walk_dirs(root: Path, *, max_depth: int) -> list[Path]:
    found: list[Path] = []
    stack: list[tuple[Path, int]] = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        if depth > 0:
            found.append(current)
        if depth >= max_depth:
            continue
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in reversed(entries):
            if not entry.is_dir(follow_symlinks=False):
                continue
            if entry.name in SKIP_DIRS or entry.name.startswith("."):
                continue
            stack.append((Path(entry.path), depth + 1))
    return found


def _score_path(path: Path, terms: set[str]) -> int:
    normalized = _normalize(str(path))
    name = _normalize(path.name)
    score = 0
    for term in terms:
        if term in name:
            score += 4
        elif term in normalized:
            score += 2
        else:
            for part in re.findall(r"[a-z0-9]+", normalized):
                if term.startswith(part) or part.startswith(term):
                    score += 1
                    break
    if score <= 0:
        return 0
    if (path / ".git").is_dir():
        score += 3
    if any((path / marker).exists() for marker in ("package.json", "pyproject.toml", "Cargo.toml", "go.mod")):
        score += 2
    return score


def _query_terms(text: str) -> set[str]:
    normalized = _normalize(text)
    terms: set[str] = set()
    for token in re.findall(r"[a-z0-9]+", normalized):
        if len(token) < 3:
            continue
        if token in {_normalize(word) for word in STOPWORDS | LOCAL_INTENT_HINTS}:
            continue
        terms.add(token)
    return terms


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    ascii_text = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9_/.-]+", " ", ascii_text).strip()
