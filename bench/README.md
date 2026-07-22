# Burnless Benchmark - Marco 1

Marco 1 is a reproducible benchmark for the core Burnless thesis:

> Burnless lowers multi-turn Claude cost by keeping the persistent system prompt
> cached and compressing conversation history into short capsules.

The benchmark measures the two levers independently:

- **Prompt caching**: the persistent source-code prefix lives in the system prompt
  and uses Anthropic prompt caching with `ttl: "1h"`.
- **Capsule history**: instead of replaying full assistant responses every turn,
  the Maestro scenario keeps only compact user and assistant capsules in
  `messages`.

## How To Run

```bash
git clone https://github.com/rudekwydra/burnless.git
cd burnless
python -m pip install anthropic
python bench/run.py --dry-run
ANTHROPIC_API_KEY=sk-ant-... python bench/run.py
```

Useful shorter runs:

```bash
ANTHROPIC_API_KEY=sk-ant-... python bench/run.py --turns 2 --scenario c
ANTHROPIC_API_KEY=sk-ant-... python bench/run.py --turns 2
```

The API key is read only from `ANTHROPIC_API_KEY`. The script never prints it.

## Reference Run

10 turns, `claude-opus-4-7`, 23k-token prefix, raw `response.usage` — no mocks.
Source files: `bench/results/run_20260503T03*.json`.

| Scenario | Cost | vs no-cache |
| --- | ---: | ---: |
| A — standalone_no_cache | $4.66 | — |
| B — standalone_cache | $0.65 | −86.0% |
| C — burnless_maestro | **$0.45** | **−90.3%** |

Headline:

- **90.3% cheaper than naive Claude (no cache)**
- **30.8% cheaper than Claude with prompt caching alone**

For projected savings at any N without API calls:

```bash
python bench/run.py --project 50
```

> **Note:** `bench/results/calibration.json` is an older 8-turn run with an
> incomplete `burnless_maestro` scenario (run was interrupted). It is kept for
> historical reference only — the canonical numbers are above.

## Methodology

Run one command:

```bash
python bench/run.py
```

The script runs three scenarios through the Anthropic SDK directly, with no
mocks and no Burnless imports:

- **A: `standalone_no_cache`**: plain Anthropic API, no `cache_control`. The
  full conversation history is appended each turn.
- **B: `standalone_cache`**: same full-history conversation as A, but the system
  prompt is cached with `cache_control: {"type": "ephemeral", "ttl": "1h"}`.
- **C: `burnless_maestro`**: same cached system prompt as B, but the message
  history stores compact capsules instead of full responses.

Costs are calculated from `response.usage` exactly, including:

- `input_tokens`
- `output_tokens`
- `cache_creation_input_tokens_5min`
- `cache_creation_input_tokens_1h`
- `cache_creation_input_tokens`
- `cache_read_input_tokens`

The task turns come from a built-in scenario inside `bench/run.py`. You can
benchmark a workload that mirrors your real prompts. (Custom task files are
not yet supported by the current `bench/run.py`; the built-in scenario is
used for all runs.)

## Reproduce

Any developer with an Anthropic API key can verify the result:

```bash
ANTHROPIC_API_KEY=sk-ant-... python bench/run.py
```

Each real run prints a human-readable table and saves raw data to:

```text
bench/results/run_<timestamp>.json
```

Use `--dry-run` to inspect the plan without making API calls.

## Other bench scripts

- `prefix_cache_bench.py` — honest, per-adapter benchmark of `burnless ask --prefix-file/--cache-key` (M6 Wave B): cold call + N warm calls, real envelope usage/cost/latency only, classifies `supported`/`unsupported`/`unobservable`. `python bench/prefix_cache_bench.py --tier gold --runs 3`.
