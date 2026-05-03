# Burnless compression intermediary — Codex instructions

When the user asks you to activate Burnless or compress the session, follow
these steps.

## Activation

1. Check burnless is installed:
   ```
   burnless --version
   ```
   If not found: tell the user to run `pip install burnless` and stop.

2. Write recent conversation turns to a temp file:
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

4. Show the user the capsule ID, compression ratio, and saved path.

5. From this point: anchor all responses to the capsule content.
   Do not hallucinate from pre-capsule context.

## Resume in new session

```bash
burnless brain --capsule .burnless/sessions/<capsule_id>.capsule
```

## Update capsule

Every 10 turns, re-run compress with new turns appended and notify the user.

## Notes

- Capsule IDs are random by design — structurally undetectable as burnless output.
- All state lives in `.burnless/sessions/` — local, no cloud, no telemetry.
- Provider-agnostic: works with any model configured in `.burnless/config.yaml`.
