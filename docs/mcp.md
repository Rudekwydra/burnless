# MCP Server — Burnless v1

Native Burnless tools via MCP protocol, integrated directly into Claude Code.

## Installation

```bash
pip install burnless[mcp]
```

## Registration

Add to `~/.claude.json`:

```json
{
  "mcpServers": {
    "burnless": {
      "command": "python",
      "args": ["-m", "burnless.mcp_server"]
    }
  }
}
```

## Tools

| Tool | Purpose |
|------|---------|
| `delegate` | Create delegation file + route to tier |
| `route` | Preview tier routing (read-only) |
| `run` | Execute delegation (sync or background) |
| `capsule` | Read finalized result JSON |
| `read` | Fallback read: capsule → envelope → log |
| `status` | Project health OR per-delegation status |

## Example: End-to-End Flow

1. **Delegate** a task:
   ```
   mcp__burnless__delegate(text="implement /health endpoint", tier=None)
   → {id: "d042", tier: "silver", agent: "claude-sonnet-4-6", ...}
   ```

2. **Run** in background:
   ```
   mcp__burnless__run(id="d042", background=True)
   → {status: "running", pid: 12345, log_path: "...", ...}
   ```

3. **Check status**:
   ```
   mcp__burnless__status(id="d042")
   → {state: "running", pid: 12345, ...}
   ```

4. **Read final result** (waits for completion):
   ```
   mcp__burnless__read(id="d042")
   → {source: "capsule", content: {status: "OK", files: [...], ...}, ...}
   ```

## Notes

- All tools resolve `.burnless/` root via walk-up from `cwd` (or explicit `project_root`).
- Error responses include `error` + `hint` fields.
- Background runs spawn detached subprocess (survive server restart).
- No HTTP/network — pure stdio transport.
