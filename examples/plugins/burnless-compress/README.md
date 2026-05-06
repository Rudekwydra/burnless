# burnless-compress (example plugin)

Local-LLM compression filter that sits between the human prompt and the cloud
LLM. Implements two Burnless hooks (`pre_worker_prompt` and `pre_brain_prompt`)
per [PLUGIN_PROTOCOL.md](../../../PLUGIN_PROTOCOL.md) v0.7.

## What it does

Two-stage compression of user messages, before they reach the paid cloud LLM:

1. **Stage 1 (LLM filter)** — local Ollama model rewrites verbose prose as a
   compressed JSON object. Drops greetings, hedging, emotion, repetition.
2. **Stage 2 (telegrafista)** — regex drops articles and high-frequency
   prepositions (`o`, `a`, `que`, `para`, `com`, `the`, `for`, `with`).

Empirically (50 PT samples, May 2026): **2.5× token compression** with
qwen2.5:7b-instruct + telegrafista. See
[`bench/COMPRESSION_FINDINGS.md`](../../../bench/COMPRESSION_FINDINGS.md) for
the full method and per-model comparison.

## Setup

```bash
# 1. Pull a model and start Ollama
ollama pull qwen2.5:7b-instruct
ollama serve   # if not already running

# 2. Run the plugin server (uses stdlib only — no extra deps)
python examples/plugins/burnless-compress/server.py

# 3. Register the plugin with Burnless
mkdir -p ~/.burnless/plugins
cp examples/plugins/burnless-compress/manifest.json ~/.burnless/plugins/

# 4. Verify
curl -s http://localhost:7711 -d '{"hook":"pre_worker_prompt","prompt":"please implement the cache test on claude -p"}'
# Expected: {"prompt": "implement cache test claude -p", ...}
```

## Configuration

Environment variables:

| Variable | Default | Notes |
|---|---|---|
| `BURNLESS_COMPRESS_MODEL` | `qwen2.5:7b-instruct` | Any Ollama model name. Cloud variants supported (`gemma3:27b-cloud`). |
| `BURNLESS_COMPRESS_LANG` | `pt` | `pt` or `en`. Locks the prompt template. |
| `BURNLESS_COMPRESS_PORT` | `7711` | HTTP port — must match `manifest.json` `endpoint`. |
| `OLLAMA_URL` | `http://localhost:11434/api/generate` | Override for non-standard Ollama setup. |

## Model choice — empirical guide

| Model | Compression | Style | When to use |
|---|---|---|---|
| qwen2.5:7b-instruct (local, 7B) | **2.5×** | aggressive — drops contextual cues | Worker bronze (deterministic action) |
| gemma3:27b-cloud (Ollama Cloud) | 1.9× | conservative — preserves reasons | Brain (decision making) |
| gpt-oss:120b-cloud (Ollama Cloud) | 1.8× | preservation-heavy | Long-term capsules |

**Counterintuitive:** larger models compress **less**, not more. Big models recognize
nuance and refuse to drop "context that might matter." For aggressive compression,
pick a smaller model.

## Verification

```bash
python examples/plugins/burnless-compress/integration_test.py
```

Starts the server on a free port, fires 5 hook payloads (pre_worker_prompt × 4 + pre_brain_prompt × 1, including an empty-string passthrough case), asserts JSON schema, and measures real-token compression with tiktoken. Saves artifacts to `~/.burnless/test_data/plugin_integration/{ts}/` (outside the repo). Zero Anthropic API consumption — Ollama-local only.

Reference run (May 2026, qwen2.5:7b-instruct): **5/5 passed, avg 2.27×** on non-empty inputs (range 1.17×–3.83×).

## Failure mode

The filter is fail-open: if Ollama is unreachable, the LLM stage falls back to
the original text and only telegrafista (Stage 2, deterministic) runs. If both
fail, the original prompt passes through unchanged. Burnless never blocks on
plugin failure (5s timeout per PLUGIN_PROTOCOL.md).

## License

MIT — same as the protocol and reference implementation.
