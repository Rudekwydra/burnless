# Using Burnless from your LLM (operator manual)

> **For LLMs:** if you (Claude/GPT/Gemini/etc.) were told by a human to "use the burnless tool", read this file end-to-end before doing anything. It explains what Burnless is, what commands you have, and how to operate without burning the user's quota. The whole point of Burnless is **to save tokens** — so use it accordingly.

---

## What is Burnless

Burnless is a **protocol layer** that separates State Management (capsules) from Cognitive Execution (workers). The invention is the **capsule**: a ~80-character semantic summary of a conversation turn that replaces full transcript replay. This takes the cost curve of a multi-turn agent loop from `Θ(N²)` to `Θ(N)`.

You (the LLM the human is talking to) are the **Maestro**. You don't execute; you delegate. Workers execute via `burnless` commands — they're separate processes (other LLMs, often cheaper) that get only the task, never the conversation history.

## Your three core commands

### 1. `burnless delegate "<task>" [--tier gold|silver|bronze]`

Creates a delegation. The maestro picks the right tier (or you pass it explicitly), writes the worker prompt, and queues the task.

```bash
burnless delegate "refactor the live_runner module to separate polling from display" --tier silver
burnless delegate "decide if it's worth splitting Free and Cloud into distinct repos" --tier gold
burnless delegate "summarize the last 10 commits as bullet points" --tier bronze
```

### 2. `burnless run [<id>]`

Executes the queued delegation. The worker receives the capsule, executes, and returns a structured JSON.

```bash
burnless run d104
burnless run         # runs the most recent queued delegation
```

### 3. `burnless read [<id>]`

Reads the result of a previous delegation.

```bash
burnless read d104
```

## Tiers — choose wisely

| Tier | Use for | Examples |
|---|---|---|
| **gold** | Architecture, decisions with trade-offs, protocol design | "should we split Free and Cloud", "design the session glossary" |
| **silver** | Code, docs, refactor, tests, PRs | "implement encoder v2", "write soul.md" |
| **bronze** | Summarize, classify, read, extract | "summarize the week's commits", "classify these errors" |

**Routing keywords (automatic):**
- decide / architecture / trade-off / design / strategy → **gold**
- implement / write / refactor / fix / test / document → **silver**
- summarize / list / classify / extract / read / compare → **bronze**

The user can pass `--tier` to override. With `BURNLESS_HARDCORE=1`, the maestro **cannot self-upgrade** beyond what the keyword router resolved — so routing rules are guarantees, not hints.

## Worker result

Burnless surfaces the subprocess exit code and stdout. Inspect what the
worker did via `git diff` and the run log.

## Optional: compression filter (saves tokens before they reach the expensive LLM)

If [`examples/plugins/burnless-compress`](../examples/plugins/burnless-compress) is installed (the user can `cp examples/plugins/burnless-compress/manifest.json ~/.burnless/plugins/` and run `python examples/plugins/burnless-compress/server.py`), Burnless automatically compresses verbose prompts before they reach the cloud LLM. Empirically: **2.5× compression** on Portuguese samples with `qwen2.5:7b-instruct` local + telegrafista. See [`bench/COMPRESSION_FINDINGS.md`](../bench/COMPRESSION_FINDINGS.md) for the full method.

You don't do anything special to use it — it runs as a Burnless plugin hook (`pre_worker_prompt`, `pre_brain_prompt`). Just know that your verbose prompt gets a free token diet on the way out.

## How to operate (rules of thumb)

1. **Don't execute terminal commands directly.** Use `burnless delegate` + `burnless run`. Reading files (`Read` tool) is fine to understand the project.

2. **Don't summarize what you just did.** The user reads the diff. End-of-turn summary should be 1–2 sentences max — what changed and what's next.

3. **Never invent worker output.** If a delegation returns `PART`, escalate or ask — don't pretend the work is done.

4. **Aprovação implícita.** If the user doesn't redirect, you proceed. If they correct, you correct fast.

5. **Profile dense, fast input.** The user might send compressed text without spaces or pleasantries (this is intentional — they're saving tokens too). Decode the intent and respond, don't ask them to reformulate.

6. **Português direct, no rodeios.** If the user speaks Portuguese, respond in Portuguese. No emoji excess.

## Common pitfalls

- **Spawning a long-running task on the Maestro side**: don't. The Maestro (your session) is supposed to stay cache-hot. Workers run long tasks; the Maestro plans and delegates only.
- **Routing everything to gold**: expensive and unnecessary. Use bronze for `read/list/classify` even when it feels too simple — that's the point.
- **Treating `PART` as `OK`**: a recurring failure mode. Read the `evidence` field. If empty or vague, the work didn't actually happen.
- **Re-reading capsules instead of delegating**: capsules are dense by design. If you need full context, the delegation should ask the worker for it — don't make Maestro-side calls to "expand" them.

## When to ask the user

- Action is destructive and reversibility is unclear (deleting branches, force-pushing, dropping tables).
- A choice has high blast radius and no obvious default (which DB, which framework, which provider).
- The user gave conflicting instructions across turns.

Otherwise: act, then report briefly.

## Engagement modes (Claude Code integration)

Burnless itself works from any assistant or plain shell — `burnless do/delegate/run` need no hook.
The `off`/`partner`/`on`/`rollover` modes (see the README "Engagement modes" section) are an *optional*
Claude Code convenience: a `/burnless` slash command sets a per-session mode, and a `UserPromptSubmit`
hook reads it and shapes the assistant's behavior each turn.

**1. Slash command** — ships at [`.claude/commands/burnless.md`](../.claude/commands/burnless.md). It emits
a sentinel `__BURNLESS_MODE_CMD__ <arg>`.

**2. Mode state** — stored per session at `~/.burnless/state/session-<id>.mode`. Precedence:
`BURNLESS_OFF=1` (env) → per-session file → `~/.burnless/state/global.on` → default `off`.
`rollover` is the experimental native-chat mode: it keeps `claude` looking like a single chat while
the hook injects a rolling capsule derived from `transcript_path`.

**3. UserPromptSubmit hook** — register in `~/.claude/settings.json`:

```json
{ "hooks": { "UserPromptSubmit": [
  { "hooks": [ { "type": "command", "command": "bash ~/.claude/scripts/burnless_mode_hook.sh" } ] }
] } }
```

The hook (reference logic — keep it small and fail-open):

```bash
#!/usr/bin/env bash
set -uo pipefail
IN=$(cat); P=$(jq -r '.prompt // empty' <<<"$IN"); SID=$(jq -r '.session_id // empty' <<<"$IN")
ST="$HOME/.burnless/state"; mkdir -p "$ST"
emit(){ jq -n --arg c "$1" '{hookSpecificOutput:{hookEventName:"UserPromptSubmit",additionalContext:$c}}'; }
# /burnless [on|partner|rollover|off] sets the mode
if grep -qiE '^[[:space:]]*(/burnless|__BURNLESS_MODE_CMD__)' <<<"$P"; then
  a=$(sed -E 's#^[[:space:]]*(/burnless|__BURNLESS_MODE_CMD__)[: ]*##i' <<<"$P" | tr -dc 'a-z')
  case "$a" in on|partner|rollover|off) [ -n "$SID" ] && echo "$a" > "$ST/session-$SID.mode";
    emit "Burnless mode -> $a (next turn). Confirm to the user, do nothing else.";; 
  *) emit "Show the Burnless mode menu: /burnless on|partner|rollover|off. Current: $(cat "$ST/session-$SID.mode" 2>/dev/null || echo off).";; esac
  exit 0
fi
[ "${BURNLESS_OFF:-}" = "1" ] && exit 0
M=off; [ -n "$SID" ] && [ -f "$ST/session-$SID.mode" ] && M=$(cat "$ST/session-$SID.mode")
[ "$M" = off ] && { [ -f "$ST/global.on" ] && M=on; }
[ "$M" = on ] && emit "[BURNLESS ON] You are the Maestro. Compress intent and ONLY delegate via burnless do/delegate (--tier bronze|silver|gold) with a tight spec + a ## Verify block. Do not write code or edit disk yourself. Read only the capsule (burnless read dXXX), never the raw log. Answer from the capsule, briefly."
# partner = no injection (you keep reasoning + delegate where it helps); rollover = rolling capsule injection;
# off = no-op
exit 0
```

`partner` deliberately injects nothing — the assistant stays itself and delegates at its own discretion;
`on` pins it to the Maestro role; `rollover` keeps the native chat but feeds a rolling capsule from the
transcript into each turn; `off` is a pure no-op. Adjust the `on` text to taste.

The shipped template at [`templates/scripts/burnless_mode_hook.sh`](../templates/scripts/burnless_mode_hook.sh)
contains the full `rollover` helper.

## Reference

- [`PROTOCOL.md`](../PROTOCOL.md) — full Burnless protocol
- [`PLUGIN_PROTOCOL.md`](../PLUGIN_PROTOCOL.md) — plugin hooks (v0.7)
- [`bench/COMPRESSION_FINDINGS.md`](../bench/COMPRESSION_FINDINGS.md) — empirical compression numbers
- [`MATH.md`](../MATH.md) — derivation of `Θ(N²) → Θ(N)`
