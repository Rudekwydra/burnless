#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"
PYPI_ENV="${PYPI_ENV:-$HOME/.config/burnless/pypi.env}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "release: missing Python executable: $PYTHON_BIN" >&2
  exit 2
fi

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

"$PYTHON_BIN" -m build --no-isolation
"$PYTHON_BIN" -m twine check dist/burnless-*

if [[ "${1:-}" == "--upload" ]]; then
  "$PYTHON_BIN" -m twine upload dist/burnless-*
else
  echo "release: build/check complete. Re-run with --upload to publish."
fi
