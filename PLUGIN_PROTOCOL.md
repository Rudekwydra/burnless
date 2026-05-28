# Burnless Plugin Protocol v0.7

> ⚠️ **DEPRECATED / NOT SHIPPING in v0.9.** This 8-hook plugin protocol is an
> aspirational design that was never wired into the shipping product. The real
> v0.9 architecture is **Encoder/Decoder · Maestro · Workers** — see `PROTOCOL.md`
> (canonical). This file is kept as a historical design note only; do not cite it
> as a feature. Remove before public ship unless the protocol is actually built.

**License:** MIT  
**Status:** Deprecated design note (not shipping)  
**Version:** 0.7.0 (historical)

Burnless calls plugins; plugins never execute inside Burnless.

---

## 1. Overview

Plugins are external processes or HTTP services. Burnless dispatches JSON payloads to registered hooks; plugins respond with JSON. No plugin code runs inside the Burnless process.

---

## 2. Manifest Format

Place manifest files in `~/.burnless/plugins/*.json`. Files ending in `.disabled` are ignored.

```json
{
  "name": "my-plugin",
  "version": "1.0.0",
  "protocol": "http",
  "endpoint": "http://localhost:7700",
  "auth": "",
  "hooks": ["pre_worker_prompt", "audit_result_received"]
}
```

| Field | Type | Values | Description |
|---|---|---|---|
| `name` | string | — | Unique plugin identifier |
| `version` | string | semver | Plugin version |
| `protocol` | string | `http` \| `stdio` \| `https` | Transport |
| `endpoint` | string | URL or path | HTTP base URL or stdio executable path |
| `auth` | string | Bearer token | Used only by `https` transport |
| `hooks` | list[string] | hook names | Hooks this plugin handles |

---

## 3. Hook Specifications (v0.7 — 8 hooks)

All hooks use JSON over the chosen transport. Burnless enforces a 5-second timeout per call. Failures are logged as warnings and do not break the pipeline.

### H1 — `pre_worker_prompt`

**Pipeline position:** Before worker prompt is dispatched to subprocess.

```jsonc
// Request
{"hook": "pre_worker_prompt", "spec": {...}, "prompt": "...", "system_prompt": "..."}
// Response
{"prompt": "...", "system_prompt": "..."}
```

### H2 — `post_worker_output`

**Pipeline position:** After worker subprocess returns stdout.

```jsonc
// Request
{"hook": "post_worker_output", "spec": {...}, "stdout": "...", "stderr": "...", "capsule": "..."}
// Response
{"capsule": "...", "stdout": "..."}
```

### H3 — `session_state_read`

**Type:** Host capability (pull). Plugins query Burnless; Burnless does not push.

```
GET /session/state
→ {"topic": "...", "recent_turns": [...]}
```

### H4 — `audit_result_received`

**Pipeline position:** After audit result is written and attached to summary. Fire-and-forget.

```jsonc
// Request
{"hook": "audit_result_received", "did": "d146", "audit": {...}, "summary": {...}}
// Response
{}
```

### H5 — `pre_brain_prompt`

**Pipeline position:** Before Brain sends prompt to Anthropic SDK.

```jsonc
// Request
{"hook": "pre_brain_prompt", "user_capsule": "...", "history": [...], "system_blocks": [...]}
// Response
{"user_capsule": "...", "system_blocks": [...]}
```

### H6 — `post_brain_output`

**Pipeline position:** After Brain receives response, before decoder.

```jsonc
// Request
{"hook": "post_brain_output", "capsule_text": "...", "raw_body": "...", "delegate_lines": [...]}
// Response
{"capsule_text": "...", "raw_body": "...", "delegate_lines": [...]}
```

### H7 — `worker_invoke_override`

**Pipeline position:** Before subprocess.run. If plugin returns a capsule, the subprocess is skipped.

```jsonc
// Request
{"hook": "worker_invoke_override", "spec": {...}, "prompt": "...", "system_prompt": "..."}
// Response (short-circuit)
{"capsule": "slv val src/foo.py :: OK ..."}
// Response (pass-through)
{"capsule": null}
```

### H8 — `pre_audit_call`

**Pipeline position:** Before audit fast-path and auditor ladder.

```jsonc
// Request
{"hook": "pre_audit_call", "did": "d146", "evidence": [...], "summary": {...}, "auditors_ladder": ["bronze", "silver", "gold"]}
// Response (full override)
{"audit": {...}}
// Response (ladder override)
{"override_ladder": ["gold"]}
// Response (pass-through)
{"audit": null}
```

---

## 4. Transports

### HTTP local (`"protocol": "http"`)

Burnless sends a `POST` to `{endpoint}/{hook_name}` with `Content-Type: application/json`.

```
POST http://localhost:7700/pre_worker_prompt
Content-Type: application/json

{"hook": "pre_worker_prompt", ...}
```

### stdio (`"protocol": "stdio"`)

Burnless forks `endpoint` as a subprocess, writes JSON to stdin, reads JSON from stdout. One invocation per hook call.

```bash
echo '{"hook":"pre_worker_prompt",...}' | my-plugin-bin
```

### HTTPS cloud (`"protocol": "https"`)

Same as HTTP but TLS, with `Authorization: Bearer {auth}` header.

```
POST https://api.myplugin.com/pre_worker_prompt
Authorization: Bearer <token>
Content-Type: application/json
```

---

## 5. Creating a Plugin

1. Choose a transport: `http` for local daemons, `stdio` for scripts, `https` for cloud services.
2. Implement handlers for the hooks you need (see §3).
3. Write a manifest JSON and drop it in `~/.burnless/plugins/my-plugin.json`.
4. Start your plugin server (for `http`/`https`) or ensure the binary is in PATH (for `stdio`).

Burnless will discover and call your plugin on the next run.

---

## 6. Policy

- **Burnless calls plugins. Burnless never executes plugin code.**
- Plugins that exceed the 5-second timeout are skipped with a warning.
- Plugin failures never break the Burnless pipeline.
- H7 (`worker_invoke_override`) can skip the worker LLM call; use responsibly.
- H8 (`pre_audit_call`) can replace the auditor; the returned audit dict must match the audit schema.
