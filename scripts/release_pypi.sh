#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"
PYPI_ENV="${PYPI_ENV:-$HOME/.config/burnless/pypi.env}"
UPLOAD=0

case "${1:-}" in
  "")
    ;;
  "--upload")
    UPLOAD=1
    ;;
  "-h"|"--help")
    echo "usage: scripts/release_pypi.sh [--upload]"
    echo
    echo "Builds sdist+wheel for the version in pyproject.toml and runs twine check."
    echo "Use --upload to publish those exact versioned artifacts to PyPI."
    exit 0
    ;;
  *)
    echo "release: unknown argument: $1" >&2
    echo "usage: scripts/release_pypi.sh [--upload]" >&2
    exit 2
    ;;
esac

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "release: missing Python executable: $PYTHON_BIN" >&2
  echo "release: create one with: python -m venv .venv && .venv/bin/python -m pip install -e '.[release]'" >&2
  exit 2
fi

require_module() {
  local module="$1"
  if ! "$PYTHON_BIN" -c "import ${module}" >/dev/null 2>&1; then
    echo "release: missing Python module: ${module}" >&2
    echo "release: install release tools with: $PYTHON_BIN -m pip install -e '.[release]'" >&2
    exit 2
  fi
}

scripts/public_git_check.sh

VERSION="$("$PYTHON_BIN" -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
CODE_VERSION="$("$PYTHON_BIN" - <<'PY'
from pathlib import Path
import re

text = Path("src/burnless/__init__.py").read_text(encoding="utf-8")
match = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.M)
if not match:
    raise SystemExit("missing __version__ in src/burnless/__init__.py")
print(match.group(1))
PY
)"
if [[ "$VERSION" != "$CODE_VERSION" ]]; then
  echo "release: version mismatch: pyproject.toml=${VERSION}, burnless.__version__=${CODE_VERSION}" >&2
  exit 2
fi

require_module build
require_module twine

DIST_GLOB="dist/burnless-${VERSION}*"

rm -f $DIST_GLOB
"$PYTHON_BIN" -m build --sdist --wheel
"$PYTHON_BIN" -m twine check $DIST_GLOB

if [[ "$UPLOAD" == "1" ]]; then
  if [[ ! -f "$PYPI_ENV" ]]; then
    echo "release: missing PyPI env file: $PYPI_ENV" >&2
    echo "expected keys: TWINE_USERNAME=__token__ and TWINE_PASSWORD=pypi-..." >&2
    exit 2
  fi

  set -a
  # shellcheck disable=SC1090
  source "$PYPI_ENV"
  set +a

  if [[ -z "${TWINE_USERNAME:-}" || -z "${TWINE_PASSWORD:-}" ]]; then
    echo "release: TWINE_USERNAME/TWINE_PASSWORD not set by $PYPI_ENV" >&2
    exit 2
  fi

  "$PYTHON_BIN" -m twine upload --skip-existing $DIST_GLOB
else
  echo "release: build/check complete for burnless ${VERSION}. Re-run with --upload to publish."
fi
