---
description: Set the Burnless engagement mode for this session (off | partner | on)
---

`/burnless [off|partner|on]` — choose how much Burnless takes over this session.

- **off** — raw chat, zero Burnless.
- **partner** — assistant keeps full reasoning and delegates execution to Burnless tiers
  (`burnless do/delegate`) where it makes sense. No role injection.
- **on** — assistant is pinned to the Maestro role: compress intent and *only* delegate
  (no direct code/edits/deep planning); read only the compact capsule, never the raw log.

`/burnless` with no argument shows the menu and the current mode. The choice is stored per
session (`~/.burnless/state/session-<id>.mode`) and takes effect from the next turn. See the
"Engagement modes" section of the README.

This command emits the mode sentinel below; a `UserPromptSubmit` hook resolves it and shapes
behavior for the rest of the session (see `docs/USING_BURNLESS_FROM_YOUR_LLM.md` to install the hook).

__BURNLESS_MODE_CMD__ $ARGUMENTS
