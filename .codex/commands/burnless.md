# Burnless — Codex instructions

When the user asks you to use Burnless, act as the orchestrator: **delegate execution** to Burnless
tiers instead of editing files or running shell directly. Burnless is your execution boundary.

## Activation

1. Check it's installed: `burnless --version`. If missing: tell the user `pip install burnless` and stop.
2. For any task that produces artifacts or runs commands, delegate:
   ```
   burnless route "<task>"                 # preview which tier would handle it
   burnless do "<task>" --tier bronze|silver|gold   # delegate + run atomically
   burnless read dXXX                       # read the compact result capsule
   ```
3. End every code spec with a `## Verify` fenced shell block asserting the DoD — the runner re-runs it
   and demotes `OK → PART` on failure (deterministic gate). `PART` → reject + re-spec smaller; never
   merge partial work.

## Tiers (roles, not models)

`gold` = architecture/decisions, `silver` = implementation with a tight spec + HARD PROHIBITIONS,
`bronze` = reads/summaries/ops/local code. Models per tier are configured in `.burnless/config.yaml`
(any provider, any CLI). Spec quality picks the tier — don't tier-creep.

## Canonical reference

- `docs/DOCTRINE.md` — how to use Burnless (single source of truth)
- `docs/COMMANDS.md` — verified CLI flags
- `docs/USING_BURNLESS_FROM_YOUR_LLM.md` — operator manual

## Deprecated (do not use)

`burnless compress` / `burnless decode` (cipher capsule round-trip) and `burnless brain` are retired.
Use `burnless chat` for an interactive session; the live decode is semantic (no cipher). See DOCTRINE.

## Notes

- All state lives in `.burnless/` — local, no cloud, no telemetry.
- Provider-agnostic: works with any model configured in `.burnless/config.yaml`.
