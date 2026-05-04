# /burnless-chat

Open Burnless persistent chat with real prefix-cache warmth.

The system anchor (Burnless glossary + project context) is byte-identical every turn.
Turn 1 writes to cache. Turn 2+ reads from cache at ~10x cheaper than fresh input.
Cache savings are shown inline after each response.

## Usage

```
/burnless-chat
```

## Steps

1. Check burnless is installed: `burnless --version`. If missing: `pip install burnless` and stop.

2. Check `.burnless/` exists. If missing: `burnless init` and stop.

3. Open chat:
   ```
   burnless shell
   ```
   Then type `/chat` at the prompt to enter persistent chat mode.

4. The shell will show:
   - `[cache written — next turn ~10x cheaper]` on turn 1
   - `[cache hit — saved ~XX% input cost]` on turns 2+

## Tiers in chat

Chat defaults to gold tier (Opus). Change with `:silver` or `:bronze` at the prompt.

## Exit

`/exit` or `Ctrl+C`

## Notes

- Anchor size: ~1891 tokens (Burnless glossary + protocol reference). Clears
  the ≥1024 token threshold required for Anthropic prefix caching.
- Cache TTL: 1 hour (extended-cache-ttl beta header enabled).
- Privacy: L0 by default (all cloud). For L2+, run Maestro locally.
