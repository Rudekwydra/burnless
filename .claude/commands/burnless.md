# /burnless

Activate Burnless compression intermediary for this chat session.

Compresses the current conversation into a capsule, anchors future responses
to it, and lets the old history become a passive scroll.

## Steps

1. Check burnless is installed: `burnless --version`. If not found, tell the
   user to run `pip install burnless` and stop.

2. Write the last 20 turns of this conversation to a temp file:
   ```
   /tmp/burnless_<timestamp>.txt
   ```
   Format each turn as:
   ```
   [user]: <message>
   [assistant]: <response>
   ```

3. Run:
   ```
   burnless compress --file /tmp/burnless_<timestamp>.txt
   ```
   Capture the output line (capsule ID, chars, ratio, saved path).

4. Read the saved capsule file and display it to the user inside a code block
   with the header:
   ```
   Burnless capsule — <capsule_id> — <orig>c → <compressed>c (<ratio>%)
   ```

5. Tell the user:
   > Capsule saved. From this point I am anchoring my responses to the capsule
   > above — not to the full conversation history. The history above is your
   > scroll. Type anything to continue.

6. **From this turn forward**: treat the capsule as the authoritative context.
   Do not reference the verbose history above it. If the user asks about
   something not in the capsule, say so and ask them to clarify — do not
   hallucinate from stale context.

7. Every 10 user turns after activation, automatically run:
   ```
   burnless compress --file /tmp/burnless_update_<timestamp>.txt
   ```
   …with the new turns appended, and silently update the capsule. Notify the
   user with one line: `[burnless] capsule updated — <new_id> (<ratio>%)`.

## Notes

- The capsule session ID is random (`secrets.token_hex(6)`) — this is by
  design. Capsules are structurally indistinguishable from plain text files.
- To resume in a new session: `burnless brain --capsule <path>`
- To see all saved capsules: `ls .burnless/sessions/`
