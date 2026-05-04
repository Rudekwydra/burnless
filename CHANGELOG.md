# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.6.2] — 2026-05-04
### Fixed
- Version bump to resolve PyPI conflict (0.6.1 was published without a matching git tag).

## [0.6.1] — 2026-05-04
### Added
- `BURNLESS_FOR_LLMS.md`, a one-page entry point for AI assistants and
  reviewers that defines Burnless as intent-compressed orchestration and sets
  the correct vocabulary around semantic capsules, cost math, and privacy
  levels.

### Changed
- Public docs and package metadata now use the protocol vocabulary consistently:
  semantic capsule, compressed state representation, dense semantic summary,
  and privacy-by-architecture.

### Fixed
- `burnless run` now uses the configured Worker CLI by default. The Anthropic
  SDK Maestro backend is opt-in via `--maestro`, preventing local filesystem
  tasks from being sent to an in-process model with no tools.
- Natural-language requests that mention local paths, projects, repositories,
  or memory notes now route to `silver` instead of falling back to `bronze`.
- Worker prompts now include runtime context: working directory, `.burnless`
  state location, memory-index hints, and instructions to search local project
  roots before returning `BLK`.
- Legacy `diamond`-only configs migrate correctly to `silver` after defaults
  are merged.

## [0.5.9] — 2026-05-04
### Fixed
- `burnless compress` no longer hangs when called without arguments in a non-interactive context (CI, scripts, `|| true` chains). Now exits with a clear error message and usage hint instead of blocking on stdin.

## [0.5.7] — 2026-05-04
### Added
- **Roundtrip decode**: `burnless run` now pipes the worker's capsule through Haiku (`decoder.decode` with `voice_sample=goal`) before returning to Brain. Brain sees ~100-200 tokens of natural-language prose instead of raw verbose output. `--no-decode` skips this step.
- The full delegation pipeline is now: Brain → worker (gold/silver) → Haiku compress → Haiku decode (voice_match) → Brain. O(N²) cost eliminated end-to-end.

## [0.5.6] — 2026-05-04
### Fixed
- `pyproject.toml`: added classifiers, keywords, and project URLs (Homepage, Repository, Changelog) for proper PyPI page rendering.
- `setup_wizard`: codex no longer overrides silver when claude is available.

## [0.5.5] — 2026-05-04
### Added
- `/chat` in the shell now uses Anthropic SDK with real prefix-cache warmth: first turn writes cache, second turn+ reads it (~99% input cost saving shown inline).
- System anchor in chat loads project docs (VISION.md, PROTOCOL.md, README.md) to ensure ≥1024 token threshold for cache activation.

### Fixed
- `/commands` and `/workers` no longer routed as delegations — now recognized as slash commands.
- `maestro_legacy`: added extended-cache-ttl beta header (was missing, preventing 1h TTL from activating).
- Routing keywords: `architect`, `design`, `system` added to gold tier.

## [0.5.4] — 2026-05-04
### Changed
- PROTOCOL.md: added Architecture section (Encoder/Maestro/Workers), Privacy Levels table (0–3), and cache/model-switching note
- README: principle 6b rewritten — privacy is a consequence of where each component runs, not a mode flag
- Site: privacy pain-grid item updated to reflect the four architecture levels; pricing lede clarified

## [0.5.3] — 2026-05-04
### Added
- `PROTOCOL.md` with explicit cost/redact/audit/opaque privacy modes.
- Capsule v2 envelope: `burnless:v2:<session_id>:<key_id>:<ciphertext>`.
- In-memory keyring for v2 capsules and compatibility decoding for v1 capsules.
- Tests proving v2 capsules do not embed the key.

### Changed
- README/VISION now distinguish cost compression from enterprise privacy claims.
- `privacy` config now exposes planned `mode`, `raw_retention`, and `key_store` knobs.

## [0.5.2] — 2026-05-04
### Added
- Burnless Chat opens by default in initialized projects.
- Slash-command UX for `/commands`, `/workers`, `/native`, `/model`, and `/maestro`.
- Brain adapter capability model for Anthropic SDK, configured worker CLIs, and planned native mode.
- Realtime cache-compaction policy based on break-even math instead of fixed capsule counts.

### Changed
- Default product tiers are now `gold`, `silver`, and `bronze`; `diamond` is retained only as a legacy alias.
- Setup defaults can mix Claude/Codex/Ollama across the same three quality/cost bands.

## [0.5.1] — 2026-05-03
### Added
- Friendly mode V1: decoder mirrors user's tone/slang/register in replies (default ON, ~5% extra tokens). Toggle via `/voice on|off` in shell or `compression.voice_match` in config.yaml.

### Changed
- `burnless init` in already-initialized project shows friendly hint instead of bare warning.
- README: clarified that `burnless setup` covers init; documented `rm -rf .burnless/` as uninstall path.
