# Always-Hot Cache: keeping the Burnless maestro (Claude CLI) session prompt-cache HOT

- **Date:** 2026-06-14
- **Author:** Gold worker (d693), audit + design only — NOT implemented
- **Scope:** v1 = Burnless used FROM the Claude CLI. Claude Code is the maestro; it shells out to `burnless do/delegate/run` for workers.
- **Requirement (Roberto):** the maestro session must stay cache-hot *intrinsically* — no dependence on the user remembering to do anything.
- **Grounding:** every claim below is grounded in the actual hook scripts (`/Users/roberto/.claude/scripts/*.sh`), `/Users/roberto/.claude/settings.json`, and Anthropic prompt-cache semantics. Anything that depends on Claude Code's internal context-assembly order is flagged **[NEEDS-CC-CONFIRM]**.

---

## 0. Anthropic prompt-cache semantics (the rules everything is judged against)

Three rules decide everything:

1. **Cache read (0.1×) is earned ONLY on a byte-identical prefix**, reused within TTL, up to and including the nearest `cache_control` breakpoint at or before the reuse point. One differing byte anywhere in the prefix → cache miss from that byte onward → those tokens re-bill as `cache_creation` (1.25×).
2. **Cache is prefix-anchored and append-only friendly.** Adding NEW bytes at the *tail* of the message array does NOT invalidate the prefix that precedes them. The previously-cached prefix still matches byte-for-byte, so it earns `cache_read`; only the appended delta is `cache_creation`. This is normal and unavoidable for any new turn.
3. **Thrash = rewriting bytes that sit INSIDE an already-cached prefix.** If volatile content occupies a *fixed early position* that is re-rendered with different bytes every turn, then everything from that position onward misses every turn. That is the failure the earlier benchmark exposed (`cache_creation` dominating, `cache_read` tiny).

**Corollary — the single decisive question for each injection:** does it land at the **tail** (current turn, append-only → safe) or at a **fixed early/prefix position that is re-rendered each turn** (→ thrash)? Volatility *alone* is harmless if it only ever appears once at the tail and is then frozen into history. Volatility is fatal only when combined with a fixed prefix slot.

TTL: default 5 min; the maestro plan uses the 1h extended-cache window (per capsule `cache-no-plano-mensal`). TTL governs whether a *reused* prefix is still warm; it does not change the prefix-identity rule.

---

## 1. INJECTION MAP

Every source that puts bytes into the maestro session context, classified.

| # | Source | Hook event | Bytes per turn | Stable / Volatile | Lands at | Cache impact |
|---|--------|-----------|----------------|-------------------|----------|--------------|
| 1 | `~/.claude/CLAUDE.md` (global) | auto-load | identical all session | **byte-stable** | system/early-prefix | safe (frozen at start) `[NEEDS-CC-CONFIRM: re-read per turn?]` |
| 2 | `~/antigravity/CLAUDE.md` (workspace) | auto-load | identical all session | **byte-stable** | system/early-prefix | safe (frozen at start) `[NEEDS-CC-CONFIRM]` |
| 3 | auto-memory `MEMORY.md` index | auto-load | **can change mid-session** (Stop hook `memory_heartbeat.sh` may rewrite it) | **stable-but-mutable** | system/early-prefix | **THRASH RISK if re-read per turn** `[NEEDS-CC-CONFIRM]` |
| 4 | `burnless_epoch_session.sh` rolling memory | SessionStart | once, at start | **byte-stable** (reads stored `seed.md` / `epoch read` chain; no timestamp/counter injected at read time — verified) | conversation, early | safe within session |
| 5 | `burnless_session_seed.sh` pending seed | SessionStart | once, consume-once (deletes pointer) | byte-stable for that read; absent next session | conversation, early | safe within session |
| 6 | `forgetless_pinned_seed.sh` pinned capsules | SessionStart | once, at start | **byte-stable within session** (pin list + 10-day decay window; changes only across days) | conversation, early | safe within session |
| 7 | `burnless_mode_hook.sh` `[BURNLESS ON]` text | UserPromptSubmit | **every turn** | **byte-stable string** (constant literal, mode `on`) | **tail** (current user turn) | safe (append-only, identical) |
| 8 | `burnless_policy_inject.sh` policy reminder | UserPromptSubmit | **every turn** | **byte-stable string** (constant heredoc) | **tail** (current user turn) | safe (append-only, identical) |
| 9 | `forgetless_auto_rank.sh` top-3 capsules | UserPromptSubmit | **every turn** | **VOLATILE** (top-3 vary by query; even same query drifts via `rec`/recency score — confirmed by live diff) | **tail** (current user turn) | **safe IFF tail-only** `[NEEDS-CC-CONFIRM that additionalContext is NOT re-rendered at a fixed early slot]` |
| 10 | `todoless_capture.sh` | UserPromptSubmit (async) | every turn | **emits NOTHING to context** (writes a forgetless capsule, `exit 0`, no `additionalContext`) | — | **zero cache impact** |
| 11 | statusline `statusline_burnless.sh` | statusLine | every turn | volatile | **not in model context** (terminal UI only) | **zero cache impact** |

Notes:
- Items 7, 8, 9 all fire on the *same* UserPromptSubmit and all emit `additionalContext`. Claude Code concatenates multiple hooks' `additionalContext` for the turn. The decisive fact is **where that concatenated block is placed in the message array**, not that there are three of them.
- Item 10 (`todoless_capture`) is frequently mis-listed as an injector. It is not — it has no `additionalContext` output path. Drop it from cache reasoning.

---

## 2. THE VERDICT

**The maestro session is, by the documented mechanics, cache-hot-SAFE for the per-turn hooks — provided Claude Code places UserPromptSubmit `additionalContext` at the conversation tail (the current user message), which is its documented behavior. The earlier benchmark thrash was almost certainly a WORKER / replay artifact (a prompt rebuilt with a changing prefix each call), NOT the interactive maestro.**

Reasoning, per the rules in §0:

- **`forgetless_auto_rank` (item 9) is genuinely volatile** — live diff of two queries returned entirely different capsules, and the `rec` recency term drifts even for a fixed query over time. So it *is* the scariest candidate.
- **But volatility at the TAIL does not thrash.** On turn N the auto-rank block is appended to turn N's user message. On turn N+1 it is *frozen history* — it is never re-rendered, never rewritten. Turn N+1 appends a *new, different* auto-rank block at the new tail. The prefix through end-of-turn-N is byte-identical to what it was, so it earns `cache_read`. Each turn pays `cache_creation` only on its own new tail delta — which is exactly what *any* new user prompt costs. There is no extra invalidation.
- This is the same reason a normal chat where the user types a different question every turn stays hot: new bytes at the tail, old prefix frozen.

**So the per-turn hooks do NOT thrash the maestro — under the tail-placement assumption.** That assumption is the one thing that flips the verdict, hence flagged **[NEEDS-CC-CONFIRM]**. If Claude Code were to re-emit hook `additionalContext` at a *fixed early slot* (e.g. re-injected as the first context block above all prior turns each turn), then item 9's volatility would invalidate the whole conversation prefix every turn → full thrash. I have no evidence CC does this, and the documented model is per-message tail attachment, but it is not provable from the hooks alone.

**The real, non-hypothetical thrash risk is item 3, NOT item 9:** the auto-memory `MEMORY.md` index sits in the *system/early-prefix* region (stable, cached), but `memory_heartbeat.sh` (a Stop hook) can **rewrite it mid-session**. If CC re-reads CLAUDE.md/MEMORY.md into the prefix on each turn, a mid-session rewrite changes early-prefix bytes → every subsequent turn misses the whole prefix until the next breakpoint. That is the textbook thrash pattern (volatile content at a fixed early position). Whether CC re-reads or freezes-at-start is **[NEEDS-CC-CONFIRM]**.

### Dollar / impact framing (order-of-magnitude, to size the bet)

- A warm maestro turn re-reads a large stable prefix (system + CLAUDE.md + tools + frozen history, easily 30k–100k+ tokens) at **0.1×**.
- A thrashing turn re-bills that same prefix at **1.25×** as `cache_creation` — a **~12.5× cost multiplier on the prefix portion of every turn**, silently. Over a long maestro session that is the difference between cents and dollars per session, and it compounds with context growth.
- The asymmetry is why this is worth nailing: the *fix* is cheap; the *silent* failure mode is expensive and invisible without reading the JSONL.

---

## 3. THE GUARANTEE DESIGN — stay hot REGARDLESS of usage

The guarantee has two independent legs. Leg A is already satisfied by current structure (keep it from regressing). Leg B is the one with a live risk.

### Leg A — keep all volatile per-turn injection at the TAIL (append-only)

**Invariant:** *No volatile content may ever occupy a fixed early/prefix position that is re-rendered per turn. Volatile content is allowed only at the conversation tail, where it freezes into history after one turn.*

Current state already complies: items 7/8/9 are UserPromptSubmit `additionalContext`, which attaches to the current user turn (tail). The design action is to **lock this invariant and prove it**, not to change the wiring:

- `forgetless_auto_rank` (volatile) — keep as UserPromptSubmit only. **Never** move it to SessionStart-with-reinjection or to any mechanism that re-renders it above prior turns. (Note the codebase already learned this once: `forgetless_pinned_seed.sh`'s header comment records moving the *pinned* block OUT of per-turn UserPromptSubmit INTO SessionStart precisely "to stop re-writing this stable block every turn, which broke prompt-cache prefix matching." That fix is correct and must not be reverted.)
- The stable per-turn strings (items 7/8) are harmless either way because they are byte-identical, but they too belong at the tail.

This leg costs nothing and is intrinsic: as long as the hooks stay UserPromptSubmit-emitting-additionalContext, usage pattern is irrelevant — the user can ask anything in any order and the prefix stays frozen.

### Leg B — freeze the system/early-prefix region for the life of the session

The prefix region (CLAUDE.md, workspace CLAUDE.md, MEMORY.md, SessionStart seeds) must be **byte-stable from session start to session end**. Two concrete rules:

1. **Do not mutate prefix-region files mid-session.** `memory_heartbeat.sh` (Stop hook) and anything else that writes `MEMORY.md`, `~/.claude/CLAUDE.md`, or `~/antigravity/CLAUDE.md` must either (a) be proven to NOT feed back into the live prefix mid-session, or (b) defer writes so the *current* session's prefix bytes never change. The safe, intrinsic design: **writes to prefix-region files take effect on the NEXT session, never the current one.** (Heartbeat already runs at Stop, i.e. end of turn — the question is only whether CC re-reads the file on the following turn.)
2. **SessionStart seeds are conversation-region, injected once, frozen.** Items 4/5/6 already comply. The only requirement is that the seed text contain **no per-read volatile bytes** (timestamps, counters, run-ids). **Verified:** `burnless_epoch_session.sh` injects the stored `seed.md` / `epoch read` chain verbatim with a constant `"## Rolling memory (carry-forward)\n\n"` header — no timestamp added at injection. `seed.md` on disk is static markdown. Good.

### On `/clear` reseed and "byte-stable on reseed"

`/clear` wipes the *conversation* but the *system prefix* (CLAUDE.md/tools/MEMORY.md) is rebuilt identically → it earns `cache_read` against the still-warm pre-`/clear` prefix automatically, regardless of the seed. The SessionStart seed lands in the (now-empty) conversation region as fresh bytes — it cannot match a pre-`/clear` conversation because that conversation is gone. So **"byte-stable seed on reseed" buys at most a marginal conversation-region hit and is NOT the lever for staying hot.** The lever is Leg B (system prefix identical) + Leg A (tail-only volatility). I call this out to avoid over-investing in seed-byte-stability that the cache model won't reward much. (The seed should still be deterministic for *correctness*/dedup reasons, just not for cache reasons.)

### Keepalive within TTL

Optional, low-value for an *interactive* maestro: a human typing keeps turns inside the 1h extended-TTL window naturally. A keepalive ping only matters if the session idles > TTL and you want the *next* human turn to hit warm. It is a nicety, not part of the guarantee, and must not be implemented as a per-turn prefix mutation (that would violate Leg A). If wanted, it is a no-op tail turn, never a prefix rewrite. **Not recommended for v1.**

---

## 4. IMPLEMENTATION SPEC

**The audit found NO code change is required to make the maestro cache-safe** — the structure is already correct under the tail-placement assumption, and the SessionStart seed is already volatile-byte-free. The only gap is **proof**: two assumptions (`[NEEDS-CC-CONFIRM]`) gate the verdict, and they are answerable by measurement, not by editing hooks. Therefore this spec is a **measurement + guard spec**, not a feature change. Implement only if you want the guarantee *verified and regression-proofed*.

### 4.1 Measurement experiment (do this FIRST — it decides whether any code change is even needed)

Read the live session JSONL and compute, per assistant turn, `cache_read_input_tokens` vs `cache_creation_input_tokens` from the `message.usage` field.

- **HOT (no thrash):** `cache_read` is large and grows with the conversation; `cache_creation` per turn is small (~ size of the new tail delta only).
- **THRASH:** `cache_creation` spikes to roughly full-context size every turn; `cache_read` stays tiny. If thrash appears, correlate the spike turns with (a) auto-rank firing (→ implicates tail-placement assumption / item 9) or (b) a `memory_heartbeat.sh` MEMORY.md rewrite (→ implicates item 3).

Session JSONL location: the `transcript_path` passed to the hooks (under `~/.claude/projects/<project-hash>/*.jsonl`). Each `assistant` record carries `message.usage.{cache_read_input_tokens, cache_creation_input_tokens, input_tokens}`.

### 4.2 Files (only if 4.1 shows thrash or you want regression guards)

- **If item 3 thrash confirmed** — `/Users/roberto/.claude/scripts/memory_heartbeat.sh`: gate any write to `MEMORY.md` / `CLAUDE.md` so it cannot alter the *current* session's prefix bytes (write to a staging file consumed at next SessionStart, or skip if the only consumer is the live prefix). Exact change depends on what heartbeat writes — read it before specifying.
- **If item 9 thrash confirmed** (i.e. CC does NOT tail-place) — this is a Claude Code behavior, not a Burnless bug; the mitigation is to stop emitting volatile `additionalContext` per turn and instead expose auto-rank as an on-demand tool/slash the maestro calls, so volatile bytes only ever enter at the tail via a tool result. Do NOT design this until 4.1 proves it's needed.
- **Regression guard (cheap, recommended regardless)** — a lint/test asserting `forgetless_auto_rank.sh` is wired ONLY under `UserPromptSubmit` (never SessionStart) and that no SessionStart seed contains a timestamp/counter pattern.

### HARD PROHIBITIONS (for whoever implements)

- Do **NOT** move `forgetless_auto_rank` (or any volatile producer) into SessionStart, or into any mechanism that re-renders its output above prior conversation turns. (Reverting the `forgetless_pinned_seed.sh` split would reintroduce the known break.)
- Do **NOT** add timestamps/counters/run-ids to any SessionStart seed or to the `[BURNLESS ON]` / policy strings.
- Do **NOT** mutate `~/.claude/CLAUDE.md`, `~/antigravity/CLAUDE.md`, or `MEMORY.md` in a way that changes the *current* session's prefix bytes mid-session.
- Do **NOT** add a per-turn "keepalive" that rewrites any prefix content.
- Do **NOT** implement a fix for item 9 before the 4.1 measurement proves CC is not tail-placing — it is likely a non-issue.

### Verify

```sh
test -f /Users/roberto/.claude/scripts/forgetless_auto_rank.sh || exit 1
python3 -c "import json,sys; s=json.load(open('/Users/roberto/.claude/settings.json')); sys.exit(0 if 'forgetless_auto_rank' in str(s['hooks']['UserPromptSubmit']) else 1)" || exit 1
python3 -c "import json,sys; s=json.load(open('/Users/roberto/.claude/settings.json')); sys.exit(1 if 'forgetless_auto_rank' in str(s['hooks']['SessionStart']) else 0)" || exit 1
grep -q "additionalContext" /Users/roberto/.claude/scripts/burnless_epoch_session.sh || exit 1
grep -L "date +" /Users/roberto/antigravity/burnless/.burnless/epochs/_rolling/seed.md | grep -q seed.md || exit 1
test -f /Users/roberto/antigravity/burnless/_design/always_hot_cache_2026_06_14.md || exit 1
```

---

## 5. HONEST LIMITS

1. **Tail-placement of `additionalContext` is assumed, not proven from the hooks.** The whole "per-turn hooks don't thrash" verdict rests on Claude Code attaching UserPromptSubmit `additionalContext` to the current user turn (tail), not re-rendering it at a fixed early slot. This is the documented model and the likely truth, but it is a Claude-Code internal. **Only the §4.1 JSONL measurement can confirm it.** Until then the verdict is "safe by mechanics, pending one CC-internals confirmation."

2. **CLAUDE.md / MEMORY.md re-read cadence is a CC-internal.** Whether CC freezes these at session start or re-reads them per turn determines whether item 3 (mid-session `MEMORY.md` rewrite) thrashes. Not knowable from the hooks. Same measurement answers it.

3. **Worker-side conversation cache cannot be kept hot — this is inherent, not fixable.** `burnless do/run` workers are stateless `claude -p` (or local) invocations. Each invocation is a fresh process with no conversation history. The Anthropic prompt cache for a worker can only cache the **CLI's own stable system prefix** (the worker system prompt + tool defs), and only if a prior worker call used a byte-identical system prefix within TTL. There is **no conversation to carry across delegations** — by design, the worker's value is statelessness. Concretely:
   - What CAN be kept hot: the worker's *system prefix*. To maximize this, every worker dispatch should use the **same byte-identical system prompt + tool set** (e.g. fixed `--system-prompt` / fixed tool allowlist per tier), so back-to-back dispatches within TTL hit `cache_read` on that prefix. Capsule `burnless-maestro-system-prompt-tools-empty-2026-05-28` already points at the cheapest stable-prefix recipe (`--system-prompt` + empty tools beats `--append` + `disallowedTools`). Keeping that prefix *constant per tier* is the only worker-side lever.
   - What CANNOT: the *task content* of each delegation is unique (that's the point), so the per-call body is always `cache_creation`. You cannot cache "the conversation" across workers because there isn't one.
   - The honest framing (already captured in `seed.md` 002): the CLI replay methodology "só cacheia o prefixo do sistema" — system-prefix only — and a fair Burnless-vs-replay comparison needs the **Anthropic API with explicit `cache_control` on both arms**, not the CLI. So worker-side "always hot" is bounded to the system prefix and nothing more. State this plainly to set expectations: **the guarantee in this doc is for the MAESTRO session; workers get system-prefix caching only, by design.**

4. **TTL idle gaps.** If the maestro idles past the (1h extended) TTL, the next turn pays `cache_creation` to rewarm — unavoidable without a keepalive, and a keepalive is explicitly out of scope for v1 (§3). This is a latency/cost blip on resume, not ongoing thrash.

5. **This audit read the hooks and the cache rules; it did not run a live multi-turn maestro and diff the JSONL.** That run (§4.1) is the one piece of empirical confirmation outstanding and is the recommended next action before declaring victory.
