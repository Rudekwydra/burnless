# /burnless

Activate Burnless compression for this Claude Code session.

Compresses recent conversation into a capsule and anchors future responses to it.
Cache-warm: the glossary block is byte-identical every call — providers cache it
automatically. From turn 2 onward, prefix costs drop ~90-99%.

## Steps

1. Check burnless is installed: `burnless --version`. If missing: `pip install burnless` and stop.

2. Write the last 20 turns to a temp file:
   ```
   /tmp/burnless_<timestamp>.txt
   ```
   Format:
   ```
   [user]: <message>
   [assistant]: <response>
   ```

3. Compress:
   ```
   burnless compress --file /tmp/burnless_<timestamp>.txt
   ```
   Capture the capsule ID, chars, ratio, and saved path.

4. Display the capsule:
   ```
   Burnless capsule — <capsule_id> — <orig>c → <compressed>c (<ratio>%)
   ```

5. Tell the user:
   > Capsule active. Anchoring to capsule above — not the full history.
   > Type anything to continue.

6. **From this turn forward**: treat the capsule as authoritative context.
   If something isn't in the capsule, ask for clarification — do not hallucinate.

7. Every 10 turns, auto-update:
   ```
   burnless compress --file /tmp/burnless_update_<timestamp>.txt
   ```
   Notify: `[burnless] capsule updated — <new_id> (<ratio>%)`

## Resume in new session

```
burnless brain --capsule <path>
```

## See all capsules

```
ls .burnless/sessions/
```
