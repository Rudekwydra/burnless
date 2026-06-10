from __future__ import annotations
import json
from pathlib import Path


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
