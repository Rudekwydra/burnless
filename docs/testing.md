# Testing (map + unification plan)

Burnless currently has multiple “test-like” surfaces (pytest, bench harnesses, app prototypes). This doc is a map plus a minimal plan to converge on **one correctness harness** without rewriting everything.

## What exists (today)

- Canonical correctness tests (pytest): `tests/`
- CLI / Shell / PTY code: `src/burnless/cli.py`, `src/burnless/shell.py`, `src/burnless/pty_shell.py`
- Benchmarks (main entrypoints): `bench/run.py`, `bench/benchmark.py`
- Bench variants / experiments: `bench/v2.py`, `bench/v4_maestro.py`, `bench/brain_tier_compare.py`, `bench/replay_vs_capsule.py`, `bench/tier_routing_savings.py`
- Live probes outside pytest: `bench/test_cache_cross_model.py`
- Desktop prototype bench/harness: `_pro/desktop/bench/`
- Cloud prototype bench/harness: `_pro/bench_via_cloud.py`, `_pro/cloud_emulator.py`, `_pro/cloud_client.py`

## The problem

Most duplication is not in “tests”, but in *harness loops + result schemas* (multiple runners doing similar accounting/logging). This makes it hard to answer: “what is the one command I run to know if things are OK?”

## Minimal unification plan (no big rewrite)

1. **Declare pytest as the single correctness harness**
   - `tests/` remains source of truth for correctness and deterministic behavior.
2. **Add a provider×tier matrix test (offline by default)**
   - Add one file like `tests/test_harness_matrix.py` that parametrizes provider×tier using mocks by default.
   - Gate live API smoke behind markers/env (example: `pytest -m live` runs only if keys exist).
3. **Keep benchmarks separate, but pick one canonical runner**
   - Document `bench/run.py` as the canonical benchmark entrypoint.
   - Keep other bench scripts as experiments/legacy (don’t break them yet).

## Recommended commands

- Correctness (fast): `pytest`
- Live smoke (only when configured): `pytest -m live`
- Bench (repro / savings): `python bench/run.py` (or `python bench/v2.py ...` when reproducing README numbers)

