# No-Proxy Real-Flow Benchmark

Measures the **actual** Burnless no-proxy UX across four real scenarios, not
simulated Maestro fiction. Every reported number must state the exact scenario
it came from and its caveat — figures are **not** comparable across scenarios
without that context.

Harness: [`no_proxy_real_flow.py`](no_proxy_real_flow.py).

## Scenarios

| # | Name | What it is | Invocation |
|---|------|------------|------------|
| 1 | `raw` | Claude Code raw session, no Burnless | `claude -p --output-format stream-json <task>` |
| 2 | `observe` | Claude Code + Burnless in `observe` mode (policy visible, no enforcement) | `BURNLESS_MODE=observe claude -p ... <task>` |
| 3 | `on` | Claude Code + Burnless `on` (planner agent delegates execution) | `claude -p --agent burnless-planner ... <task>` |
| 4 | `cli_do` | Burnless CLI direct worker routing | `burnless do --tier silver <task>` |

## Metrics captured

- input tokens
- output tokens
- cache read / cache write
- assistant turns
- worker delegations
- retrieval calls
- verify pass/fail
- wall time
- successful completion
- user-visible verbosity
- post-clear recovery success

Uncaptured metrics for a given scenario are reported as `null`, never faked.

## Running

Dry-run (no model calls — prints the measurement plan only; this is the
acceptance check):

```bash
cd /Users/roberto/antigravity/burnless
.venv/bin/python tests/benchmarks/no_proxy_real_flow.py --dry-run
.venv/bin/python tests/benchmarks/no_proxy_real_flow.py --dry-run --json
```

Live run (invokes real `claude -p` / `burnless do`; cost and latency depend on
model and machine):

```bash
.venv/bin/python tests/benchmarks/no_proxy_real_flow.py \
  --task "List the 5 largest .py files in src/burnless by line count." --runs 1
```

## Caveats

- Each row is scenario-specific; do not aggregate across scenarios without the
  caveat attached.
- Live runs hit real models — token, cost, and wall-time numbers vary by model
  and host.
- Dry-run performs **no** model calls and reports only the plan.
- Token reuse helpers are shared with [`v09_real_compare.py`](v09_real_compare.py);
  if that import is unavailable, live token fields degrade to `null` rather than
  failing.
