from __future__ import annotations
import json
import subprocess
from pathlib import Path


def _strip_quotes(s: str) -> str:
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1]
    return s


def snapshot_tree(cwd) -> dict:
    """Non-destructive read-only snapshot of the git working tree at cwd.

    Returns {"head": <HEAD sha or "">, "porcelain": {path: status_code}} where
    porcelain maps each changed/untracked path to its 2-char `git status
    --porcelain` XY code. Fail-open: returns {"head": "", "porcelain": {}} on
    any error (not a git repo, git missing, timeout).
    """
    try:
        head = ""
        try:
            r = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=5,
            )
            if r.returncode == 0:
                head = r.stdout.strip()
        except Exception:
            head = ""

        porcelain = {}
        r = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if r.returncode != 0:
            return {"head": "", "porcelain": {}}
        for line in r.stdout.splitlines():
            if not line:
                continue
            code = line[:2]
            path = _strip_quotes(line[3:].strip())
            if path:
                porcelain[path] = code
        return {"head": head, "porcelain": porcelain}
    except Exception:
        return {"head": "", "porcelain": {}}


def diff_snapshots(before: dict, after: dict, cwd) -> dict:
    """Derive what changed between two snapshot_tree() results.

    Returns {"files_changed": sorted[str], "added": [...], "modified": [...],
    "deleted": [...], "diff_stats": {"files": int, "insertions": int, "deletions": int}}.
    files_changed = paths whose porcelain status appears (or differs) in `after`
    but not identically in `before`. Classify by after-status first char:
    '?' or 'A' -> added; 'D' -> deleted; else -> modified. diff_stats is summed
    from `git diff --numstat HEAD` (timeout=10) restricted to files_changed
    (binary files report '-' for counts -> treat as 0). Fail-open: on any error
    return the structure with empty lists and zeroed diff_stats.
    """
    empty = {
        "files_changed": [],
        "added": [],
        "modified": [],
        "deleted": [],
        "diff_stats": {"files": 0, "insertions": 0, "deletions": 0},
    }
    try:
        before_p = (before or {}).get("porcelain", {}) or {}
        after_p = (after or {}).get("porcelain", {}) or {}

        added, modified, deleted = [], [], []
        files_changed = []
        for path, code in after_p.items():
            if before_p.get(path) == code:
                continue
            files_changed.append(path)
            first = code[0] if code else ""
            if first in ("?", "A"):
                added.append(path)
            elif first == "D":
                deleted.append(path)
            else:
                modified.append(path)

        files_changed = sorted(files_changed)

        diff_stats = {"files": 0, "insertions": 0, "deletions": 0}
        if files_changed:
            try:
                r = subprocess.run(
                    ["git", "diff", "--numstat", "HEAD"],
                    cwd=str(cwd),
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if r.returncode == 0:
                    changed_set = set(files_changed)
                    for line in r.stdout.splitlines():
                        if not line.strip():
                            continue
                        parts = line.split("\t")
                        if len(parts) < 3:
                            continue
                        ins_s, del_s, path = parts[0], parts[1], _strip_quotes(parts[2].strip())
                        if path not in changed_set:
                            continue
                        ins = int(ins_s) if ins_s.isdigit() else 0
                        dels = int(del_s) if del_s.isdigit() else 0
                        diff_stats["files"] += 1
                        diff_stats["insertions"] += ins
                        diff_stats["deletions"] += dels
            except Exception:
                diff_stats = {"files": 0, "insertions": 0, "deletions": 0}

        return {
            "files_changed": files_changed,
            "added": sorted(added),
            "modified": sorted(modified),
            "deleted": sorted(deleted),
            "diff_stats": diff_stats,
        }
    except Exception:
        return empty


def check_run_integrity(did: str, project_root) -> list[str]:
    """Return a list of human-readable warnings about delegation `did`. Empty list = clean."""
    try:
        root = Path(project_root)
        deleg_path = root / ".burnless" / "delegations" / f"{did}.md"
        capsule_path = root / ".burnless" / "capsules" / f"{did}.json"

        if not deleg_path.exists():
            return []

        if not capsule_path.exists():
            return [f"{did}: ran but no capsule (worker envelope unparseable?)"]

        try:
            with capsule_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, ValueError):
            return [f"{did}: capsule unparseable"]

        if not isinstance(data, dict) or not data.get("status"):
            return [f"{did}: capsule has no status"]

        return []
    except Exception:
        return []


def scan_orphans(project_root, limit: int = 50) -> list[str]:
    """Return dXXX ids with a delegation md but no capsule json, newest first, capped at limit."""
    try:
        root = Path(project_root)
        deleg_dir = root / ".burnless" / "delegations"
        capsule_dir = root / ".burnless" / "capsules"

        if not deleg_dir.exists():
            return []

        mds = sorted(deleg_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
        orphans = []
        for md in mds:
            did = md.stem
            capsule = capsule_dir / f"{did}.json"
            if not capsule.exists():
                orphans.append(did)
            if len(orphans) >= limit:
                break
        return orphans
    except Exception:
        return []
