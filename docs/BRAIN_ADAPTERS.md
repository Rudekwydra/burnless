# Brain Adapters

Burnless's Brain (the maestro orchestrator) can run on any of four providers.
Worker tier agents (bronze/silver/gold) are independent of this — Brain is the
strategic layer that delegates and audits.

## Providers

| Provider            | `brain_adapter` value | Env var                                | Default model                    | Optional extra        |
|---------------------|-----------------------|----------------------------------------|----------------------------------|-----------------------|
| Anthropic (default) | `anthropic`           | `ANTHROPIC_API_KEY`                    | `claude-sonnet-4-6`              | (built-in)            |
| OpenAI              | `openai`              | `OPENAI_API_KEY`                       | `gpt-4o`                         | `burnless[brain-openai]` |
| Gemini              | `gemini`              | `GEMINI_API_KEY` or `GOOGLE_API_KEY`   | `gemini-2.5-pro`                 | `burnless[brain-gemini]` |
| OpenRouter          | `openrouter`          | `OPENROUTER_API_KEY`                   | `anthropic/claude-sonnet-4`      | `burnless[brain-openai]` (uses OpenAI SDK) |

## Switching provider

In `.burnless/config.yaml`:

```yaml
brain_adapter: openai     # anthropic | openai | gemini | openrouter
```

Then start the chat:

```bash
burnless chat --model gpt-4o
```

The adapter is loaded via `brain_adapters.load_adapter()` and the streaming
implementation lives in `src/burnless/maestro/brain_streams/{provider}.py`.

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

## Common slash commands in chat

- `/model NAME` — switch model within the active adapter
- `/native` — show the native worker agents
- `/keepalive [status|on|off]` — TTL keepalive daemon (Anthropic only — see brecha #7)
- `/workers` — list configured worker tiers (bronze/silver/gold)
- `/help` — full slash-command reference

## Adding a fifth provider

1. Add a `*_adapter()` factory in `src/burnless/brain_adapters.py`
   (mirror the shape of `openai_adapter()`).
2. Wire it into `load_adapter()` switch.
3. Implement `create_stream(client, *, model, system, messages, thinking_kw)`
   in `src/burnless/maestro/brain_streams/PROVIDER.py`. Yield `NormalizedEvent`
   instances with `kind` ∈ {`text_delta`, `think_delta`, `usage`, `done`}.
4. Add tests in `tests/test_brain_adapters.py` covering shape + routing.
5. Document the env var, default model, and cache/thinking behaviour here.
6. Add a `pyproject.toml` optional-dependency block if a new SDK is required.

## Reference

Spec: `_design/brecha6_brain_adapters_spec.md` (private, internal).
