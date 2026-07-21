# Codex hooks (burnless epoch, `--host codex`)

- **`SessionStart` payload schema is UNVERIFIED.** No official-doc access and no local evidence of
  its real shape at authoring time. `codex_payload.sh` defensively tries several candidate key
  names for session id and cwd, and validates the session id against
  `transcript_sources.resolve_path` before trusting it.
- When no candidate validates, the hook dumps the raw stdin payload (truncated to ~4KB) to
  `~/.burnless/codex_hook_payloads/<unix-timestamp>.json`. This dump is the intended schema-discovery
  mechanism — it stays on by default this wave (greenfield, zero cost) and should be removed once the
  real schema is confirmed and captured.
- **`Stop` is the guaranteed anchor event** for this wave — it's what actually writes to the journal.
  `SessionStart` is best-effort and safe-no-op if the event never fires.
- **`PostToolUse` is out of scope this wave** (high-frequency noise, unvalidatable without a test
  harness). It's a documented future contingency: if `SessionStart` proves to never fire in practice,
  a lazy-init on the first `PostToolUse` call would be the fallback path to pick up rolling-memory
  restore late.
