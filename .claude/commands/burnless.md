---
description: Set the Burnless engagement mode for this session (off | on)
---

`/burnless [off|on]` — choose whether Burnless drives this session.

- **off** — raw chat, zero Burnless.
- **on** — assistant is the Maestro: compress intent and *only* delegate via `burnless do/delegate`
  (no direct code/edits/deep planning; read only the compact capsule, never the raw log), **plus**
  rolling memory — epoch hooks keep context O(N) and survive `/clear`. This is the one efficient mode.

(Legacy `partner` and `rollover` are removed; both are coerced to `on` for back-compat.)

`/burnless` with no argument shows the menu and the current mode. The choice is stored per
session (`~/.burnless/state/session-<id>.mode`) and takes effect from the next turn. See the
"Engagement modes" section of the README.

This command emits the mode sentinel below; a `UserPromptSubmit` hook resolves it and shapes
behavior for the rest of the session (see `docs/USING_BURNLESS_FROM_YOUR_LLM.md` to install the hook).

__BURNLESS_MODE_CMD__ $ARGUMENTS
