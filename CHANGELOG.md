# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.9.5] — 2026-07-11

First PyPI release of the v0.9 line (PyPI was at 0.7.4). Highlights since 0.8.0: v1-scope hardening (CLI-driven usage; interactive chat shell removed), single faithful compression mode, rolling memory (epoch hooks, `/clear`-survivable context), engagement modes `off`/`on`, maestro adapters for OpenAI/Gemini/OpenRouter, warm pool with hermetic test isolation, `## Verify` fail-closed gate. Suite: 1222 tests green.

### Added

- **`burnless epoch export` + published `burnless-epoch-export/v1` contract.**
  On SessionEnd the consolidated `living_md` (V3) is written atomically
  (tmp + fsync + rename) to
  `<project>/.burnless/exports/epoch-<host>-<sid8>-<UTCts>.md` with a YAML
  front-matter header followed by the living document verbatim. Fail-open,
  skips empty documents, retention via `epochs.exports_keep` (default 30,
  oldest GC'd). Full contract in `PROTOCOL.md` ("Epoch Export Contract").

### Removed

- **`burnless epoch seal`.** Sealing shelled out to an external cold-memory
  binary at session end, which coupled burnless's hot-memory path to another
  application's runtime (app separation violation: hot memory must never
  depend on a consumer). The bridge is now an on-disk artifact: burnless
  exports, consumers pull `.burnless/exports/` on their own schedule with
  their own ledger and never write inside `.burnless/`. Use
  `burnless epoch export` instead; the session-end hook already does.

## [0.8.0] — 2026-05-25

### Highlights

- **Warm session pool, now global and default.** Every worker forks a cached
  prefix held in `~/.burnless/warm_session*.json` (one pool per `(user, model)`,
  shared across every project and window). The boot warmer pays the cold
  establishment once per install; every subsequent `burnless do` in any
  project forks the warm prefix, paying only the new payload. Worker silent
  cold-spawn is now treated as a regression — three fallback paths in
  `agents.py` and one in `live_runner.py` now emit explicit `[burnless] WARN:
  ... will spawn COLD` on stderr instead of degrading quietly.
- **Per-(provider, model) warm pool.** Each `(provider, model)` pair keeps
  its own warm session at `~/.burnless/warm/<provider>/<model>.json`.
  Opening a haiku worker after a sonnet worker no longer prunes the
  sonnet cache; they coexist. Drift-as-prune logic is gone end-to-end
  — different models simply live in different files. Auto-init on
  first dispatch covers cold-start; the daemon-based keepalive is
  available via `burnless warm daemon start` for batch-heavy use cases
  but is NOT enabled by default (a per-day cold init costs ~$0.022
  and a refresh ping costs ~$0.0014, so the daemon is only worthwhile
  when daily cold-starts ≥ 1; for typical interactive use the
  on-demand auto-init resolves the prewarm problem more cleanly).
- **Maestro stable-prefix cache complete.** The maestro layer was already
  caching its `system` array (glossary 1h, role 1h, recent_capsules 5m); the
  `messages` history was not. With this release the last block of the last
  history message now carries the 4th (and final) Anthropic cache_control
  breakpoint, so every turn after the first reads the accumulated history
  from cache (10% input price) instead of re-billing it at full rate.
- **`burnless run` is silent by default.** A successful run emits one line
  (`OK:dXXX`) on stdout; failures emit one line and a 180-char reason.
  Pass `--verbose` (or run with a TTY attached) to restore the full panel
  output. The previous behavior would dump tens of thousands of tokens of
  worker stream-JSON into whatever shell or harness invoked it, poisoning
  any main-session that read the result.
- **Filesystem-first audit contract.** The strict JSON envelope requirement
  was dropped in favor of inferring status from exit_code + git diff. The
  audit module that lived inside `cli.py` was extracted, then killed from
  the core entirely (see fase 4 below); auditing is now a property of the
  filesystem state the worker leaves behind, not a contract clause the
  worker must honor.

### Added

- **`warm_session.py` and `warm_session_codex.py`.** Per-`(user, model)` global
  warm pool. Each holds a UUID + ISO timestamps + neutral W0 brief at
  `~/.burnless/warm_session*.json`. Iso-cwd workers run in
  `~/.burnless/iso-cwd/<warm_uuid>/` to prevent CLAUDE.md leaks from any
  project subtree. Heartbeat refreshes when `last_used > 50 minutes` (1h
  ephemeral_1h TTL minus 10-min headroom).
- **`warm_daemon.py`.** Background daemon that polls warm state and refreshes
  before expiry. CLI: `burnless warm daemon {start,stop,status,run-fg}`.
  `burnless warm refresh [--codex]` for ad-hoc refresh.
- **Maestro `_apply_history_cache_breakpoint` helper.** Marks the tail of
  accumulated history as a cache breakpoint; string content normalized to
  a single text block on the fly so cache_control can attach.
- **`session_token_audit.py` benchmark.** Measures real main-session token
  economics (cache write vs read vs miss) under different orchestration
  modes — baseline solo, light maestro, full pipeline.
- **T5_app_build, T5_50_extended, T7_realistic_human benchmark scenarios.**
  T5_app_build analysis ships with the release; longer-form scenarios staged
  for future longitudinal runs.
- **`runner.py:collect_worker_usage(burnless_root, since_ts)` helper.** Scans
  `<burnless_root>/logs/d*.log` for files modified after a snapshot
  timestamp and aggregates worker usage per model. `call_light` and
  `call_pipeline` now include worker tokens in their `total_usd /
  total_input_tokens / total_output_tokens` — they previously only counted
  the maestro layer, silently subcounting every multi-worker run.
- **`burnless run --verbose` flag.** Opt-in restoration of the pre-0.8.0
  multi-line panel output. The flag is also implied when stdout is a TTY.
- **Liveness v0.8.1 MVP.** Structured event protocol so background observers
  (Monitor, dashboards) can react to worker state changes without parsing
  free-form log lines.
- **Debugless v0.1 MVP.** GoPro-style trace via local ollama — captures the
  full request/response stream into a replayable artifact, no API spend.
- **Bash whitelist + rotating block message in maestro.** Configurable
  whitelist of commands the maestro is allowed to issue; out-of-whitelist
  attempts trigger a rotating helpful-block-message instead of opaque
  rejection.
- **Subprocess IO liveness probe.** Opt-in `psutil`-based check that
  distinguishes a stuck-but-alive worker from a stalled one, so the
  tool-aware stale timeout fires only on real stalls.
- **Tool-aware stale timeout + suspect alerts.** Absolute timeout knows
  which workers are mid tool-call and refuses to kill them prematurely;
  surfaces suspect-alive states for inspection.
- **Warm cache drift detection + opt-in auto_reinit.** Detects when the
  warm prefix has drifted from the configured brief and (opt-in) rebuilds
  it in place.
- **Capsule auto-trail for runs.** Every worker writes a capsule so a
  retrospective `burnless capsule dXXX` always has a record, even after a
  partial or errored run.
- **`list_warm_files()`** helper in `warm_session.py` and
  `warm_session_codex.py` enumerates every `~/.burnless/warm/<provider>/*.json`
  for daemon refresh loops and statusline rendering.
- **`_extract_model_from_parts` + `_detect_provider_from_parts`** in
  `agents.py` parse the worker command line to identify which warm pool
  to use. Unknown providers (gemini, ollama, openrouter) are silently
  skipped — they have no warm-fork analogue at the upstream CLI level
  and that is structural, not a bug.

### Changed

- **`burnless init` default inverted.** `CLAUDE.md` generation is now
  opt-in (`--with-claudemd`) instead of opt-out. Most users were deleting
  it immediately; the default now matches actual behavior.
- **Maestro stops compressing the delegation prompt.** v0.7.x compressed
  the spec before sending it to the worker; this stripped structural
  cues the worker relied on (PROIBIÇÕES DURAS, DoD blocks). The
  delegation prompt is now sent verbatim.
- **Worker contract: audit/status taxonomy removed from docs.** The worker
  no longer has to author an envelope; the audit layer infers everything
  from filesystem state and exit code.
- **Warm codex heartbeat raised 70s → 300s.** Reduces refresh chatter
  without affecting cache hit-rate.
- **Heartbeat poll 60s → 30s; idle threshold 50 → 59s.** Empirically tuned
  against observed daemon scheduling jitter.
- **`burnless run` silent by default** (see Highlights). Old behavior
  available via `--verbose` or TTY-attached stdout.
- **Warm pool state schema is now per-(provider, model)**, not per-provider.
  Old `~/.burnless/warm_session.json` and `~/.burnless/warm_session_codex.json`
  paths are no longer read; users with a v0.7.x state should delete those
  files (or just ignore them — the next dispatch will create the new
  per-model files automatically and the old ones will sit unused).
- **Auto-init is the prewarm path.** Every `burnless do` checks the
  per-model warm file and calls `init()` if missing or expired. No daemon
  required for the basic "never spawn cold" guarantee.

### Fixed

- **`live_runner.py` auto-init warm session on the real dispatch path.**
  Earlier auto-init only ran on a fallback path that most invocations
  skipped, so the first delegation in a project still hit a cold worker.
- **Warm-session ghost detection + auto-prune.** Stale UUIDs from killed
  daemons no longer block fresh inits.
- **Tier_session ghost path eliminated.** Same class of bug as the
  warm-session ghost; the `_load_tier_session` / `_save_tier_session`
  fallback was vestigial and produced infinite loops on certain restart
  paths. Killed end-to-end.
- **`--append-system-prompt` stripped on fork.** This flag invalidated the
  warm prefix; stripping it keeps the cache hot across forks.
- **Concurrent-writer tolerance in metrics.** Two parallel `burnless do`
  calls writing to the metrics file no longer corrupt it.
- **Worker envelope parse tolerance.** Bool, dict, and string variants of
  `validated`/`evidence` no longer crash the parser.
- **Delegation prose schema in English.** Some delegations were rendered
  in PT-BR via the prose-schema path while others in EN; the worker has
  better tool-use compliance with EN-only schemas, so all delegations
  are now EN.
- **JSONL stream decoder for `extract_result_json`.** The decoder now
  correctly walks a streaming JSONL response instead of trying to
  `json.loads` the whole thing.
- **`audit.enabled` guard restored.** Disabling audit in config no longer
  partially executes the audit path.

### Removed

- **`audit.py` module from core.** Auditing is now filesystem-first; the
  monolithic audit module was extracted into `_pro/audit.py` (proprietary
  tier, gitignored) and the core no longer imports it.
- **Strict JSON envelope requirement.** Workers no longer have to author
  a structured envelope; status is inferred from exit_code + git diff.
- **`_load_tier_session` fallback + ghost call to `_save_tier_session`.**
  Vestigial code paths that caused observable runtime bugs.
- **Prompt compression in the delegation path.** See Changed; compression
  hurt more than it helped for specs with structural prohibitions.
- **Prune-by-drift logic.** With per-model warm files, the "model drift"
  condition cannot arise — different models live in different paths.
  `cache_validity` and `prune_ghost` / `prune_stale` no longer accept
  an `expected_model` argument.

### Documentation

- README and llms.txt refreshed to describe the v0.8 default (warm-fork
  + silent-default + filesystem-first audit) instead of the v0.7
  envelope-driven flow.
- Worker contract docs rewritten as filesystem-first.
- "Pink Elephant" framing dropped from prose — describe what is shipped,
  not what is missing.

## [0.7.3] — 2026-05-08

### Documentation — major recalibration
- **Honest history note added to README.** Acknowledges that earlier docs (0.3.0 → 0.6.7 era, 2026-05-03 to 2026-05-05) overclaimed novelty and savings, with specific corrections: TCP/IP analogy reframed as design inspiration not architectural equivalence; "16× cheaper" labeled as personal-workload anecdote not universal claim; cross-model cache sharing claim retracted (Anthropic prefix cache is per-model). Git history left intact — no rewrites, no cover.
- `pyproject.toml description` updated from manifest framing to plain technical description.
- `README.md`, `llms.txt`, `site/llms.txt`, `BURNLESS_FOR_LLMS.md`, `MATH.md`, `PITCH_PT.md`, `LAUNCH_PACKAGE.md`, `soul.md` recalibrated. `VISION.md` left intact per author preference.
- `Structural context — why this exists` section added to README explaining per-token billing as structural pressure that produces verbosity drift in RLHF-trained models.

### Cache + tone-aware encoder/decoder
- **`src/burnless/codec/encoder.py`**: cacheable prefix moved to `system` block with `cache_control: ephemeral 1h`. Few-shots expanded from 8 to 25, covering 12 tone registers (formal / casual / mano / diminutivo / telegraphic / code / emotive / meme / bug_report / tentative / imperative / code_review). Capsule output now carries `[tone:X,lang:Y]` tag detected per-message. Estimated prefix size ~2274 tokens — above Haiku's 2048 cache threshold.
- **`src/burnless/codec/decoder.py`**: cacheable prefix moved to `system` block with `cache_control: ephemeral 1h`. STYLE_GUIDE expanded from 5 to 18 pairs showing same content rendered in different tones based on capsule tone tag. Voice sample remains in user message for per-turn tone matching.
- Both fail-safe: caching errors never block the hot path; metrics recording never blocks the result.

### Real-time metrics instrumentation
- **`src/burnless/metrics.py`**: new functions `record_encoder_call`, `record_decoder_call`, `record_brain_call`, `session_snapshot`, `session_diff`. New counters covering encoder/decoder/brain volumes plus `output_decompression_avoided` source. Numbers are conservative floors by construction (decoder output is shorter than what an unprompted Brain Sonnet would produce — RLHF default leans verbose).
- Wire-up in `encoder.py`, `decoder.py`, `cached_worker.py` is best-effort: observability is never load-bearing.

### Dashboard + CLI
- **`src/burnless/dashboard.py`**: `render_metrics` expanded with full source breakdown plus counter table. Honest footer note about floor estimation. New `render_session_diff` for snapshot comparison.
- **`src/burnless/cli.py`**: `burnless metrics --snapshot LABEL` captures point-in-time snapshot; `burnless metrics --diff` shows delta between two most recent snapshots. `/keepalive on` shows daily idle cost warning (~$0.00045 USD/day on Sonnet, capped at 24 pings).

### Critical operating directives in cached prefix
- **`_design/maestro_v1/brain_role.md`** and **`worker_role.md`**: anti-hype, anti-cerimony, anti-validation-reflex directives prepended to the cached prefix. Forbids "great question" / "absolutely" / "perfect" / hedging "some version of X was thought of" / output ceremony. Mandates output token economy. Applies to all Brain calls and all Worker subprocess calls.

### Bench
- **`bench/daily_compare.py`**: paired-measurement bench comparing compressed pipeline (Haiku encoder → Sonnet brain → Haiku decoder) vs raw pipeline (Sonnet direct). 10 fixed prompts varying tone/length. Cost cap configurable (default $0.50/batch). Designed for cron/launchd daily scheduling — produces longitudinal data on cross-pipeline output ratio.

### Tests
- 173/173 passing. Zero regressions from 0.7.2.

### Compatibility
- Zero breaking changes from 0.7.2. Metrics fields are additive — existing `metrics.json` files are auto-healed on next read. Encoder/decoder cache_control is opt-in via Anthropic's normal cache mechanism (no code change required for existing users).

## [0.7.2] — 2026-05-07

### Added
- **QTP-E: visual review hook** for `kind=execution` audits — when the worker emits `files_touched` containing visual deliverables (`.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`, `.svg`, `.pdf`, `.pptx`, `.html`), the audit JSON now carries `visual_artifacts` (paths) and `visual_thumbnails` (256×256 base64 JPEG). Operator scans for "obviously wrong" output without opening N files manually. Tool chain: Pillow primary → `sips` (macOS) fallback → paths-only when neither tool is available. Configurable via `visual_review.{enabled, thumbnails, max_size, max_artifacts}`. Default ON. Closes the last open issue from `QTP_OPERATIONAL_TEST_2026-05-06.md`.

### Tests
- **+13** in `tests/test_visual_review.py` covering extension detection (`.png/.jpg/.pdf/.pptx/.svg/.gif/.webp/.html`), real PNG → JPEG thumbnail roundtrip via Pillow, missing file handling, no-files-touched no-op, no-visuals no-op, relative path resolution, disabled / thumbnails-off / max_artifacts config respect, default config invariants. Brings total to **173/173 passing**.

### Compatibility
- Zero breaking changes from v0.7.1. The visual review hook attaches new fields to the audit JSON; consumers that ignored unknown fields in v0.7.1 continue to work.

## [0.7.1] — 2026-05-07

Operational hardening release driven by real-world findings from the
QTP delivery batch (5 parallel silver delegations producing client
deliverables). All four QTP issues plus an opt-in cache-prefix layout
and a privacy filter extension.

### Audit
- **QTP-A: filesystem-first auditor** for `kind=execution` reports — when the worker declares `files_touched`, audit by checking files exist on disk + sizes match `validated[]` entries (1024B tolerance) instead of parsing prose. Prose nitpicks become warnings, not blockers. Skips the LLM auditor ladder when filesystem verdict is decisive.
- **QTP-B: status precedence** — when filesystem audit returns OK, the runner does not downgrade worker OK to PART based on auditor prose. Files on disk are hard evidence; prose is soft.

### Reliability
- **QTP-C: parallel-launch jitter** — when multiple `burnless do` invocations fire concurrently from shell backgrounding (`do "..." & do "..." &`), workers register a lockfile in `temp/in_flight/` and apply 0.5–2.5 s random jitter before launching if siblings are detected. Avoids the 529 (overload) cascade observed in the QTP test where 5 workers all hit the API in the same 85-second window. Configurable via `parallel_jitter.{enabled,min_s,max_s}`. Default ON.
- **QTP-D: `burnless read` 3-paths fallback** — `read d###` now tries `capsules/{id}.json` → `temp/{id}.json` → `logs/{id}.log` in order, returning whatever exists. Operator no longer blind on PART/ERR runs that crash before capsule write.

### Cache
- **QTP-F: cacheable prefix layout (opt-in)** — `cfg.cache_prefix.enabled = True` reorders the worker prompt as `[FIXED RUNTIME PREFIX] → [TASK delta] → [chain manifest] → [FIXED OUTPUT CONTRACT]`. Maximizes Anthropic prompt-cache hit rate (`ephemeral_1h`) across sibling delegations in the same project. Default OFF for backwards compatibility with the v0.7.0 layout.

### Privacy
- **`.mcp.json`** added to `.gitignore` and `scripts/public_git_check.sh` filter — MCP server endpoints (Supabase project_ref, etc) are environment-local and must never land in tracked files. Defense-in-depth.

### Tests
- 27 new tests (4 read-3-paths, 8 parallel-jitter, 9 filesystem-first auditor, 7 cache-prefix layout) bringing total to 160/160 passing.

## [0.7.0] — 2026-05-07

### Plugin Protocol v0.7 (stable)
- **`PLUGIN_PROTOCOL.md`** — full public spec for the 8-hook protocol: H1 `pre_worker_prompt`, H2 `post_worker_output`, H3 `session_state_read`, H4 `audit_result_received`, H5 `pre_brain_prompt`, H6 `post_brain_output`, H7 `worker_invoke_override`, H8 `pre_audit_call`. HTTP / stdio / HTTPS transports, 5s timeout per hook, fail-open semantics. Burnless calls plugins; plugins never execute inside Burnless.
- **`src/burnless/plugin_loader.py`** — manifest discovery in `~/.burnless/plugins/*.json`, hook dispatch with timeout, transport abstraction, H3 session-state HTTP server on port 7701.

### Added
- **`tests/test_retry_loop.py`** — 8 tests covering brecha #2: PART/ERR automatic retry loop, retry prompt builder, audit-fix prompt, stale worker doubled timeout (`stale_timeout_s * 2`, capped at 600s), retry_count/retry_status fields surfaced in summary. Closes brecha #2.

### Hardened
- `scripts/public_git_check.sh`: extends private-leak filter with `memory/`, `_design/brecha*.md`, `_design/plugin_protocol_v0_hooks_audit.md`. Prevents accidental publication of internal specs.

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
