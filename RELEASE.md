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

```bash
scripts/release_pypi.sh
```

The script loads `~/.config/burnless/pypi.env`, builds with the local virtualenv,
and runs `twine check`.

## Publish

```bash
scripts/release_pypi.sh --upload
```

## Manual Equivalent

```bash
set -a
source ~/.config/burnless/pypi.env
set +a
.venv/bin/python -m build --no-isolation
.venv/bin/python -m twine check dist/burnless-*
.venv/bin/python -m twine upload dist/burnless-<version>*
```
