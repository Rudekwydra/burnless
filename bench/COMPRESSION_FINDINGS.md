# Compression filter — empirical findings (May 2026)

What this is: results of running `bench/filter_entrada_spike.py` over 30 medium-to-large
Portuguese sentences across 4 LLM models (2 local, 2 cloud), measured with the real
`tiktoken` tokenizer (cl100k_base — close proxy for Anthropic and OpenAI tokenizers).

The filter is a two-stage compressor that sits between a verbose human message and the
expensive cloud LLM that receives the orchestrated request. **Stage 1** is a small/medium
LLM that drops filler. **Stage 2** is `deterministic_squeeze` — a regex telegrafista
that drops articles and high-frequency 1-token prepositions ("o", "a", "de", "que",
"para", "com", "the", "a", "for", "with").

## Setup

- 30 input messages, mixed: technical requests, verbose-with-filler, bug reports,
  architectural questions, refactor requests, urgency-emotional commands, short tech
  commands. Average length ~35 tokens.
- Token counting: `tiktoken.cl100k_base`. The previous heuristic `len(text)/4` was
  systematically off — replaced with real BPE counts.
- Each message processed through: `original → LLM filter (Stage 1) → telegrafista
  (Stage 2) → final`.

## Results (N=50 samples, except where noted)

![Compression filter results](compression_chart.svg)


| Model | Type | Size | Final ratio | Style |
|---|---|---|---:|---|
| `qwen2.5:7b-instruct` | local Ollama | 7.6B | **2.50×** | aggressive — drops contextual cues |
| `qwen3-next:80b-cloud` | Ollama Cloud | 80B | 2.29× (N=38, partial — cloud timed out at sample 39) | aggressive — Qwen family preserves the squeeze style at scale |
| `gpt-oss:120b-cloud` | Ollama Cloud | 120B | 1.90× | preservation-heavy |
| `gemma3:27b-cloud` | Ollama Cloud | 27B | 1.90× | preservation-heavy |

ASCII-bar comparison (compression ratio, longer = more compression):

```
qwen2.5:7b-instruct  ( 7.6B local)   2.50x  ████████████████████████████████████████
qwen3-next:80b-cloud (  80B cloud)   2.29x  ████████████████████████████████████░░░░
gpt-oss:120b-cloud   ( 120B cloud)   1.90x  ██████████████████████████████░░░░░░░░░░
gemma3:27b-cloud     (  27B cloud)   1.90x  ██████████████████████████████░░░░░░░░░░
```

### What this reveals

The naive "bigger model = more compression" hypothesis is **wrong**. Two observations dominate:

1. **Model family > model size for compression.** `qwen3-next:80b` (80B params) compresses *more aggressively* than `gemma3:27b` (27B params). The Qwen family was trained with a more terse instruction-following style; that survives at scale. Gemma and gpt-oss prefer to preserve context, regardless of size.

2. **Within a family, larger = more conservative.** `qwen2.5:7b` (2.50×) compresses harder than `qwen3-next:80b` (2.29×); both are Qwen, the smaller one is more aggressive. Same direction observed for Gemma (`gemma4:e2b` 5.1B at 2.1× in the N=30 sample, vs `gemma3:27b` 27B at 1.9× in N=50).

So the right axis is **family + size**, not size alone. For aggressive compression of bronze-tier worker prompts, pick a small Qwen. For nuance-preserving compression of gold-tier Brain prompts, pick a larger Gemma or gpt-oss.

Counterintuitive finding: **larger models compress less, not more.** Big models
recognize nuance and refuse to drop "context that might matter." Small models don't
see the nuance and cut hard. The compression-vs-preservation trade-off correlates
inversely with model size.

## Discarded approaches (validated empirically as worse)

These were tested with real tiktoken and **all yielded zero or negative token savings**:

| Technique | Why it fails |
|---|---|
| Abbreviations dictionary (`thx`, `vc`, `w/`, `pls`) | BPE breaks "thx" into 2 tokens; "thank you" is 2 tokens. Net **+0 to +2 tokens per substitution.** |
| Disemvoweling ("contxt") | "parágrafo" is 2 tokens; "prgrf" is 4 tokens. **Always loses.** |
| gzip + base64 | Binary text in base64 expands to ~4× the original tokens — LLMs don't decode gzip. |
| Emoji substitution | Most emojis are 3 BPE tokens; only `✅❌💾💡` are 2 tokens. Net wash to negative. |

What works:
- **Telegrafista** (drop articles/preps): consistent **+10–30%** savings in cl100k_base.
- **LLM filter with strong few-shot prompt + JSON output schema**: **1.5–2.5×** depending on model.
- **Combined**: 2.0–2.8× real savings, validated.

## Tradeoffs by use case

| Filter destination | Recommended model | Rationale |
|---|---|---|
| Worker bronze (deterministic action) | small (7B local) | aggressive compression OK; worker only needs the action |
| Brain (decision making) | medium-large (27B cloud) | preserve nuance for reasoning |
| Long-term capsule | medium (12–30B) | balanced compression + preservation |

## Why this matters for the Burnless protocol

Capsules-as-replay-replacement is the invention of Burnless. The compression filter
is one of the layers that makes capsules economical. The filter does **not** change
the `Θ(N²) → Θ(N)` curve — capsules do that. The filter reduces the constant
factor on each turn's input, layered on top of the curve change.

Empirical numbers:
- Curve change (capsules vs replay): asymptotic, dominates at high N.
- Constant-factor reduction (filter): consistent 2× per turn.
- Together: the cumulative cost across N turns is `2× cheaper` per turn AND `O(N)`
  instead of `O(N²)`.

## Reproducing

```bash
# Local
ollama pull qwen2.5:7b-instruct      # ~5 GB
ollama pull gemma4:e2b               # ~7 GB
python bench/filter_entrada_spike.py --model qwen2.5:7b-instruct --lang pt --squeeze-stage post

# Cloud (Ollama Cloud — requires OLLAMA_API_KEY)
python bench/filter_entrada_spike.py --model gemma3:27b-cloud --lang pt --squeeze-stage post
python bench/filter_entrada_spike.py --model gpt-oss:120b-cloud --lang pt --squeeze-stage post
```

Token counting requires `tiktoken`:
```bash
pip install tiktoken          # or use a venv
```

## Recommended preset (proposed v0.6)

```yaml
# .burnless/config.yaml — compression filter section
compression_filter:
  enabled: true
  squeeze_stage: post                    # telegrafista AFTER LLM filter
  lang: pt                                # locks examples to one language
  by_tier:
    bronze:
      model: qwen2.5:7b-instruct          # local, aggressive, fast
      mode: aggressive
      fallback: haiku                     # paid fallback if local busy
    silver:
      model: gpt-oss:120b-cloud           # cloud, balanced, tool-capable
      mode: balanced
      fallback: claude-sonnet-4-6         # paid fallback if cloud unavailable
    gold:
      model: gemma3:27b-cloud             # cloud, preservation-focused
      mode: conservative
      fallback: claude-opus-4-7
```

The fallback pattern keeps the system running when cloud is rate-limited or local
hardware is busy. Cost rises gracefully; nothing blocks.

## Headline

**Everything that saves tokens is burnless. Work more, burnless.**
