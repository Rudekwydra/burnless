# Maestro Adapters

Burnless's Maestro orchestrator can run on any of four providers.
Worker tier agents (bronze/silver/gold) are independent of this — Maestro is the
strategic layer that delegates and audits.

## Providers

| Provider            | `maestro_adapter` value | Env var                                | Default model                    | Optional extra        |
|---------------------|-----------------------|----------------------------------------|----------------------------------|-----------------------|
| Anthropic (default) | `anthropic`           | `ANTHROPIC_API_KEY`                    | `claude-opus-4-8`               | (built-in)            |
| OpenAI              | `openai`              | `OPENAI_API_KEY`                       | `gpt-4o`                         | `burnless[brain-openai]` |
| Gemini              | `gemini`              | `GEMINI_API_KEY` or `GOOGLE_API_KEY`   | `gemini-2.5-pro`                 | `burnless[brain-gemini]` |
| OpenRouter          | `openrouter`          | `OPENROUTER_API_KEY`                   | `anthropic/claude-sonnet-4`      | `burnless[brain-openai]` (uses OpenAI SDK) |

## Switching provider

In `.burnless/config.yaml`:

```yaml
maestro_adapter: openai     # anthropic | openai | gemini | openrouter (brain_adapter still accepted for back-compat)
```

The adapter applies on the next turn. There is no standalone CLI chat: the
Maestro runs in your Claude Code session (`/burnless on`) or the desktop app.

The adapter is loaded via `maestro_adapters.load_adapter()`, which returns a
declarative `MaestroAdapter` dataclass (key, models, capabilities, env var) from
the matching `*_adapter()` factory in `src/burnless/maestro_adapters.py`.

## Cache + thinking semantics by provider

| Provider    | Prompt cache visibility                                          | Thinking events                                  |
|-------------|------------------------------------------------------------------|--------------------------------------------------|
| Anthropic   | `cache_creation_input_tokens` + `cache_read_input_tokens` (explicit, with `cache_control`) | Native (server-side reasoning)                   |
| OpenAI      | `prompt_tokens_details.cached_tokens` (inferred, automatic)      | Not surfaced (o1/o3 reasoning is hidden)         |
| Gemini      | `cached_content_token_count` (inferred, automatic)               | Synthetic event when `part.thought` is set       |
| OpenRouter  | `prompt_tokens_details.cached_tokens` (inferred, varies by model) | `delta.reasoning` field on supporting models     |

Anthropic is the only adapter with **explicit** cache control via
`cache_control` blocks. Other providers report cache hits but cache state is
managed automatically based on prefix repetition.

## Common slash commands in the Maestro chat (desktop app)

- `/model NAME` — switch model within the active adapter
- `/native` — show the native worker agents
- `/keepalive [status|on|off]` — TTL keepalive daemon (Anthropic only — see brecha #7)
- `/workers` — list configured worker tiers (bronze/silver/gold)
- `/help` — full slash-command reference

## Adding a fifth provider

1. Add a `*_adapter()` factory in `src/burnless/maestro_adapters.py`
   (mirror the shape of `openai_adapter()`).
2. In that factory, return a `MaestroAdapter` declaring `key`, `kind`,
   `api_key_env`, `default_model`, `models`, and a `MaestroCapabilities`
   (single_shot / interactive / streaming / delegation / …). Set
   `base_url` and `supports_thinking` if relevant.
3. Wire the new `kind` into the `load_adapter()` switch.
4. Add tests in `tests/test_maestro_adapters.py` covering shape + routing.
5. Document the env var, default model, and cache/thinking behaviour here.
6. Add a `pyproject.toml` optional-dependency block if a new SDK is required.

## Reference

Spec: `_design/brecha6_maestro_adapters_spec.md` (private, internal).
