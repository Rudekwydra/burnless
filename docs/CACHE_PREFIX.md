# Cacheable prompt prefix (`cache_prefix`)

Burnless v0.7.1 adds an opt-in prompt layout that maximizes Anthropic
`ephemeral_1h` prompt-cache hit rate across **sibling delegations** in
the same project (e.g. running `burnless do --tier silver "..."` five
times in a row for related tasks).

## What it does

When `cache_prefix.enabled = True` in `.burnless/config.yaml`, the
worker prompt is structured as four contiguous segments:

```
┌─ [FIXED PREFIX]   ← cacheable. identical for every sibling.
│  Burnless Runtime Context
│  - working dir, burnless dir, memory hint
│  - search instructions, BLK guidance
│
│  [TASK delta]      ← variable per delegation
│  the actual delegation text
│
│  [chain manifest] ← variable per chain (lazy capsule paths)
│
└─ [FIXED SUFFIX]   ← cacheable. identical for every sibling.
   Output contract reminder (status enum, JSON schema)
```

## Why it matters

The default v0.7.0 layout puts the variable task **first** and the
runtime context **after** — meaning every sibling delegation has a
different prefix, and Anthropic's prompt cache cannot match.

With `cache_prefix.enabled = True`, the first ~500–1000 tokens are
identical across siblings. After the first delegation primes the cache,
subsequent ones in the 1-hour TTL window read those tokens from cache
instead of paying full input price. Empirically that's a ~10× cost
reduction on the cached portion.

## How to enable

```yaml
# .burnless/config.yaml
cache_prefix:
  enabled: true
```

That's the whole switch. No code changes, no breaking changes — opt-in
by design so existing projects continue working with the v0.7.0 layout.

## When NOT to enable

- **Single-delegation projects.** No siblings = nothing to cache against.
- **Projects with rapidly changing config.** If your `project_root` /
  `burnless_root` / `memory_index` change every run, the prefix is no
  longer stable and you lose the win without any downside.
- **Worker on a non-Anthropic provider with no prompt cache.** OpenAI
  and Gemini have automatic caching but it's based on prefix repetition;
  the layout still helps but the gain is smaller than with Anthropic's
  explicit `cache_control`.

## Measuring the effect

Live measurement requires the SDK path with explicit usage logging:

```bash
# legacy layout
burnless do --tier silver "task A"
burnless do --tier silver "task B"
burnless do --tier silver "task C"
# observe cache_read_input_tokens via cached_worker logs

# enable cache_prefix in .burnless/config.yaml, repeat
```

Look for `cache_read_input_tokens` climbing from ~0 in legacy to
~500–1000+ in the cache-prefix layout for the second and subsequent
delegations.

The plain `claude -p` subprocess path auto-caches user messages with
`ephemeral_1h` TTL anyway; the cache_prefix gain there is real but
harder to measure (Claude Code doesn't expose per-call usage detail
to the operator).

## Spec source

- Issue F in `QTP_OPERATIONAL_TEST_2026-05-06.md` (private, internal)
- Implementation in `src/burnless/cli.py:_build_cacheable_runtime_prefix`
  and `src/burnless/cli.py:_with_runtime_context`
- Tests in `tests/test_cache_prefix.py` (7 tests covering layout
  ordering, prefix stability, default-off invariant, suffix presence)

## Compatibility

- v0.7.0 → v0.7.1: zero impact unless you flip the flag (default False)
- v0.7.1 → v0.7.0: capsules and delegations remain compatible; only the
  in-process prompt composition changes
- Plugin Protocol v0.7 hooks (H1 `pre_worker_prompt`, H5 `pre_brain_prompt`)
  receive the composed prompt as before — plugins see the new layout
  when the flag is on, but the JSON shape is unchanged
