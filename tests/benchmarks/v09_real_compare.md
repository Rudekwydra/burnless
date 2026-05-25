# v0.9 Real-Comparison Benchmark Protocol

Compares `claude -p` one-shot task execution in two modes:

- **A — default**: no agent, full Claude TUI defaults (CLAUDE.md discovery, all tools)
- **B — burnless-planner**: with `--agent burnless-planner`, restricted to delegating via burnless-worker

### Prereq

1. `burnless init --claude-code` already executed (agents installed to `~/.claude/agents/`).
2. Smoke test agent loads:
   ```bash
   claude --agent burnless-planner -p "ping" --output-format json | python3 -c "import sys,json; d=json.load(sys.stdin); print('OK' if d.get('result') else 'FAIL')"
   ```

### Run

```bash
python3 tests/benchmarks/v09_real_compare.py \
  --task "lista os 5 maiores arquivos .py de src/burnless/ por linhas" \
  --runs 2 \
  --label first_v09_bench
```

Outputs:
- Per-run usage breakdown (stderr)
- Aggregated JSON in `tests/benchmarks/results/<timestamp>_first_v09_bench/v09_real_compare.json`
- USD/wall ratios burnless vs default

### What the benchmark CAN and CANNOT show

**CAN:**
- Direct USD per task with and without burnless agent
- Token I/O breakdown showing if burnless delegates correctly (low input + many worker calls)
- Wall time overhead of multi-layer architecture

**CANNOT (without further work):**
- Quality of output (no automated correctness check)
- Multi-turn conversations (this is one-shot only)
- Cold cache scenarios (TTL > 1h gaps between runs)

### Interpreting results

| usd_ratio (burnless/default) | Verdict |
|---|---|
| < 0.7 | Burnless winning clearly (tier delegation paying off) |
| 0.7 - 1.2 | Roughly break-even (overhead absorbed by tier savings) |
| > 1.2 | Burnless losing (overhead > tier savings — investigate why) |

| tokens_in_ratio (burnless/default) | Verdict |
|---|---|
| < 0.3 | Compactor + delegation working: planner saw little, workers did the heavy lifting |
| 0.3 - 0.8 | Some delegation but planner still consumed significant tokens |
| > 0.8 | Planner did everything itself (workers count = 0) — fix agent system prompt |

### Tasks for evaluation matrix (suggested)

- T-trivial-lookup: "lista os 5 maiores arquivos .py por linhas"
- T-medium-edit: "adicione typing.Optional em todos os parâmetros default=None de src/burnless/cli.py"
- T-heavy-build: "criar Flask app mínimo em /tmp/foo com 3 endpoints + pytest passing"
- T-research: "achar todos lugares onde 'iso_cwd' é referenciado e listar com paths + linhas"

Run each twice (default + burnless-planner), compare. Document in capsule.
