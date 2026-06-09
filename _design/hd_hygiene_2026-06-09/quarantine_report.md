# Quarantine Report — HD Hygiene 2026-06-09

Delegation **d516**. REVERSIBLE quarantine (move, never delete) of 4 TRUE-zombie
`.burnless` dirs living in archive/Dropbox/scratch locations.

- **Quarantine root:** `~/.burnless_quarantine_2026-06-09/`
- **Method:** `mv` (atomic move). No `rm` was used anywhere. Original relative path
  preserved inside the quarantine for readability.
- **Restore:** `~/.burnless_quarantine_2026-06-09/restore.sh` (executable, `-rwxr-xr-x`).

## Moved dirs (source → dest)

| # | Source (original) | Dest (inside quarantine) |
|---|---|---|
| 1 | `/Users/roberto/Library/CloudStorage/Dropbox/RUDEKWYDRA/CHARDON/chardon_catalog/.burnless` | `~/.burnless_quarantine_2026-06-09/Library/CloudStorage/Dropbox/RUDEKWYDRA/CHARDON/chardon_catalog/.burnless` |
| 2 | `/Users/roberto/antigravity/semgit/social-machine/.burnless` | `~/.burnless_quarantine_2026-06-09/antigravity/semgit/social-machine/.burnless` |
| 3 | `/Users/roberto/antigravity/semgit/burnless-launch/.burnless` | `~/.burnless_quarantine_2026-06-09/antigravity/semgit/burnless-launch/.burnless` |
| 4 | `/Users/roberto/antigravity/semgit/burnless-launch/experiment/.burnless` | `~/.burnless_quarantine_2026-06-09/antigravity/semgit/burnless-launch/experiment/.burnless` |

All 4 moves confirmed; each dest contains its `config.yaml`.

## Why these were classified TRUE zombies
- **chardon_catalog** — lives under Dropbox/CloudStorage (a synced archive, not an active repo root).
- **semgit/social-machine, burnless-launch, burnless-launch/experiment** — under `semgit/`
  (a no-git scratch/archive area); `experiment/` is itself a throwaway sub-scratch.

These were the exact 4 dirs enumerated in the spec's Task B. No discovery/guessing —
only the listed paths were moved.

## How to restore
Run the generated script (idempotent-safe: skips if source missing or dest already exists):

```sh
~/.burnless_quarantine_2026-06-09/restore.sh
```

It reverses every move, recreating parent dirs as needed and putting each `.burnless`
back at its original absolute path.

## Untouched (per HARD PROHIBITIONS)
NOT touched: `~/.burnless`, `~/.burnless/desktop`, `antigravity/.burnless`,
`antigravity/burnless/.burnless`, nutri, forgetless, fw-social, fw-social-next, agilize,
app_paty, aeomachine, leads-rudekwydra, rudekwydra-atendimento, and anything under any
`.claude/worktrees/`. No config.yaml CONTENT was modified anywhere.

## Verify (run 2026-06-09, all passed)
```
ok: quarantine dir
ok: restore.sh executable
ok: social-machine gone
ok: chardon gone
ok: nutri intact
ok: burnless intact
ok: ~/.burnless intact
```
