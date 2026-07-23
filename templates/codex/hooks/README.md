# Codex hooks (burnless epoch, `--host codex`)

Codex 0.144.4 uses the documented global hook source `~/.codex/hooks.json`.
`burnless setup --codex` installs this directory's shell scripts under
`~/.codex/hooks/`, merges the three registrations below into that global JSON
file, and leaves `~/.codex/config.toml` untouched.

- `SessionStart`: restore on `startup`, `resume`, `clear`, or `compact`.
- `Stop`: append the completed turn to the journal.
- `SessionEnd`: checkpoint/handoff when the main thread actually ends. Codex
  emits this on normal close, archive/delete of an open conversation, or after
  30 minutes idle with no connected client; switching conversations alone does
  not end the session immediately.

Each command receives one JSON object on stdin. The fields used here are the
documented `session_id` and `cwd`; Codex does not document a host PID field, so
the scripts retain their process-ancestor fallback for lineage.

Codex skips new or changed non-managed hooks until their exact definitions are
trusted. After setup or an upgrade, open `/hooks` in Codex, review the three
Burnless commands, and trust them. Trust is recorded against the definition
hash, so changed hooks must be reviewed again.
