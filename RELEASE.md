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

## Site Deploy (burnless.pro)

**No auto-deploy.** There is no GitHub Actions workflow and no `wrangler.toml`. Every site release requires a manual deploy step after pushing to GitHub.

### Prerequisites

Credentials live outside the repo — never commit them:

```text
~/.config/burnless/supabase.env   → SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY
~/.config/cloudflare/burnless_pages.env → CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_PAGES_PROJECT
```

`npx wrangler` (v4+) must be available (`npm i -g wrangler` or via npx).

### Deploy

```bash
# 1. Load credentials
SUPABASE_URL=$(grep "^SUPABASE_URL=" ~/.config/burnless/supabase.env | cut -d= -f2-)
SUPABASE_KEY=$(grep "^SUPABASE_PUBLISHABLE_KEY=" ~/.config/burnless/supabase.env | cut -d= -f2-)
CF_TOKEN=$(grep "^CLOUDFLARE_API_TOKEN=" ~/.config/cloudflare/burnless_pages.env | cut -d= -f2-)
CF_PROJECT=$(grep "^CLOUDFLARE_PAGES_PROJECT=" ~/.config/cloudflare/burnless_pages.env | cut -d= -f2-)

# 2. Build deploy dir with credentials filled in (never committed to git)
DEPLOY_DIR=$(mktemp -d)
cp -r site/. "$DEPLOY_DIR/"
sed -i '' "s|const SUPABASE_URL = '';|const SUPABASE_URL = '$SUPABASE_URL';|" "$DEPLOY_DIR/index.html"
sed -i '' "s|const SUPABASE_ANON_KEY = '';|const SUPABASE_ANON_KEY = '$SUPABASE_KEY';|" "$DEPLOY_DIR/index.html"

# 3. Deploy
CLOUDFLARE_API_TOKEN="$CF_TOKEN" npx wrangler pages deploy "$DEPLOY_DIR" \
  --project-name "$CF_PROJECT" \
  --commit-dirty=true

# 4. Verify
curl -s https://burnless.pro | grep "SUPABASE_URL"
```

### Why credentials are blanked in git

`site/index.html` stores `SUPABASE_URL = ''` and `SUPABASE_ANON_KEY = ''` as placeholders.
The deploy step fills them from `~/.config/burnless/supabase.env` at deploy time.
The Supabase anon key (`sb_publishable_*`) is a client-side public key — it is visible in the deployed HTML — but keeping it out of git prevents accidental key rotation issues and keeps the public repo clean.

### Verify after deploy

```bash
diff <(curl -s https://burnless.pro) <(
  SUPABASE_URL=$(grep "^SUPABASE_URL=" ~/.config/burnless/supabase.env | cut -d= -f2-)
  SUPABASE_KEY=$(grep "^SUPABASE_PUBLISHABLE_KEY=" ~/.config/burnless/supabase.env | cut -d= -f2-)
  sed "s|const SUPABASE_URL = '';|const SUPABASE_URL = '$SUPABASE_URL';|;
       s|const SUPABASE_ANON_KEY = '';|const SUPABASE_ANON_KEY = '$SUPABASE_KEY';|" site/index.html
)
# Exit 0 = live matches local (with credentials filled in)
```
