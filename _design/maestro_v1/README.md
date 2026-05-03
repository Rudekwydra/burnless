# Maestro v1 — design

Runtime artifacts loaded by the Burnless Brain/Worker.

## Files
- `glossary.md` — Burnless v1 vocabulary (~50 terms, byte-identical every turn)
- `brain_role.md` — Brain prompt (orchestrator)
- `worker_role.md` — Worker prompt (one-shot executor)
- `schemas.md` — capsule format, exec_log, brain_history, cache breakpoints

## Principles

1. **Cache hot via `cache_control` ephemeral 1h** — header is byte-identical every turn
2. **Shared header Brain ↔ Worker** — same cached prefix, paid once
3. **Split execution memory** — Brain only stores capsules (50–200 tok); detail in `exec_log` outside cache
4. **Encoder/decoder Haiku at the edges** — humans see natural prose; Brain only sees capsules
5. **Pluggable semantic compressor** — `--encoder llmlingua2` (XLM-RoBERTa, CPU) or any local model
6. **Police (opt-in)** — re-validates capsule when confidence is low
7. **Glossary core ≠ tenant glossary** — core is the framework (byte-identical cross-tenant); tenant glossary is configurable per project
8. **`[THINK]` block always visible** — user sees Brain reasoning in dim color, but it never goes into the API cache
9. **Per-project `exec_log`** — `.burnless/exec_log/T<id>.md` lives in the project where it ran
