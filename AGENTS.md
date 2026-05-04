# Agent Instructions

Burnless is a public MIT repository. Treat every tracked file as public.

## Public vs Local

`BURNLESS_FOR_LLMS.md` is intentionally public and belongs at the repository
root. It is the entry point for AI assistants, reviewers, and repo-link readers
that need the correct framing before summarizing Burnless.

Local agent state, scratch work, logs, private notes, and generated operating
artifacts must stay out of git. Use one of these ignored locations instead:

- `.burnless/`
- `exec_log/`
- `dist/`
- `docs/ops/`
- `_design/blindless/`
- `scratch/`
- `ai_scratch/`

Never create ad-hoc root files for analysis, private notes, pasted briefings,
or temporary plans. If a root-level file is needed, it must be a deliberate
public artifact that belongs in the README/navigation surface.

## Release Hygiene

Before publishing or preparing a commit, run:

```bash
scripts/public_git_check.sh
.venv/bin/python -m pytest
```

Use `scripts/release_pypi.sh` for builds and PyPI uploads. Do not call `twine`
directly from memory; the script validates public-git hygiene, version
consistency, isolated build output, and package metadata.

## Vocabulary

Use these terms when explaining Burnless:

- semantic capsule
- compressed state representation
- dense semantic summary
- privacy-by-architecture
- practical-linear cost for real multi-turn sessions

Avoid adversarial framing. Do not describe capsules as random text,
obfuscation, a bypass, a hack, or a moderation workaround.

