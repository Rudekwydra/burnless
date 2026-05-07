# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.6.8] — 2026-05-06 / 2026-05-07
### Site URLs reorganized
- **`free.burnless.pro`** is now the canonical home of the open-source / community site (the `site/` folder in this repo). Served by Cloudflare Pages, also reachable at `burnless.pages.dev`.
- **`burnless.pro`** apex is now reserved for the paid Pro/Cloud landing (deployed separately from the private `_pro/landing/` directory via Vercel; not in this repo).
- Updated `og:url`, `og:image`, `twitter:image` in `site/index.html`, `pyproject.toml` Homepage, `llms.txt`, `site/llms.txt`, and `RELEASE.md` Site Deploy runbook to reflect the rename.

### Added
- **Compression filter (Stage 1 LLM + Stage 2 telegrafista)** — example plugin `burnless-compress` in `examples/plugins/` implementing `pre_worker_prompt` and `pre_brain_prompt` hooks (PLUGIN_PROTOCOL.md v0.7). Compresses verbose human prompts before they reach the cloud LLM. Empirical: 2.5× compression on 50 PT samples with `qwen2.5:7b-instruct` local + telegrafista. Stdlib-only, fail-open.
- **`docs/USING_BURNLESS_FROM_YOUR_LLM.md`** — short operating manual the human points their AI assistant at: install Burnless, then say "use this tool, manual is at this path." Covers core commands, tier semantics, audit contract, and how the compression plugin is consumed.
- **`bench/COMPRESSION_FINDINGS.md`** with embedded SVG chart — 50 PT samples × 4 LLMs (2 local, 2 Ollama Cloud), tokens via tiktoken cl100k_base. Documents method and counter-intuitive finding: compression depends on family + size, not size alone (Qwen 80B compresses harder than Gemma 27B).
- **6 new bench scripts**: `cache_warm_check`, `cache_invalidation`, `cache_persistence`, `replay_vs_capsule`, `tier_routing_savings`, `filter_entrada_spike`, `aggregate_compression`. All run via `claude -p` with `--output-format json` to validate cache mechanics on the monthly plan path (no `ANTHROPIC_API_KEY` required).
- **README "For end users — tell your LLM to use Burnless"** section with explicit caveat that the chat shell is still evolving and community contributions are welcome.

### Changed
- **Pitch repositioning** in README, llms.txt, BURNLESS_FOR_LLMS.md: capsules-as-invention is now lead, not "shared prefix cache." Multi-LLM delegation, tier routing, and prompt caching are explicitly framed as commodity infrastructure that exists in other frameworks; capsules (`Θ(N²) → Θ(N)`) are the protocol-layer invention.
- **README `## Design decisions`**: `Brain stores semantic capsules` is now decision #1 ("the only invention in Burnless"), promoted from #2.
- **Honest path-dependence note** added after the numbers tables: `bench/run.py` (SDK-direct + explicit `cache_control`) is the API-credits ceiling; `claude -p` (Claude Code monthly plan, auto-cache absorbs much of the replay) is the floor at ~1.1–2× per session at low N. The 16× weekly figure was measured on the subscription path over a full development workload, so it holds at scale.

### Fixed
- `cli.py:_should_use_cached_worker` and `cached_worker.py` module docstrings: removed the false claim that `claude -p` lacks `cache_control`. Empirically: `claude -p` auto-caches user messages with `ephemeral_1h` TTL; CachedWorker is the SDK path for explicit cache_control tuning, not the only path to prefix-cache warmth.

### Privacy
- `.gitignore` now blocks `*.cast` (asciinema recordings often capture full terminal sessions) and `QTP_*.md` (operational test reports with client PII).

## [0.6.7] — 2026-05-05
### Added
- **Thought vs execution reports**: Worker JSON now carries `kind: execution | thought`. Thought-only reports can finish as `OK` without execution evidence, while execution reports still require verifiable evidence.
- **Dynamic heartbeat UX**: `burnless run` progress now keeps a short live state visible (`thinking`, `reading`, `writing`, `testing`, `auditing`, `compressing`) and shows idle time after 2s without worker output.
- `kind` is persisted in summaries and raw logs so `read/log/capsule` can distinguish reasoning reports from execution reports.
- Private release sync protocol added under `.burnless/`; public `RELEASE.md` now documents the required update order.

### Changed
- The audit loop skips execution-evidence checks for thought-only reports instead of creating a false `PART`.
- `brief/watch` progress panels and `minimal` progress now remain legible without accumulating terminal-scroll history.

## [0.6.6] — 2026-05-05
### Added
- **Stale/heartbeat detection**: `display.stale_timeout_seconds` config key (default: 300). Workers that emit no stdout/stderr for the configured duration are killed and the delegation is marked `PART` with `stale_worker` issue. Set to `0` to disable.
- `RunResult.stale` field propagated through `to_dict()` and surfaced in `burnless run` output.
- 11 new unit tests in `tests/test_stale.py` covering config defaults, `RunResult` field, kill behaviour, non-trigger case, disabled case, and `cmd_run` PART output.

### Changed
- **README**: Added "Using Burnless with an AI Assistant" section — any LLM can use `burnless delegate/run/read` as its execution boundary.
- **RELEASE.md**: Added "Site Deploy (burnless.pro)" runbook with credential loading, Wrangler deploy, and verification steps.

## [0.6.5] — 2026-05-05
### Added
- **Ephemeral progress display** for `burnless run` and the shell. Three modes:
  - `minimal` — rotating spinner + phase label (`pensando/lendo/editando/testando/auditando/compactando`) on a single carriage-return line; no terminal-scroll history growth.
  - `brief` (default) — Rich live panel that disappears after the worker finishes (transient); phase events fed from worker output.
  - `full` — raw streaming, unchanged from previous behaviour.
- `display.progress_detail: minimal|brief|full` config key in `.burnless/config.yaml` (default: `brief`).
- `burnless run --progress minimal|brief|full` CLI flag; overrides the config key.
- Shell spinner writes directly to the real `stdout` fd so it survives the internal `_capture` redirect.
- 13 new unit tests covering `_detect_phase`, `_MinimalSpinner` non-tty path, config defaults, mode resolution precedence, and flag-overrides-config.

## [0.6.4] — 2026-05-05
### Changed
- Public docs (README, llms.txt, site/llms.txt) rewritten for honest Free/Core scope: protocol layer, Brain Without Tools, workers, capsules, basic audit trail.
- Removed marketing terms "self-healing" and "process audit loop" from audit loop descriptions; behavior described accurately.
- site/index.html: Supabase credentials replaced with configuration placeholders — no secrets in the public repo.

## [0.6.3] — 2026-05-04
### Changed
- TCP/IP framing propagated to all public-facing surfaces: pyproject.toml
  description, site title/meta/hero, MATH.md opening, llms.txt, and
  BURNLESS_FOR_LLMS.md. Any LLM or human encountering Burnless cold now
  sees the correct architectural frame before anything else.

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
