# Burnless Release Runbook

This project keeps local release secrets outside the repo.

## Local Secret Files

Preferred location:

```text
~/.config/burnless/
  anthropic.env
  pypi.env
  stripe.env
  supabase.env
```

Required permissions:

```bash
chmod 600 ~/.config/burnless/*.env
```

PyPI file format:

```env
TWINE_USERNAME=__token__
TWINE_PASSWORD=pypi-...
```

Do not use `.pypirc` in this repo. Do not commit real `.env` files.

## Build And Check

Install local release tools once:

```bash
.venv/bin/python -m pip install -e '.[release]'
```

Then build and validate the current version:

```bash
scripts/release_pypi.sh
```

The script first runs `scripts/public_git_check.sh`, then reads the version from
`pyproject.toml`, verifies it matches `burnless.__version__`, builds an
isolated sdist+wheel, and runs `twine check` against only that version's
artifacts. No PyPI token is required for build/check.

## Public Git Check

```bash
scripts/public_git_check.sh
```

This fails if local/private paths such as `.burnless/`, `dist/`, `.env`,
`.pypirc`, release keys, internal ops docs, or known private design folders are
tracked by git. It also scans tracked text files for common real secret
patterns such as Anthropic/OpenAI/PyPI tokens and private-key blocks.

## Publish

```bash
scripts/release_pypi.sh --upload
```

Upload mode loads `~/.config/burnless/pypi.env` and uploads only
`dist/burnless-<version>*` with `--skip-existing`.

## Manual Equivalent

```bash
set -a
source ~/.config/burnless/pypi.env
set +a
.venv/bin/python -m build --sdist --wheel
.venv/bin/python -m twine check dist/burnless-<version>*
.venv/bin/python -m twine upload dist/burnless-<version>*
```
