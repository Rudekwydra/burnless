# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
