# Project AGENTS.md — Burnless active

This project has a `.burnless/` directory. Prefer delegating work with
`burnless do "<spec>"` (atomic) over editing files directly; use
`burnless delegate "<spec>"` then `burnless run <id>` for staged execution.

- **Worker specs must use absolute paths** (for example `/Users/.../file.py`);
  relative paths fail in the worker's isolated working directory.
- **Gold/Diamond tier tasks:** prefer `burnless ask --tier gold/diamond` for
  planning, architecture, and irreversible-decision arbitration — this is
  currently a RECOMMENDATION, not an enforced requirement.
- **Recovery:** the `SessionStart` hook restores rolling-memory state for this
  project automatically at the start of a new session — no manual raw-log
  replay needed.
- **When to delegate:** conversational questions and quick single-file reads —
  just answer. Multi-file or spec-able changes — prefer `burnless do`.
- **Language:** these operating instructions are in English. ALWAYS reply to
  the user in the USER'S language.
- **Reference:** run `burnless --help` for current commands and flags.

Copy this file to the project root as `AGENTS.md` (or merge it into an
existing one) to give Codex the same operating rules Burnless expects in a
project it orchestrates. This is separate from the global
`~/.codex/AGENTS.md` block installed by `burnless setup --codex`.
