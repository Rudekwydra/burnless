---
name: burnless-router
description: Route Codex tasks to the right Burnless tier (bronze/silver/gold/diamond) before editing or executing — mirrors the same tier discipline Claude Code sessions already use.
---

# Burnless Router (Codex)

Classify every non-trivial task before touching files or a shell. Delegate through Burnless instead
of editing/executing directly whenever the task fits a tier below.

### Tier table (canonical — do not redesign)

| Situation | Action |
|---|---|
| Cheap read/classification | Bronze-capable sidecar |
| Implementation/tests/docs | Codex Silver via `burnless do/run` |
| Planning/architecture/textual arbitration | `burnless ask --tier gold` |
| Irreversible decision/final second opinion | `burnless ask --tier diamond`, explicit and rare |
| Gold/Diamond need to edit or use tools | Plan with `ask`; execute separately on Silver; escalating the executor itself is exception-only and audited |

Gold/Diamond `ask` usage above is a **current recommendation**, not an enforced hard requirement —
nothing blocks you from skipping it, this is guidance for picking the cheapest correct tier.

### Hard rules

1. Classify tier before editing or running shell for anything non-trivial. When the route isn't
   obvious, run `burnless ask --dry-run` first instead of guessing.
2. Use the minimum tier capable of the task. Don't reach for gold/diamond when silver suffices.
3. Worker specs need absolute paths — same rule as the Claude-facing delegation contract.
4. Verify worker output against the live filesystem/services before trusting it — an `OK` result
   still needs the same `## Verify`-style scrutiny Claude-side delegations use.
5. Recover relevant memory when it's configured for the session before delegating.

### Unverified

This skill's actual loading mechanism by the Codex CLI (file location, frontmatter schema,
invocation trigger) is **UNVERIFIED** at authoring time — no confirmed official-doc access to
Codex's skill discovery conventions in this session. See
`templates/codex/hooks/README.md` for the same caveat pattern applied to hooks.
