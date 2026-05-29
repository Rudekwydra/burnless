# cached_worker started model=claude-opus-4-7 timeout=900s
# started_at: 2026-05-29T03:01:35.384497+00:00
I'll audit the codebase read-only, grounding every claim in file:line refs.
[tool] bash({"command": "cd /Users/roberto/antigravity/burnless && ls -la && cat PROTOCOL.md | head -200"})
[tool_result] total 544
drwxr-xr-x@ 53 roberto  staff   1696 May 28 18:31 .
drwxr-xr-x  47 roberto  staff   1504 May 28 18:53 ..
-rw-r--r--@  1 roberto  staff   6148 May  9 21:13 .DS_Store
drwxr-xr-x@ 41 roberto  staff   1312 May 28 23:01 .burnless
drwxr-xr-x@  6 roberto  staff    192 May 25 11:22 .claude
drwxr-x
[tool] bash({"command": "which forgetless && forgetless get burnless-shared-preamble-cache-architecture-2026-05-28 2>&1 | head -200"})
[tool_result] /Users/roberto/.local/bin/forgetless
# burnless-shared-preamble-cache-architecture-2026-05-28

_Warm cache serve encoder+maestro+worker via PREÂMBULO compartilhado cacheado (≥2048 tok, inclui moral-block) + sufixo de papel com 2 breakpoints. 1 cache literal funde papéis (NÃO); preâmbulo+sufixo resol
[tool] bash({"command": "forgetless get burnless-per-layer-tier-e-dor-coletiva-2026-05-28 2>&1 | head -100"})
[tool_result] # burnless-per-layer-tier-e-dor-coletiva-2026-05-28

_Per-layer tier substitui toggle direct/protocol (viram presets); a dor coletiva 'burnless emburrece minha conversa' resolve com encoder=opus|passthrough + worker=gold + maestro=off_

**tags:** burnless, architecture, paper-opportunity, roberto-fl
[tool] bash({"command": "forgetless get burnless-warm-cache-pipeline-not-implemented-2026-05-28 2>&1 | head -100 && echo \"---SEP---\" && forgetless get burnless-maestro-under-isolated-system-bug-2026-05-28 2>&1 )
[tool_result] # burnless-warm-cache-pipeline-not-implemented-2026-05-28

_INVESTIGACAO (Roberto flag): preambulo ≥2048 compartilhado desenhado e DESIGN-ONLY no pipeline. chat_mode tem _CACHE_PAD ≥2048 mas orfao do pipeline; warm_session.build_project_brief esvaziado p/ 105 tok (sub-threshold, nao cacheia); maestr
Now I have the concept. Let me audit the actual code.
[tool] bash({"command": "cd /Users/roberto/antigravity/burnless && cat templates/hooks/burnless_compact_haiku.sh"})
[tool_result] #!/usr/bin/env bash
# Burnless layer-1 semantic compactor — Claude Code UserPromptSubmit hook.
#
# Reads hook input JSON from stdin, extracts user prompt, calls Haiku via
# `claude -p` to compact into a JSON telegram envelope {i, r, m}, returns
# Claude Code hook output JSON with additionalContext.

[tool] bash({"command": "cd /Users/roberto/antigravity/burnless && cat src/burnless/maestro_runner.py"})
[tool_result] """Maestro runner — invokes the burnless conducting layer (camada 2) isolated.

The Maestro is a pure dispatcher: it receives a compacted telegram of intent and
emits a single compacted telegram routing decision. It NEVER executes work, plans,
or inspects files. Stateless per call (no --resume); the
[tool] bash({"command": "cd /Users/roberto/antigravity/burnless && cat src/burnless/warm_session.py"})
[tool_result] """Warm session pool for Burnless workers — GLOBAL, cross-project.

Workers invoked via `claude -p --resume <warm_uuid> --fork-session` inherit a
cached prefix from a SINGLE warm session shared across every project and every
window. Boot warmer pays the cold once per user (~$0.03), every subsequent

[tool] bash({"command": "cd /Users/roberto/antigravity/burnless && wc -l src/burnless/live_runner.py src/burnless/cached_worker.py src/burnless/warm_session_codex.py src/burnless/chat_mode.py src/burnless/config.)
[tool_result] 1282 src/burnless/live_runner.py
     512 src/burnless/cached_worker.py
     424 src/burnless/warm_session_codex.py
     729 src/burnless/chat_mode.py
     323 src/burnless/config.py
    3270 total
[tool] bash({"command": "cd /Users/roberto/antigravity/burnless && sed -n '1,130p' src/burnless/chat_mode.py"})
[tool_result] """
Burnless persistent chat mode.

/chat in the shell enters this mode. Two backends:

1. SDK backend (default when ANTHROPIC_API_KEY is set):
   - System prompt with plan+memory marked cache_control ephemeral 1h
   - Real prefix-cache warmth: 2nd turn costs ~10x less than 1st
   - Conversation his
[tool] bash({"command": "cd /Users/roberto/antigravity/burnless && sed -n '330,400p' src/burnless/chat_mode.py"})
[tool_result] if plan:
        project_context += f"[project plan]\n{plan.strip()}\n"
    if memory_blob:
        project_context += f"\n[user memory — read-only]\n{memory_blob}\n"

    blocks: list[dict] = [
        {
            "type": "text",
            "text": glossary,
            "cache_control": {"type":
[tool] bash({"command": "cd /Users/roberto/antigravity/burnless && grep -n \"build_command\\|build_cmd\\|claude.*-p\\|setting-sources\\|strict-mcp\\|no-session-persistence\\|disable-slash\\|exclude-dynamic\\|appe)
[tool_result] 101:        # Capture session_id so the caller can write per-tier resume state.
303:    session_holder: list[str] = []  # Captures session_id from the result event for per-tier resume.
311:    #      (--resume <warm> --fork-session). The warm carries the project
313:    #   2. Otherwise fall back to
[tool] bash({"command": "cd /Users/roberto/antigravity/burnless && sed -n '290,400p' src/burnless/live_runner.py"})
[tool_result] if shutil.which(command[0]) is None:
        raise AgentError(
            f"agent binary not found in PATH: {command[0]} (configured for {agent_cfg.get('name')})"
        )
    if mode not in {"plain", "watch", "quiet", "full", "minimal", "brief"}:
        mode = "plain"

    started = datetime.now
[tool] bash({"command": "cd /Users/roberto/antigravity/burnless && sed -n '1,100p' src/burnless/cached_worker.py"})
[tool_result] """Burnless CachedWorker — executes delegations via Anthropic API with explicit cache_control.

This is the SDK path. The CLI path (`claude -p`) also has prefix-cache warmth —
Claude Code injects cache_control automatically with ephemeral_1h TTL — so this
module is not "the way to get cache." It is 
[tool] bash({"command": "cd /Users/roberto/antigravity/burnless && grep -n \"system_blocks\\|cache_control\\|glossary\\|build_system\\|_load_glossary\\|_CACHE_PAD\\|worker_role\" src/burnless/cached_worker.py | h)
[tool_result] 1:"""Burnless CachedWorker — executes delegations via Anthropic API with explicit cache_control.
4:Claude Code injects cache_control automatically with ephemeral_1h TTL — so this
6:  - system blocks with cache_control={type: "ephemeral", ttl: "1h"}
14:  block 0: glossary.md  (ttl=1h)
15:  block 1: w
[tool] bash({"command": "cd /Users/roberto/antigravity/burnless && sed -n '240,300p' src/burnless/cached_worker.py"})
[tool_result] if pkg_design.is_dir():
        return pkg_design
    return local  # return non-existent path; _load_text uses fallback strings


def build_system_blocks(
    *, project_root: Path, burnless_root: Path, memory_index: Path | None = None
) -> list[dict[str, Any]]:
    """Build a single cached system 
[tool] bash({"command": "cd /Users/roberto/antigravity/burnless && grep -n \"CACHE_MIN_TOKENS\\|_CHARS_PER_TOKEN\\|_FALLBACK_GLOSSARY\\|_FALLBACK_WORKER_ROLE\" src/burnless/cached_worker.py | head -20"})
[tool_result] 217:CACHE_MIN_TOKENS = 1024
219:_CHARS_PER_TOKEN = 3.5
221:_FALLBACK_GLOSSARY = """\
227:_FALLBACK_WORKER_ROLE = """\
261:    glossary = _load_text(design_dir / "glossary.md", _FALLBACK_GLOSSARY)
262:    worker_role = _load_text(design_dir / "worker_role.md", _FALLBACK_WORKER_ROLE)
281:    estimated
[tool] bash({"command": "cd /Users/roberto/antigravity/burnless && sed -n '210,240p' src/burnless/cached_worker.py"})
[tool_result] except Exception:
        return fallback


# Anthropic requires a minimum prefix size for cache to activate.
# Sonnet/Opus 3.5+: 1024 tokens. Haiku 3.5: 2048. Claude 4.x: 1024.
# We target 1024 as the safe minimum for Sonnet/gold tiers.
CACHE_MIN_TOKENS = 1024
# Conservative chars-per-token ratio f
[tool] bash({"command": "cd /Users/roberto/antigravity/burnless && ls _design/maestro_v1/ && wc -c _design/maestro_v1/glossary.md _design/maestro_v1/worker_role.md 2>/dev/null"})
[tool_result] README.md
brain_role.md
codex_dia1_prompt.md
codex_dia2_prompt.md
codex_dia3_prompt.md
glossary.md
schemas.md
worker_role.md
    4199 _design/maestro_v1/glossary.md
    5046 _design/maestro_v1/worker_role.md
    9245 total
Good — 9245 chars ≈ 2640 tokens. The cached_worker block crosses 2048. Now warm_session_codex:
[tool] bash({"command": "cd /Users/roberto/antigravity/burnless && grep -n \"preamble\\|brief\\|build_\\|cacheable\\|_CACHE\\|PAD\\|≥\\|>=2048\\|1024\\|2048\" src/burnless/warm_session_codex.py | head -40"})
[tool_result] 5:get cached server-side. We seed the cache with a build_project_brief prefix,
77:    """Two-level listing to push the cached prefix past the 1024-char threshold
133:def build_project_brief(project_root: Path) -> str:
134:    """Build the cacheable preamble sent as byte-identical prefix in every cod
[tool] bash({"command": "cd /Users/roberto/antigravity/burnless && sed -n '70,170p' src/burnless/warm_session_codex.py"})
[tool_result] path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _top_level_tree(root: str, max_entries: int = 80) -> str:
    """Two-le
[tool] bash({"command": "cd /Users/roberto/antigravity/burnless && sed -n '1,60p' src/burnless/config.py && echo \"---\" && grep -n \"resolve_layer_models\\|layer\\|encoder\\|maestro\" src/burnless/config.py | he)
[tool_result] from __future__ import annotations
from pathlib import Path
import yaml

DEFAULT_CONFIG: dict = {
    "project_name": "Project",
    "language": "pt-BR",
    "mode": "local_first",
    "agents": {
        # Tiers are quality/cost bands, not vendors. Map any provider/CLI here:
        # Claude gold, 
[tool] bash({"command": "cd /Users/roberto/antigravity/burnless && sed -n '295,323p' src/burnless/config.py"})
[tool_result] if isinstance(silver_rules, list):
                for kw in legacy:
                    if kw not in silver_rules:
                        silver_rules.append(kw)


HAIKU_MODEL = "claude-haiku-4-5-20251001"

_PRESET_RESOLUTIONS = {
    "protocol": {"encoder": HAIKU_MODEL, "maestro": HAIKU_MODEL},
 
Now token-sizing the relevant prompts:
[tool] bash({"command": "cd /Users/roberto/antigravity/burnless && python3 -c \"\nimport re\n# Maestro system prompt\nwith open('src/burnless/maestro_runner.py') as f:\n    src = f.read()\nm = re.search(r'MAESTRO)
[tool_result] maestro chars: 1827 est_tokens(~3.5cpt): 522
claude warm brief chars: 423 est_tokens: 121
encoder fixed chars: 275 est_tokens: 79
chat _CACHE_PAD chars: 3454 est_tokens: 987
cached_worker combined chars: 9162 est_tokens: 2618
[tool] bash({"command": "cd /Users/roberto/antigravity/burnless && python3 -c \"\n# chat_mode loads glossary + pad. glossary alone:\ng = open('_design/maestro_v1/glossary.md').read()\nprint('glossary alone chars:)
[tool_result] glossary alone chars: 4112 est_tokens: 1175
chat_mode glossary+pad chars: 7566 est_tokens: 2162
[tool] bash({"command": "cd /Users/roberto/antigravity/burnless && grep -n \"passthrough\\|disable.slash\\|strict-mcp\\|no-session\\|exclude-dynamic\\|--system-prompt\\|append-system\" templates/hooks/burnless_co)
[tool_result] templates/hooks/burnless_compact_haiku.sh:48:# passthrough → encoder disabled, no compaction (no-op, fail-open)
templates/hooks/burnless_compact_haiku.sh:49:if [ "$ENCODER_MODEL" = "passthrough" ]; then
src/burnless/maestro_runner.py:84:        "--exclude-dynamic-system-prompt-sections",
src/burnles
[tool] bash({"command": "cd /Users/roberto/antigravity/burnless && grep -n \"no-session-persistence\\|fork-session\" src/burnless/warm_session.py"})
[tool_result] 3:Workers invoked via `claude -p --resume <warm_uuid> --fork-session` inherit a
99:        "--fork-session`. Your role and behavior are determined entirely by the "
365:        "--fork-session",
409:    return ["--resume", state["uuid"], "--fork-session"]
Confirmed:
- warm_session.init (line ~190-217) is missing `--no-session-persistence` (but has strict-mcp, disable-slash, exclude-dynamic, setting-sources, append-system-prompt). Note: init creates the warm session, so `--no-session-persistence` would defeat resume — that's why it's absent (intentional).
- live_runner.py:376-388 ADDS those 4 flags to the worker subprocess (post-fork).
- maestro_runner.build_command (l 76-87): has setting-sources, exclude-dynamic, system-prompt, tools=""; MISSING --no-session-persistence, --strict-mcp-config, --disable-slash-commands. Confirmed today-finding.
- encoder hook (compact_haiku.sh l 67-72): has --permission-mode bypassPermissions, --allowedTools '', --output-format json. MISSING --setting-sources, --strict-mcp-config, --disable-slash-commands, --exclude-dynamic-system-prompt-sections, --no-session-persistence. And the prompt is passed via `-p` (i.e., as USER message, not `--system-prompt`), so the byte-stable prefix is just stdin + flags, not a cacheable system prompt. The user input is glued INSIDE the prompt → never byte-stable → never caches.

Let me confirm encoder hook user-prompt injection vs `--system-prompt`:
[tool] bash({"command": "cd /Users/roberto/antigravity/burnless && grep -n \"system-prompt\\|append-system\\|-p\\b\" templates/hooks/burnless_compact_haiku.sh"})
[tool_result] 5:# `claude -p` to compact into a JSON telegram envelope {i, r, m}, returns
66:TELEGRAM="$(printf '%s' "$COMPACT_PROMPT" | timeout 4 /opt/homebrew/bin/claude -p \
Confirmed: encoder pipes `COMPACT_PROMPT` (instructions + user input concatenated) to `claude -p` stdin → that's a user message, varies per call, no cacheable system prefix at all.
[tool] bash({"command": "cd /Users/roberto/antigravity/burnless && grep -rn \"encoder_prompt\\|haiku_encoder\\|encoder_runner\\|run_encoder\" src/burnless/ | head"})
[tool_result] 
No encoder runner module — encoder lives only in the shell hook. Now confirm the `_top_level_tree` call site (today-finding says it's unused in claude warm):
[tool] bash({"command": "cd /Users/roberto/antigravity/burnless && grep -n \"_top_level_tree\\|_detect_languages\\|_safe_git_branch\" src/burnless/warm_session.py"})
[tool_result] 107:def _top_level_tree(root: str, max_entries: int = 80) -> str:
124:def _safe_git_branch(root: str) -> str:
137:def _detect_languages(root: str) -> list[str]:
Confirmed: `_top_level_tree`, `_safe_git_branch`, `_detect_languages` are defined but **never called** in warm_session.py (only used in warm_session_codex.py). build_project_brief returns just the 423-char neutral string. Today-finding verified.
[tool] bash({"command": "cd /Users/roberto/antigravity/burnless && grep -n \"build_project_brief\\b\" src/burnless/warm_session.py"})
[tool_result] 86:def build_project_brief(project_root: Path) -> str:
196:    brief = build_project_brief(project_root)
[tool] bash({"command": "cd /Users/roberto/antigravity/burnless && grep -rn \"from .codec.glossary_loader\\|glossary_loader\" src/burnless/ | head"})
[tool_result] src/burnless/maestro/dispatcher.py:17:from ..codec.glossary_loader import load_glossary
src/burnless/maestro/core.py:10:from ..codec.glossary_loader import load_glossary
Binary file src/burnless/maestro/__pycache__/core.cpython-314.pyc matches
Binary file src/burnless/maestro/__pycache__/dispatcher.
[tool] bash({"command": "cd /Users/roberto/antigravity/burnless && wc -l src/burnless/codec/glossary_loader.py && head -50 src/burnless/codec/glossary_loader.py"})
[tool_result] 74 src/burnless/codec/glossary_loader.py
from __future__ import annotations

import logging
from pathlib import Path

from .. import paths as paths_mod

log = logging.getLogger(__name__)

_CACHE_TEXT: str | None = None
_CACHE_SIG: tuple[tuple[str, int | None, int | None], ...] | None = None


def lo
Good — `glossary_loader.load_glossary` returns the core `_design/maestro_v1/glossary.md` (1175 tok). chat_mode pads it with `_CACHE_PAD` (987 tok) to ≈2162 tok. cached_worker pads glossary+worker_role to ≈2618 tok. Both real, both ≥2048. They use DIFFERENT prefixes → don't share cache.

Final check — what existing tests assert about maestro_runner.build_command (for "keep tests green" prohibition):
[tool] bash({"command": "cd /Users/roberto/antigravity/burnless && grep -rn \"maestro_runner\\|build_command\\|MAESTRO_SYSTEM_PROMPT\" tests/ 2>/dev/null | head -20"})
[tool_result] tests/test_maestro_runner.py:1:from burnless import maestro_runner as mr
tests/test_maestro_runner.py:16:def test_build_command_has_isolation_flags():
tests/test_maestro_runner.py:17:    cmd = mr.build_command('{"intent":"x"}', "claude-haiku-4-5-20251001")
tests/test_maestro_runner.py:32:def test_bu
[tool] bash({"command": "cd /Users/roberto/antigravity/burnless && sed -n '14,40p' tests/test_maestro_runner.py"})
[tool_result] def test_build_command_has_isolation_flags():
    cmd = mr.build_command('{"intent":"x"}', "claude-haiku-4-5-20251001")
    joined = " ".join(cmd)
    assert "--setting-sources" in cmd
    assert "project,local" in cmd
    assert "--exclude-dynamic-system-prompt-sections" in cmd
    assert "--system
Test asserts presence of certain flags + absence of `--append-system-prompt` and `--disallowedTools`. Adding new flags is compatible. Good.

I have enough. Producing the deliverable.

---

gld :: OK aud burnless v0.9 cache+isolation vs concept :: 3 layers audited, gaps grounded in file:line, today-findings VERIFIED with one nuance

## Evidence (literal code refs)

**Encoder — `templates/hooks/burnless_compact_haiku.sh`**
- l.66-72: `claude -p --model "$ENCODER_MODEL" --permission-mode bypassPermissions --allowedTools '' --output-format json` — that's the full flag set.
- l.51-58: prompt is `COMPACT_PROMPT = "Você é compactador telegrafo. … [USER INPUT]\n$USER_PROMPT"` piped via stdin to `claude -p`, i.e. consumed as user message; there is NO `--system-prompt`/`--append-system-prompt` at all.
- Consequence: the byte-stable instruction block (~275 chars / ~79 tok) is glued to varying user text → no stable cacheable prefix. Even if it were stable, 79 tok ≪ 2048 Haiku floor → `cache_creation=0`.

**Maestro — `src/burnless/maestro_runner.py`**
- l.20-50: `MAESTRO_SYSTEM_PROMPT` triple-quoted string, measured 1827 chars / ~522 tok.
- l.76-87 `build_command`:
  ```
  [claude, -p, telegram,
   --model, model,
   --setting-sources, project,local,
   --exclude-dynamic-system-prompt-sections,
   --system-prompt, MAESTRO_SYSTEM_PROMPT,
   --tools, "",
   --output-format, json]
  ```
- Missing vs worker: `--no-session-persistence`, `--strict-mcp-config`, `--disable-slash-commands`. Today-finding `burnless-maestro-under-isolated-system-bug-2026-05-28` CONFIRMED literally.
- 522 tok system prompt < 2048 Haiku floor (and even < 1024 Sonnet/Opus floor) → never caches at Haiku; would cache only if maestro=opus/sonnet AND ≥1024 (still no — 522<1024).
- `run_maestro` (l.94) runs `cwd="/tmp"` — good for CLAUDE.md isolation, but no flag stopping MCP/session-persistence side-effects.

**Worker warm (claude) — `src/burnless/warm_session.py`**
- l.86-104 `build_project_brief`: returns a 423-char (~121 tok) static neutral string. `project_root` arg accepted "for signature compatibility, NOT used" (l.94-95).
- l.107-122 `_top_level_tree`, l.124-134 `_safe_git_branch`, l.137-150 `_detect_languages`: defined but **never referenced** anywhere in this file (grep result above: definitions only). Today-finding "`_top_level_tree` defined but called" → CONFIRMED unused; the "pad to 1024" comment in l.108 is stale.
- l.196-217 `init` cmd:
  ```
  --model, --permission-mode bypassPermissions,
  --allowedTools Read,Edit,Write,Bash,Glob,Grep,LS,
  --session-id new_uuid,
  --strict-mcp-config,
  --disable-slash-commands,
  --setting-sources project,local,
  --exclude-dynamic-system-prompt-sections,
  --append-system-prompt brief,
  --output-format json,
  ack
  ```
  No `--no-session-persistence` — INTENTIONAL: init must persist so subsequent `--resume <uuid>` works (worker forks). The today-finding is right that the worker warm brief is sub-threshold; nuance: `--no-session-persistence` is correctly omitted at init.
- `--append-system-prompt` (not `--system-prompt`) means brief is APPENDED to claude code's stock system → the base system is part of the cached prefix too. That base likely > 2048 tok in practice, which is presumably why Haiku/Sonnet warm actually DID register cache_creation in earlier benches. But the 121-tok brief itself is too small to dominate the cache key, and the stock base is opaque/version-drift-prone → fragile.
- Worker subprocess flags added at `src/burnless/live_runner.py:374-388`: idempotently appends `--no-session-persistence --strict-mcp-config --disable-slash-commands --exclude-dynamic-system-prompt-sections` and `--setting-sources project,local` (l.383-384), then `--permission-mode bypassPermissions` for claude (l.390-391). Worker IS the most isolated layer.

**Codex warm — `src/burnless/warm_session_codex.py`**
- l.133-153 `build_project_brief`: returns `"=== PROJECT CONTEXT (cacheable preamble) ===\nProject: …\nRoot: …\nBranch: …\nLanguages: …\n\nTop-level layout:\n{_top_level_tree(root)}\n\n=== END CONTEXT ===\n\n"`. ACTUALLY calls `_top_level_tree` (l.144). For a project of moderate size this clears OpenAI's 1024-char threshold (header in l.137).
- However: this is project-specific, mutates with `git branch` / file moves (`cache_validity` at l.313-321 hashes brief and prunes on drift). NOT byte-identical across users/projects → no cross-tenant prewarm; only intra-project warmth.

**chat_mode (the working reference) — `src/burnless/chat_mode.py`**
- l.105-130 `_CACHE_PAD`: 3454 chars (~987 tok) of canonical protocol reference.
- l.336-349 `blocks`: glossary block with `cache_control={"type":"ephemeral","ttl":"1h"}`, then optional project_context block with same cache_control. Two breakpoints.
- l.353-365 `_load_glossary`: `text = load_glossary(p["root"].parent)` (returns core 4112 chars / 1175 tok), then `if len(text) < 8000: text += _CACHE_PAD` → ~7566 chars / 2162 tok. Crosses 2048-tok Haiku floor.
- This is the ONLY place in the repo that actually realizes the designed pattern: byte-identical core ≥2048 tok with explicit `cache_control`. Today-finding "órfão do pipeline" CONFIRMED.

**cached_worker (SDK alternative) — `src/burnless/cached_worker.py`**
- l.214-216 `CACHE_MIN_TOKENS = 1024` — wrong for Haiku (real floor 2048); silent under-target if worker is Haiku.
- l.245-297 `build_system_blocks`: concatenates `glossary.md` + `worker_role.md` + runtime_context (9162 chars / 2618 tok) into ONE block with single `cache_control`. Adds pad if estimate < 1024.
- This DOES cache, but it's a third standalone prefix (not the chat_mode one, not warm_session's). Three independent caches.

**Per-layer tier — `src/burnless/config.py`**
- l.302-323: `HAIKU_MODEL`, `_PRESET_RESOLUTIONS = {"protocol":{"encoder":HAIKU,"maestro":HAIKU}, "direct":{"encoder":"passthrough","maestro":"off"}}`, `resolve_layer_models(cfg)` returns `{"encoder":…, "maestro":…}` with explicit cfg overriding preset. Clean. Worker tier already lives in `cfg["agents"]`. The mechanism is sound and orthogonal to caching.

**Glossary loader — `src/burnless/codec/glossary_loader.py`**
- l.14-43: returns core `_design/maestro_v1/glossary.md` byte-identical; detects tenant glossary but explicitly does NOT merge ("intentionally not merged yet, so the returned core remains byte-identical and cache-friendly", l.16-17). Perfect anchor for a shared preamble.

**Today-finding verification (nuances)**
- "Maestro 456 tok não cacheia": MEASURED in code → 522 tok (the 456 figure was probably a tokenizer estimate). Conclusion identical: below the 1024 floor, will not cache on Opus/Sonnet; below 2048 Haiku floor.
- "Warm brief 423 chars ≈105 tok": exact (423 chars / 3.5 = 121 tok by our estimator; same order, conclusion identical).
- "_top_level_tree defined but called": CORRECTED — defined but NEVER called in `warm_session.py` (claude). It IS called in `warm_session_codex.py:144`. Today-finding scoped to claude path is right.
- "Encoder zero isolamento": CONFIRMED. No `--strict-mcp-config`, no `--disable-slash-commands`, no `--no-session-persistence`, no `--exclude-dynamic-system-prompt-sections`, no `--setting-sources`. AND no `--system-prompt` slot at all — instructions ride in the user message.

---

## DELIVERABLE 1 — Per-layer gap analysis

### Layer 1: ENCODER (`templates/hooks/burnless_compact_haiku.sh`)

| Dim | Reality (ref) | Concept | Gap |
|---|---|---|---|
| (a) `--no-session-persistence` | absent (l.66-72) | required | MISSING |
| (a) `--strict-mcp-config` | absent | required | MISSING — user MCPs (~tens of k tok) inject nondeterministically |
| (a) `--disable-slash-commands` | absent | required | MISSING |
| (a) `--exclude-dynamic-system-prompt-sections` | absent | required | MISSING — cwd/git/env vary → cache thrash |
| (a) `--setting-sources project,local` | absent | required | MISSING — user `settings.json` (incl. forgetless auto-rank hook) leaks into call |
| (a) fixed `--system-prompt` | absent — instructions in user msg (l.51-72) | required | STRUCTURAL: no system prompt at all → no cacheable prefix slot |
| (a) `--tools ''` | `--allowedTools ''` (l.69) | equivalent | OK (different flag name, same effect) |
| (b) cacheable prefix size | ~79 tok fixed + variable user text | ≥2048 Haiku floor | NOT caching: 79≪2048 AND not byte-stable |
| (c) shared preamble? | standalone, embedded in shell heredoc | shared with maestro+worker | NOT shared |
| (d) risks | cost (every call pays full input), nondeterminism (MCP/settings inject), leak (forgetless hook auto-ranks user prompt into context), no warm amortization across N parallel chats |

### Layer 2: MAESTRO (`src/burnless/maestro_runner.py`)

| Dim | Reality (ref) | Concept | Gap |
|---|---|---|---|
| (a) `--no-session-persistence` | absent (l.76-87) | required | MISSING — 71 stale /tmp sessions in a day per today-finding |
| (a) `--strict-mcp-config` | absent | required | MISSING — MCP schemas inject (16k–66k tok variance) |
| (a) `--disable-slash-commands` | absent | required | MISSING |
| (a) `--exclude-dynamic-system-prompt-sections` | present (l.84) | required | OK |
| (a) `--setting-sources project,local` | present (l.83) | required | OK |
| (a) fixed `--system-prompt` | present (l.85) | required | OK — but content is layer-specific (gap c) |
|
# stop_reason=max_tokens at turn 35
# ended_at: 2026-05-29T03:04:32.094826+00:00
# usage: input=84826 output=10344 cache_read=1163523 cache_write=56287
