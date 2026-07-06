"""Boundary gate (P6/S3): burnless must have zero coupling to forgetless.

Burnless is hot memory and never depends on any cold-memory consumer. The
bridge is the on-disk `burnless-epoch-export/v1` artifact (see PROTOCOL.md);
consumers pull it and keep their own ledger. Therefore the string
"forgetless" (case-insensitive) must not appear anywhere in `src/burnless/`
or `templates/`.

The allowlist is intentionally EMPTY. If a historical comment trips this
gate, delete the comment — do not allowlist it.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCANNED_DIRS = ("src/burnless", "templates")
NEEDLE = b"forgetless"
ALLOWLIST: frozenset[str] = frozenset()  # keep empty — see module docstring
SKIP_DIR_NAMES = {"__pycache__"}
SKIP_SUFFIXES = {".pyc", ".pyo"}


def _scannable_files():
    for rel in SCANNED_DIRS:
        base = REPO_ROOT / rel
        assert base.is_dir(), f"expected directory missing: {base}"
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            if any(part in SKIP_DIR_NAMES for part in path.parts):
                continue  # compiled caches are not source
            if path.suffix in SKIP_SUFFIXES:
                continue
            yield path


def test_no_forgetless_string_in_src_or_templates():
    offenders = []
    for path in _scannable_files():
        rel = str(path.relative_to(REPO_ROOT))
        if rel in ALLOWLIST:
            continue
        data = path.read_bytes().lower()
        if NEEDLE in data:
            lines = [
                f"{rel}:{lineno}"
                for lineno, line in enumerate(data.splitlines(), start=1)
                if NEEDLE in line
            ]
            offenders.extend(lines)
    assert not offenders, (
        "forgetless coupling detected — burnless must stay consumer-agnostic "
        "(export artifacts, never call; see PROTOCOL.md 'Epoch Export "
        f"Contract'): {offenders}"
    )
