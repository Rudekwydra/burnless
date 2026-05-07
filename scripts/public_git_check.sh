#!/usr/bin/env bash
set -euo pipefail

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "public-git-check: not inside a git work tree" >&2
  exit 2
fi

required_public=(
  "AGENTS.md"
  "GEMINI.md"
  "BURNLESS_FOR_LLMS.md"
  "llms.txt"
  "site/llms.txt"
)

missing_public=()
for path in "${required_public[@]}"; do
  if [[ ! -f "$path" ]]; then
    missing_public+=("$path (missing)")
  elif ! git ls-files --error-unmatch "$path" >/dev/null 2>&1; then
    missing_public+=("$path (not tracked)")
  fi
done

if (( ${#missing_public[@]} > 0 )); then
  echo "public-git-check: required public repo guidance is missing from git:" >&2
  printf '  %s\n' "${missing_public[@]}" >&2
  echo "public-git-check: add these files before committing or releasing." >&2
  exit 1
fi

tracked_private="$(
  git ls-files \
    '.burnless' '.burnless/**' \
    'dist' 'dist/**' \
    'build' 'build/**' \
    'exec_log' 'exec_log/**' \
    '.env' '.env.*' '*.env' \
    '.pypirc' '*.key' '*.pem' '*.p12' '*.p8' \
    'docs/ops' 'docs/ops/**' \
    'memory' 'memory/**' \
    '_design/blindless' '_design/blindless/**' \
    '_design/brecha*.md' \
    '_design/plugin_protocol_v0_hooks_audit.md' \
    '_design/evidence_rubrica.md' '_design/warm_cache_measurement.md' \
    'OPERATING_PROFILE.md' 'soul.md' \
    'LAUNCH_PACKAGE.md' 'PITCH_PT.md' \
    2>/dev/null | grep -vx '.env.example' || true
)"

if [[ -n "$tracked_private" ]]; then
  echo "public-git-check: private/local paths are tracked:" >&2
  echo "$tracked_private" >&2
  exit 1
fi

secret_hits="$(
  git grep -n -E \
    'sk-ant-[A-Za-z0-9_-]{20,}|sk-proj-[A-Za-z0-9_-]{20,}|pypi-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|BEGIN (RSA |OPENSSH |PRIVATE )?PRIVATE KEY' \
    -- \
    ':!*.png' ':!*.jpg' ':!*.jpeg' ':!*.gif' ':!*.pdf' \
    2>/dev/null || true
)"

if [[ -n "$secret_hits" ]]; then
  echo "public-git-check: possible real secret in tracked files:" >&2
  echo "$secret_hits" >&2
  exit 1
fi

echo "public-git-check: ok"
