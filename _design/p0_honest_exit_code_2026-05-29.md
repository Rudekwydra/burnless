# P0 — Honest Exit Code (runner re-executes the spec's VERIFY block)

**Date:** 2026-05-29
**Author:** gold (d492, READ-ONLY design)
**Status:** design — ready to delegate to silver/bronze
**Problem case:** d175 (nutri) — worker returned `OK:dXXX`, but `grep -c markedSignsJson = 0`; the prisma edit + `db push` never happened. The OK seal is pure worker self-report. Automatic auditor is RETIRED (v0.8/v0.9). Worker is optimized to *close* the task, not to be skeptical against it.

---

## 1. Field-verified current behavior (grounded in file:line)

### 1.1 How status (OK/PART/ERR/BLK) is derived today

The authoritative status is **the worker's self-reported JSON envelope** — the process exit code is only a *fallback when no envelope is present*.

- `src/burnless/cli.py:920` — `extracted_json = deleg_mod.extract_result_json(result.get("stdout", ""))`.
- `cli.py:921-922` — if a JSON envelope exists: `summary = normalize_worker_envelope(extracted_json)`; `summary["status"]` is taken **verbatim from the worker text**. The real process `returncode` is never consulted in this branch.
- `cli.py:946-971` — **only when there is NO envelope** does status come from the OS exit code: `_status = "OK" if result["returncode"] == 0 else "ERR"` (`cli.py:957`). This is the v0.8 "no envelope is fine" path.
- `src/burnless/delegations.py:142` `extract_result_json()` — grabs the **last** ```` ```json ```` fenced block (or trailing `{...}`) and `json.loads` it. Pure text trust.

### 1.2 How the `run` command exit code propagates

- `cli.py:1248` — `status_str = summary.get("status", "?")`.
- `cli.py:1266` — `return 0 if status_str == "OK" else 1`. **The CLI exit code is a direct function of the worker's self-reported status string.** A phantom OK ⇒ exit 0 ⇒ Maestro believes the work is done.
- The run logic lives in `_cmd_run_body(args)` (def at `cli.py:~691`; `cmd_run` at `cli.py:679` wraps it). `deleg_text` is read once at `cli.py:713` (`deleg_text = deleg_path.read_text(...)`) and is in scope for the whole body.

### 1.3 Spec / DoD / envelope parsing — is there a VERIFY block today?

- **No.** The delegation template (`src/burnless/delegations.py:9-32`) has only `## Goal`, `## Task`, `## Success criteria`, `## Report kind`. The output contract is appended at `cli.py:254`. There is no machine-extractable check block.
- `src/burnless/delegation_parse.py` holds the pure spec parsers (`parse_chain_from_delegation`, `parse_goal_from_delegation`, etc.) — stdlib-only, regex/`split`. **This is the correct home for a new `extract_verify_block`.**
- `src/burnless/spec_validator.py` is the precedent: a pure regex module over spec text, re-exported and called by `cmd_delegate`. The verify extractor mirrors this shape.

### 1.4 Are `evidence`/`validated` machine-runnable today?

**No — they are freeform text lists.** `normalize_worker_envelope` (`src/burnless/codec/decoder.py:234-247`) only `_coerce_to_list`s them; no command semantics. `extract_test_status` (`delegation_parse.py:63-78`) *string-greps* `evidence`+`validated` for `"pytest"`/`"passed"`/`"failed"` — proof they are prose, not executable. So we cannot trust them as checks, and we cannot trust the worker to supply checks either (see §2).

### 1.5 Retry loop + `do` atomic path

- Retry loop: `cli.py:1088-1178`. `_cur_status in ("PART","ERR")` ⇒ retry (`_max_attempts`, default `retry.max_attempts=1`). On a retry that returns OK, `cli.py:1163` `if _new_status == "OK": summary=_r_sum; break`. **OK breaks the loop unconditionally** — this is the exact spot a verified-OK gate must hook.
- `cmd_do` (`cli.py:2374`) = `cmd_delegate` + build `run_args` + run through the same `_cmd_run_body`. **It inherits the gate for free** — no separate change.
- Worker + re-exec both run with `cwd=root.parent` (the real project root; `cli.py:839`). The re-exec must use the **same cwd** so paths resolve identically.

---

## 2. Design decision #1 — where the check block comes from (THE key decision)

**Chosen: (a) author-controlled `## Verify` block in the spec, extracted and executed by the runner. REJECT (b) worker-emitted `checks`.**

The whole failure mode is that **the worker is the untrusted party** — it is optimized to close the task. If the worker supplies the checks (option b, `checks:[...]` in the envelope), a phantom worker emits a trivially-passing check (`echo ok`, `true`) and we are back to square one: self-report wearing a costume. **Trust must originate from the spec author (Maestro/Roberto), never from the worker.**

Therefore: the **spec author** writes a `## Verify` section containing a fenced shell block. The **runner** extracts it (worker never sees it as authoritative) and **re-executes it after the worker exits**, in the worker's cwd. The real aggregate exit code becomes the authoritative gate on OK.

### Convention (deterministic, easy to author)

```
## Verify

```sh
test "$(grep -c markedSignsJson prisma/schema.prisma)" -ge 1
python -c "import json,sys; json.load(open('config.json'))"
```
```

Rules the extractor enforces:
- Section header matches `^##+\s*Verify\b` (case-insensitive).
- The **first** fenced block under it whose info-string is one of `sh|bash|shell|verify` (or bare ```` ``` ````) is the check block.
- Each **non-blank, non-`#`-comment line** is one check command, executed in order.
- Empty/absent ⇒ `None` ⇒ feature is a **no-op** (backward compat, §4).

### Trade-off table

| Option | Worker-prompt change | Spec-format change | Runner change | Trust model | Verdict |
|---|---|---|---|---|---|
| (a) `## Verify` block in spec | none (1 optional info line) | +1 optional section | extract + re-exec | **author-controlled ✅** | **CHOSEN** |
| (b) `checks:[...]` in envelope | mandatory new field + retrain | none | re-exec | worker-controlled ❌ (phantom can fake) | reject |
| (c) parse `evidence` strings as commands | none | none | heuristic extract | worker-controlled + ambiguous parse | reject |

(a) wins on trust *and* on blast radius: it is a **runner-only + opt-in spec-format** change. No envelope schema change, no worker retraining required for correctness.

---

## 3. Design decision #2 — where re-exec runs and how it overrides status

- **cwd:** `root.parent` — identical to the worker (`cli.py:839`). Same filesystem state the worker left behind. No sandbox: the point is to observe the *real* side effects the worker claims to have made.
- **Execution:** sequential, fail-fast. For each command:
  `subprocess.run(cmd, shell=True, cwd=root.parent, capture_output=True, text=True, timeout=verify_timeout_s)`.
  First non-zero rc short-circuits; capture `(cmd, rc, stderr_tail[-500:])`.
- **Timeout:** config `validation.verify_timeout_s` (default `120`). On `TimeoutExpired` ⇒ treat as failure.
- **Override semantics — one-way demotion only (skeptical by construction):**
  - All checks exit 0 ⇒ **OK stands**, append a positive `validated` entry (`verify: N/N checks passed`).
  - Any check non-zero/timeout ⇒ **demote OK → PART**, append issue `verify_failed: <cmd> (rc=N): <stderr_tail>`, set `next` to the failing command so the retry prompt is actionable.
  - **Never promote** (PART/ERR/BLK are left as-is, or also verified but cannot be lifted to OK by checks). Honesty = the runner can only *take away* an unearned OK, never *grant* one.
- **Context hygiene (Roberto cache-save):** full verify stdout/stderr is appended to `log_path` under a `--- VERIFY ---` banner (isolated like raw logs); **only the failing cmd + short stderr tail** enters `summary["issues"]`/`next`.

---

## 4. Design decision #3 — backward compatibility (no regression)

- **Auto-detected, opt-in by data.** Gate runs *only* when `extract_verify_block(deleg_text)` returns a non-empty list. Specs without a `## Verify` block → `None` → gate is a pure no-op → **current behavior byte-for-byte**. This is why no existing test regresses.
- **Global kill-switch:** config `validation.honest_exit_code` (default `true`). `false` ⇒ extractor never called. Lets a user disable without editing specs.
- No change to the default `DELEGATION_TEMPLATE` is *required*; `## Verify` is additive. (Optional follow-up: have `cmd_delegate` echo a hint when a code-touching spec has no `## Verify`.)

---

## 5. Design decision #4 — interaction with retry loop + `do`

The gate is a **status mutator applied to every OK before that OK is allowed to stand**, so it must fire both before the retry loop and on each retry's OK.

Cleanest minimal wiring (a single helper, two call sites):

1. New helper `_apply_verify_gate(summary, verify_cmds, *, cwd, did, log_path, timeout) -> summary` (in `cli.py`). No-op + returns unchanged if `verify_cmds` is falsy or `summary["status"] != "OK"`.
2. **Call site A — before the retry loop** (immediately after the bronze-rescue block, ~`cli.py:1087`, before `_cur_status = ...` at `cli.py:1095`). A demoted OK→PART then **flows into the existing PART retry loop for free** — the retry prompt now carries the real failing command. No new retry machinery.
3. **Call site B — inside the retry loop's OK branch** (`cli.py:1163`, the `if _new_status == "OK":`). Re-apply the gate to `_r_sum`; only `break` if it **stays** OK. If the gate demotes the retried OK, treat as the loop's PART path (merge issues, continue/exhaust attempts) instead of breaking. This guarantees a *retried* OK is also independently verified.

Net: verify cmds are extracted **once** (top of body, alongside `deleg_text`), passed into both sites. `do` path inherits everything because it routes through `_cmd_run_body`.

---

## 6. Design decision #5 — blast radius + tests that must stay green

### Files / functions changed
- `src/burnless/delegation_parse.py` — **NEW** pure fn `extract_verify_block(md: str) -> list[str]` (stdlib `re` only; mirrors `spec_validator` / existing parsers). Update module docstring API list.
- `src/burnless/cli.py`
  - re-export alias near `cli.py:44` (where `_extract_test_status` etc. are imported): `from .delegation_parse import extract_verify_block as _extract_verify_block`.
  - **NEW** `_apply_verify_gate(...)` helper.
  - In `_cmd_run_body`: extract `_verify_cmds` once (after `deleg_text` at `cli.py:713`, gated on `cfg["validation"].get("honest_exit_code", True)`); call site A (~`cli.py:1087`); call site B (`cli.py:1163`).
- `src/burnless/config.py` (or the default-config dict) — add `validation.honest_exit_code: true` and `validation.verify_timeout_s: 120`. (No-op default keeps existing configs valid.)
- *(optional, non-correctness)* output-contract string `cli.py:254` — one line: "An OK is independently re-verified by the runner executing the spec's `## Verify` block; a false OK is demoted to PART."

### Tests that MUST stay green (grep'd `tests/` for run/status/retry)
- `tests/test_retry_loop.py` (esp. `test_part_triggers_retry_and_ok_on_second_attempt:103`, `test_no_retry_fields_zero_when_ok_immediately:315`, `test_part_retry_merges_issues_on_double_failure:213`, `test_stale_worker_retry_doubles_timeout:270`) — **must pass unchanged**: all use specs with no `## Verify` ⇒ gate no-ops.
- `tests/test_run_backend.py`, `tests/test_p0_runtime.py`, `tests/test_worker_envelope.py`, `tests/test_stale.py`, `tests/test_audit.py`, `tests/test_audit_filesystem_first.py`.

### NEW test — `tests/test_honest_exit_code.py`
1. `extract_verify_block` parses `sh|bash|shell|verify`-fenced block; ignores comments/blanks; returns `[]`/`None` when absent.
2. Worker returns OK + passing check (`true`) ⇒ status stays OK, run exit 0.
3. Worker returns **phantom OK** + failing check (`false` / `grep -c X == 0`) ⇒ status demoted to **PART**, issue contains `verify_failed`, run exit 1, then retry fires (mirror `test_part_triggers_retry`).
4. Spec with **no** `## Verify` ⇒ gate no-op, exit code unchanged (regression guard).
5. `validation.honest_exit_code=false` ⇒ gate skipped even with a `## Verify` block.

---

## 7. HARD PROHIBITIONS (for the implementing worker)
- DO NOT trust worker-supplied `checks`/`evidence` as the verification source. Verification commands come **only** from the spec's `## Verify` block.
- DO NOT make the gate mandatory. No `## Verify` block ⇒ exact current behavior.
- The gate may **only demote OK→PART/ERR**, never promote any status to OK.
- DO NOT paste full verify stdout/stderr into `summary`/state/capsule — full output to `log_path` only; short tail into `issues`/`next`.
- Re-exec cwd MUST be `root.parent` (same as the worker). No new sandbox.
- DO NOT touch `extract_result_json`/`normalize_worker_envelope` semantics, the maestro path, or the no-envelope v0.8 fallback.
