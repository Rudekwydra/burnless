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

## Worker output contract (the JSON envelope)

The canonical worker output contract is a **JSON envelope**. Every worker returns exactly these fields:

| Field | Meaning |
|---|---|
| `status` | `OK` \| `PART` \| `ERR` \| `BLK` — the verdict. |
| `kind` | `execution` (changed/ran/checked something — needs evidence) or `thought` (planning/design only). |
| `summary` | One-line what-happened. |
| `files_touched` | Array of absolute paths the worker created/modified. |
| `validated` | What was actually verified (test output, grep results, the `## Verify` block outcome). |
| `evidence` | **Literal** commands run + their literal outputs. **Never abbreviated, never paraphrased** — the audit reads the raw text to confirm the work happened. |
| `issues` | Problems, gaps, or caveats found. |
| `next` | Suggested follow-up, or empty. |

`evidence` is the load-bearing field. If it is empty or vague, treat the result as if the work did **not** happen — even when `status` says `OK`. The audit loop checks `kind: execution` reports against the filesystem (file existence, sizes) before the Maestro is allowed to treat them as done; `kind: thought` reports skip the filesystem check so design work doesn't loop as a false `PART`.

Beyond the envelope, Burnless also surfaces the subprocess exit code and stdout. You can still inspect what the worker did via `git diff` and the run log.

## Optional: compression filter (saves tokens before they reach the expensive LLM)

If [`examples/plugins/burnless-compress`](../examples/plugins/burnless-compress) is installed (the user can `cp examples/plugins/burnless-compress/manifest.json ~/.burnless/plugins/` and run `python examples/plugins/burnless-compress/server.py`), Burnless automatically compresses verbose prompts before they reach the cloud LLM. Empirically: **2.5× compression** on Portuguese samples with `qwen2.5:7b-instruct` local + telegrafista. See [`bench/COMPRESSION_FINDINGS.md`](../bench/COMPRESSION_FINDINGS.md) for the full method.

You don't do anything special to use it — it runs as a Burnless plugin hook (`pre_worker_prompt`, `pre_brain_prompt`). Just know that your verbose prompt gets a free token diet on the way out.

## Compression (how prior session state is encoded)

Separate from the optional compress *plugin* above, Burnless has built-in capsule compression that controls how the prior session state is preserved in the capsule history the Maestro reads. It is **fixed and faithful** — ~150 chars/field, ≤12 list items, full paths, dedupe only. There is no per-run knob; everything is preserved (the anchor stays revisable and phantom-completion has no compression vector).

Workers are always **epistemically pure** — they receive a clean task without the Maestro's debate history, so compression only affects what the Maestro itself sees between turns.

## Capsule encryption status (read before assuming privacy)

The capsule envelope (compression Layer 3) uses a **session key held in RAM** by default. It is **NOT enterprise-grade encryption in v0.x** — do not treat capsules as a privacy guarantee against a determined party. Capsule format v2: `burnless:v2:<session_id>:<key_id>:<base64_ciphertext>`.

Privacy in Burnless is a property of **where each component runs** (local vs cloud encoder/Maestro/workers), not of the envelope crypto. If you need real encryption guarantees, that is out of scope for the current implementation — modes `redact`, `audit`, `opaque`, `burnkey` are planned, not yet implemented.

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

## Engagement modes (generic — any LLM host)

The `off`/`on` engagement modes are a **generic** mechanism, implemented in
`burnless_mode_hook.sh` and usable from **any LLM host**, not a Claude-Code-only feature. The hook reads a
per-session mode and shapes the assistant's behavior each turn; any host that can run a shell hook on user
input (or otherwise read the mode file) can adopt them. Burnless's core (`burnless do/delegate/run`) needs
no hook at all — the modes are an optional behavior layer on top. (Legacy `partner`/`rollover` are removed;
both coerce to `on`.)

Claude Code is simply the **reference integration**: a `/burnless` slash command sets the per-session mode and a
`UserPromptSubmit` hook invokes `burnless_mode_hook.sh`. The example below shows that wiring, but the mode logic
itself is host-agnostic.

**1. Slash command** — ships at [`.claude/commands/burnless.md`](../.claude/commands/burnless.md). It emits
a sentinel `__BURNLESS_MODE_CMD__ <arg>`.

**2. Mode state** — stored per session at `~/.burnless/state/session-<id>.mode`. Precedence:
`BURNLESS_OFF=1` (env) → per-session file → `~/.burnless/state/global.on` → default `off`.
`on` is the Maestro mode and carries rolling memory (epoch `Stop`/`SessionStart` hooks keep context
O(N) and survive `/clear`); `off` is a pure no-op.

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
# /burnless [on|off] sets the mode (legacy partner|rollover coerce to on)
if grep -qiE '^[[:space:]]*(/burnless|__BURNLESS_MODE_CMD__)' <<<"$P"; then
  a=$(sed -E 's#^[[:space:]]*(/burnless|__BURNLESS_MODE_CMD__)[: ]*##i' <<<"$P" | tr -dc 'a-z')
  case "$a" in partner|rollover) a=on;; esac
  case "$a" in on|off) [ -n "$SID" ] && echo "$a" > "$ST/session-$SID.mode";
    emit "Burnless mode -> $a (next turn). Confirm to the user, do nothing else.";; 
  *) emit "Show the Burnless mode menu: /burnless on|off. Current: $(cat "$ST/session-$SID.mode" 2>/dev/null || echo off).";; esac
  exit 0
fi
[ "${BURNLESS_OFF:-}" = "1" ] && exit 0
M=off; [ -n "$SID" ] && [ -f "$ST/session-$SID.mode" ] && M=$(cat "$ST/session-$SID.mode")
case "$M" in partner|rollover) M=on;; esac
[ "$M" = off ] && { [ -f "$ST/global.on" ] && M=on; }
[ "$M" = on ] && emit "[BURNLESS ON] You are the Maestro. Compress intent and ONLY delegate via burnless do/delegate (--tier bronze|silver|gold) with a tight spec + a ## Verify block. Do not write code or edit disk yourself. Read only the capsule (burnless read dXXX), never the raw log. Answer from the capsule, briefly."
# on = Maestro injection + rolling memory (via epoch Stop/SessionStart hooks); off = no-op
exit 0
```

`on` makes the assistant the Maestro and (via the epoch hooks) carries rolling memory across turns and
`/clear`; `off` is a pure no-op. Adjust the `on` text to taste. Rolling memory itself comes from the
separate `Stop`/`SessionStart` epoch hooks, not this hook.

## Reference

- [`PROTOCOL.md`](../PROTOCOL.md) — full Burnless protocol
- [`PLUGIN_PROTOCOL.md`](../PLUGIN_PROTOCOL.md) — plugin hooks (v0.7)
- [`bench/COMPRESSION_FINDINGS.md`](../bench/COMPRESSION_FINDINGS.md) — empirical compression numbers
- [`MATH.md`](../MATH.md) — derivation of `Θ(N²) → Θ(N)`
