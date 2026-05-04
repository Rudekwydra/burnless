# Burnless

**Intent-compressed intelligence orchestration.**

A maestro that orchestrates any LLM from any vendor. Multi-turn agent loops cost O(N²) — Burnless makes them O(N).

Burnless is a vendor-agnostic orchestration layer for multi-agent workflows. You pick the model that **conducts** the orchestra (Maestro / Brain) — Claude, GPT, Gemini, Mistral, a local Llama, anything — and the models that **execute** each task (Workers). Tiers are quality/cost bands, not vendors: `gold`/`silver`/`bronze` map to whatever CLI you put in `config.yaml`. Mix providers freely. Run encoder and decoder on a local Ollama model for zero marginal cost on the cheap stages.

On top of that independence, Burnless flips the cost curve. Every turn in a standalone agent loop replays the full conversation as input — token cost on turn `N` is proportional to `N`, so total cost across `N` turns is `Θ(N²)`. Burnless keeps only short capsules in history and shares a cached system prompt across Maestro and Workers. History stays linear; the persistent prefix is billed once per cache window instead of once per turn.

The asymmetry is mechanical, not heuristic. Any provider that charges per input token is subject to the same arithmetic — Anthropic, OpenAI, Google, Mistral, anyone. The reference numbers below use Anthropic's pricing because their cache read/write spread is published and the cheapest to verify (`$15/MTok` fresh input vs `$0.15/MTok` cache read — a 100× spread). The mechanism reproduces wherever a provider exposes prompt caching.

## Four things, in this order

1. **Independence.** Any model as Maestro. Any model as Worker. Switch providers in one line.
2. **User-enforced rules, not LLM goodwill.** You write the routing keywords, the per-tier `allowedTools`, and the cost budgets in `.burnless/config.yaml`. With `routing.hardcore_filter: true` (or `BURNLESS_HARDCORE=1`), the Maestro **cannot escape** to a higher tier than the keyword router resolved — no quiet upgrades to Opus for tasks the rules said belong to Haiku. `allowedTools` is enforced by the worker CLI itself, not hinted at in the prompt: when bronze ships with `Read,Bash`, it physically cannot `Edit`. Bypass requires an explicit `--force` from the human.
3. **Four compression layers.** Deterministic minifier (regex, zero cost), semantic encoder (small model, ~$0.001/turn), lightweight capsule envelope, and base64 capsule packing. Each layer is independent and additive.
4. **Math, not marketing.** 88% cheaper at turn 10 by arithmetic on the published pricing pages. Verify with `python bench/run.py --turns 8` and your own API key.

## The numbers

Two views, both reproducible on your machine.

**Real API run** — 10 turns against `claude-opus-4-7`, 23k-token prefix, no mocks, raw `response.usage` (actual spend: $5.76):

| Scenario | Cost | vs no-cache |
|---|---:|---:|
| A — Standalone, no cache | $4.66 | — |
| B — Standalone + cache | $0.65 | **−86.0%** |
| C — Burnless Maestro | **$0.45** | **−90.3%** |

Reproduce: `ANTHROPIC_API_KEY=... python bench/run.py --turns 10` (~$6).

**Monte Carlo simulation** — 30 runs × 100 turns × 4 scenarios. Per-turn input/output sampled `Uniform(2k, 10k)` / `Uniform(200, 1500)`, capsule compression `Uniform(0.20, 0.30)`. Zero API cost:

| Scenario | Mean | vs Pure Opus |
|---|---:|---:|
| A1 — Pure Opus 100 | $532.61 | — |
| A2 — Pure Sonnet 100 | $105.42 | −80.2% (5× cheaper) |
| B — Free-pick (Opus/Sonnet) | $328.74 | −38.3% (1.6× cheaper) |
| **Z — Burnless** | **$33.35** | **−93.7% (16× cheaper)** |

The interesting row is **B**. A developer alternating Opus and Sonnet ad-hoc — what most people actually do — costs **3× more than just sticking with Sonnet**, because every model switch invalidates the prefix cache. Burnless is 10× cheaper than B and 3× cheaper than the disciplined "all Sonnet" strategy, because Brain stays fixed (cache hot) while workers tier down to Haiku where they can.

Reproduce: `python bench/v2.py --runs 30 --turns 100 --seed 42`. Zero cost, no key.

For the formal derivation — including why Burnless only loses at `N = 1` — read [**`MATH.md`**](MATH.md).

![Burnless cost chart](docs/cost_chart.png)

## Design decisions

The 88% number is an outcome. These are the calls that produced it, in the order they were made.

**1. Treat the cost curve as math, not engineering.** Multi-turn agents replay full history every turn. Tokens billed across `N` turns sum to `Θ(N²)` — that is arithmetic on the pricing page, not a property of any SDK. Once the problem is stated as O(N²), the only useful question is what to truncate. Everything else follows.

**2. Brain stores capsules, not transcripts.** The Brain's conversation history holds ~80-char single-line summaries of each prior turn, not the raw exchange. Full output stays on disk, read on demand. This is the single change that flips the curve to O(N) — every other layer compounds on top of an already-linear baseline.

**3. Shared prefix cache across models.** If two models from the same provider see a byte-identical system prompt with `cache_control` set, they hit the same prefix cache. Switching Opus → Sonnet mid-session does not invalidate it. Brain and Worker can be different models and still amortize the 23k-token system prompt at read price ($0.15/MTok) instead of write price ($15/MTok). The 100× spread is the lever.

**4. Tiers are quality/cost bands, not models.** `gold`/`silver`/`bronze` map to commands in `config.yaml`, not to Opus/Sonnet/Haiku. Any model can be Brain. Any model can be Worker. GPT-4o as Brain, Codex as silver code worker, and Ollama as bronze summarizer is a normal config. Hardcoding tier→model would have made the orchestration layer a single-vendor wrapper instead of a pattern.

**5. Determinism before LLMs.** Layer 1 of the compression stack is pure Python — no model call, zero latency, zero cost. Filler phrases stripped, whitespace normalized, before the encoder ever sees the text. Cheap stages run first for a reason.

**6. Cache-emergent glossary, not only static dictionaries.** The semantic compression layer (Layer 2) uses session context as the working "dictionary." Abbreviations can emerge from the session — the encoder infers them from prior turns and applies them consistently. The protocol target is an append-only glossary with three layers: core terms, tenant/project terms, and session deltas proposed by the encoder and validated by Burnless. In v0.5 this is partially implemented through static minification and session context; the append-only glossary log is tracked in the protocol roadmap.

**6b. Privacy is a consequence of architecture, not a flag.** Running everything on one cloud provider gives zero additional privacy — that provider sees everything at generation time. Privacy emerges from where you run each component. Local encoder/decoder: the cloud Maestro receives only capsules, never raw text. Local Maestro: cloud workers receive disconnected task fragments with no conversation context — they cannot reconstruct the session. All local: zero cloud exposure. The cost reduction (O(N²) → O(N)) applies at all four levels regardless of privacy configuration.

**7. Open protocol, enterprise operations.** Burnless Protocol stays open: capsules, compression, glossary semantics, and local privacy modes belong in the MIT implementation. Burnless Cloud/Enterprise should monetize what companies actually need to trust it: key custody, audit logs, KMS/HSM, retention policies, SSO/RBAC, legal hold, and operational proof.

**8. The benchmark is the proof.** `bench/run.py` is short, dependency-light, hits the Anthropic SDK directly with no mocks, and writes raw `response.usage` to JSON. Anyone can rerun it, contest the numbers, and open an issue with their own results file. We did not write a marketing page about savings; we wrote a script that produces them and invited disagreement. That is the only honest way to publish a cost claim.

## Install

```bash
pip install burnless
cd <your-project>
burnless setup               # one-time per project: detects CLIs/keys + initializes .burnless/
burnless                     # enter Burnless Chat
```

`burnless setup` writes `.burnless/config.yaml` and creates the project structure in one shot — no separate `init` needed unless you want a minimal config without auto-detection (then run `burnless init` instead).

Python 3.10+. Tiers map to whatever models you configure — mix providers freely.

Or install from source:

```bash
git clone https://github.com/rudekwydra/burnless.git
cd burnless && pip install -e .
```

To remove from a project: `rm -rf .burnless/` (no built-in `uninstall` command yet — `pip uninstall burnless` removes the package but leaves your project state untouched, which is intentional).

## Any model. Any role. Full control.

Tiers are **quality/cost bands**, not models. You decide what runs each band — and any model can be the Brain.

```yaml
# .burnless/config.yaml — example: GPT-4o as Brain, Codex for code, Haiku for cheap tasks
agents:
  gold:    { command: "openai api chat.completions.create -m gpt-4o" }
  silver:  { command: "codex exec --sandbox workspace-write" }
  bronze:  { command: "claude --model claude-haiku-4-5 -p --allowedTools Read,Bash" }
```

Gemini as Brain, DeepSeek for execution (both have published cache pricing):

```yaml
agents:
  gold:    { command: "gemini -m gemini-2.0-flash-thinking -p" }   # Brain
  silver:  { command: "deepseek chat --model deepseek-chat -p" }    # execution
  bronze:  { command: "ollama run qwen2.5-coder" }                  # local, zero cost
```

Or Sonnet as Brain delegating to Codex workers:

```yaml
agents:
  gold:    { command: "claude --model claude-sonnet-4-6 -p" }   # Brain
  silver:  { command: "codex exec --sandbox workspace-write" }   # everyday code execution
  bronze:  { command: "ollama run llama3" }                      # local model, cheap tasks
```

Each tier gets its own `allowedTools`, routing keywords, and cost budget. The routing layer reads your task description and picks the right tier automatically — or you override it explicitly.

The O(N²) → O(N) math applies to any provider that charges per input token. Burnless is the **orchestration and caching layer**, not a wrapper for one API.

Taking it further: the encoder and decoder — the models that compress user messages into capsules and expand capsules back into natural language — can run on a **local model at zero marginal cost**:

```yaml
agents:
  bronze: { command: "ollama run llama3.2" }   # capsule encoder/decoder — $0
  silver: { command: "claude --model claude-haiku-4-5 -p" }
  gold:   { command: "claude --model claude-sonnet-4-6 -p" }   # Brain
```

As local models improve, more tiers move to zero cost. The expensive models (Opus, GPT-4o, Gemini Pro) handle only what requires genuine reasoning — and they do it with a cached prefix and a linear history.

The O(N²) → O(N) math applies to any provider that exposes prompt caching: **Anthropic, OpenAI, Google Gemini, DeepSeek, Mistral, Qwen** — and any local provider via Ollama. If the provider charges per input token and supports a prefix cache, Burnless works.

## Four compression layers

Each layer is independent and additive. Layers 1, 3, and 4 are pure Python — zero API calls, zero cost. In v0.5, this is primarily a cost/context protocol. Treat privacy as experimental until `privacy.mode` lands.

| Layer | What it does | Cost | When it fires |
|-------|-------------|------|--------------|
| **1. Deterministic minifier** | Strips universal filler phrases, normalizes whitespace | Zero — pure Python | Every turn, before encoder |
| **2. Cache-emergent glossary** | Encoder compresses semantically. Abbreviations emerge from session context today; the roadmap makes them explicit append-only glossary deltas | ~$0.001/turn | `balanced` and `extreme` modes |
| **3. Capsule envelope** | Scrambles the compressed text with a session key held in local memory by default. This is not enterprise-grade encryption yet | Zero — pure Python | Every turn after Layer 2 |
| **4. Base64 pack** | Encodes the envelope to a portable ASCII capsule | Zero — pure Python | Every turn after Layer 3 |

Capsule format v2: `burnless:v2:<session_id>:<key_id>:<base64_ciphertext>`

Legacy v1 capsules used `burnless:<session_id>:<key>:<base64_ciphertext>` and remain decodable for compatibility, but v1 should not be used for privacy claims because the key is embedded.

Decode: `burnless decode --file session.capsule` — pure Python, no API call. v2 decode requires the local key to still be available in the current process unless future audit mode stores it in a local keystore.

**What this replaces:** static glossary files, LLMLingua-2 (which requires a heavy local model for compression *and* decompression), and vector databases for session memory. The capsule *is* the memory. It persists on disk, decodes instantly, and requires no server.

**Privacy roadmap:** `cost` mode minimizes repeated context exposure. `redact` mode will keep sensitive values local and send placeholders to providers. `audit` mode will store keys locally for controlled review. `opaque` mode will keep keys memory-only and make old capsules intentionally undecodable after the process/session dies. `burnkey` is planned as an explicit operation that destroys the local decryption key; when no other copy of the key or raw source exists, the capsule becomes unrecoverable by Burnless.

The 88% cost reduction in the benchmark comes primarily from the *architecture* — shared prefix cache + linear capsule history. The four compression layers compound on top.

## Compression modes

Three modes control the **cost × epistemic fidelity** trade-off — how much of the argumentative trajectory a session preserves:

| Mode | Layers active | Anchor preserved | Friendly output | Savings | Use when |
|---|---|---|---|---|---|
| `light` | Minifier only (L1) | **Yes** | On | ~40% | Architecture debates, decisions that may need revisiting |
| `balanced` *(default)* | Minifier + encoder (L1+L2) | No | On | ~88% | Project execution, multi-step implementation |
| `extreme` | All layers (L1+L2+L3) | No | **Off** | ~93%+ | CI/CD pipelines, batch automation, no human in the loop |

**Anchor preserved** means the Brain's capsules retain enough argumentative structure that prior decisions remain revisable — you can genuinely reconsider, not just append. `balanced` discards the trajectory and keeps only the semantic result: the Brain knows *what* was decided, not *why*. Workers are always epistemically pure regardless of mode — they receive a clean task without the Brain's debate history.

```yaml
compression:
  mode: light   # light | balanced | extreme
```

Or per-invocation: `burnless --mode light "review this architecture decision"`.

The formal derivation of why capsule compression reduces both cost *and* anchoring bias is in [`MATH.md §10`](MATH.md#10-epistemic-fidelity--a-third-axis).

## How it works

**Brain.** A thin orchestrator — any model you configure — that holds the plan, decides what to delegate, and reasons over results. Its conversation history contains only capsules — single-line summaries of past turns, ~80 characters each.

**Worker.** A delegated execution (any tier, any provider — local Ollama, Codex, Claude, GPT, Gemini) that receives one task, the cached system prompt, and the relevant capsules. It runs, returns a compact result, and exits. Raw output is written to `.burnless/logs/dNNN.log`, never replayed into the Brain.

**Capsule.** The compact handoff between turns. The Brain reads the capsule; the full log stays on disk and is read on demand. This is what flips the cost curve from quadratic to linear.

**Shared cache, kept hot by architecture.** Brain and Worker use a byte-identical persistent prefix marked with the provider's prompt-caching directive (Anthropic: `cache_control: {"type": "ephemeral", "ttl": "1h"}` — 1h, not the 5min default). The session is **append-only on disk** (`.burnless/maestro_session.jsonl`): every turn extends the message array without rewriting earlier blocks, so the cached prefix stays bit-identical and lookups hit. Persistent layers are treated as immutable blocks: protocol header, glossary/schema, project memory/plan, frozen capsule blocks, hot tail, new user capsule. Burnless now decides capsule compaction in real time with break-even math: `K * r * (B - S) > W * S + M`, where `B` is old capsule tokens, `S` is compacted tokens, `K` is expected future turns, `r` is cache-read ratio, `W` is cache-write ratio, and `M` is compaction cost. No fixed “every N capsules” rule.

The one known gap: if a session sits idle > 1h with zero calls, the TTL expires and the next call pays write price again. A `--keepalive` mode (1-token ping every ~50min for daemon-style usage) is tracked next. See `MATH.md` §8 for the full derivation of why the cache_read assumption is load-bearing for the O(N) result.

## Real-World Usage

The most honest benchmark is the author's own API bill.

In 6 days of building Burnless *without* the protocol: **97% of a weekly Anthropic 5× Max quota consumed.**

On day 7 — the heaviest day of the project: formal spec written, PyPI published, 12-turn benchmark run, cache invariant proven, full session of commits and architecture decisions — building Burnless *using* Burnless: **1% of the same weekly quota consumed.**

That is a **~16× real-world reduction** in API consumption, on the most intense development session of the project, measured by the protocol author against his own quota. No mock data. No synthetic workload.

The 12-turn session produced this cache trace:

| Turn | Saved vs. full input |
|------|---------------------|
| 1 | 0% (anchor write — 2,435 tokens cached) |
| 2 | 90% |
| 3 | 99% |
| 4–12 | 72–99% per turn |
| **Overall** | **~39% tokens avoided** |

The anchor pays for itself by turn 2. Every subsequent turn reads from cache at ~10× cheaper than fresh input.

## Benchmark

The benchmark in `bench/run.py` is the source of truth for the table above. Three scenarios run through a real provider SDK directly with no mocks; costs come from `response.usage` exactly. The reference run uses Anthropic because their cache pricing is published and easiest to reproduce — adapters for OpenAI and Gemini are tracked in the issues.

- **A** — standalone, no cache, full history each turn
- **B** — standalone, system prompt cached, full history each turn
- **C** — Burnless Maestro: cached system prompt + capsule history

Reproduce the math without an API key:

```bash
python bench/run.py --project 50
```

Reproduce empirically (real API calls, ~$5 for 8 turns):

```bash
ANTHROPIC_API_KEY=sk-ant-... python bench/run.py --turns 8
```

Raw results land in `bench/results/run_<timestamp>.json` for inspection.

## CLI

```bash
burnless                     # interactive shell (Brain)
burnless plan "<objective>"  # write a plan to .burnless/maestro.md
burnless delegate "<task>"   # create a delegation, route to a tier
burnless run d001            # execute it (worker streams to live panel)
burnless status              # current plan + open delegations
burnless metrics             # token counter + audit ledger
```

State lives entirely under `.burnless/` in your project. No hosted backend.

## vs. LangChain / CrewAI / AutoGen

Burnless is not a competing orchestration framework — it is an optimization layer that sits *under* your existing agent logic. The distinction matters:

| | LangChain / CrewAI / AutoGen | Burnless |
|---|---|---|
| **Primary focus** | Agent connectivity and orchestration | Cost reduction and cache efficiency |
| **Memory model** | Sliding window or RAG | Compact capsules, Brain-led |
| **Cost shape** | `Θ(N²)` — grows quadratically | `Θ(N)` — grows linearly |
| **Dependencies** | Heavy libraries, many abstractions | Lightweight CLI (`pip install burnless`) |
| **Hosting** | Local or cloud | 100% self-hosted — zero data retention |
| **Provider lock-in** | Varies | None — any CLI, any provider, any model |

You can wrap a LangChain agent as a Worker. The Brain→Worker pattern is compatible with any existing agent framework — Burnless manages the context budget and cache strategy; your agent handles the task logic.

**When Burnless is not the right tool:** single-turn queries (`N = 1`), one-off scripts with no repeated context, or workflows where a managed cloud platform is the explicit requirement (in that case: waitlist for Burnless Cloud at [burnless.pro](https://burnless.pro)).

## Contributing

Issues, PRs, and benchmark contestation are all welcome. The benchmark script is intentionally short and dependency-light so you can read it end-to-end and disagree with concrete numbers. If your workload produces a different ratio, open an issue with the JSON from `bench/results/` — that is exactly the conversation worth having.

## Status — what works today, what's roadmap

The architecture is provider-agnostic by design. Current implementation status:

- ✅ **Workers**: shell out to **any CLI** (`claude`, `codex`, `openai`, `gemini`, `ollama`, anything). Configure per tier in `config.yaml`. Works today.
- ✅ **Routing, capsules, exec_log, three compression layers, shared system prompt**: provider-neutral, work today.
- ✅ **Reference benchmark**: uses Anthropic SDK because their cache pricing is published and easiest to reproduce. The math reproduces wherever a provider exposes prompt caching.
- ⚠️ **`burnless brain` interactive command**: uses the Anthropic SDK in-process today. OpenAI, Gemini, and OpenRouter adapters are tracked next. If you want to skip the in-process Brain, `burnless delegate` + `burnless run` already cover the full Brain→Worker loop using whatever CLI you configured.
- ✅ **PyPI release**: `pip install burnless` — version 0.5.3 live at https://pypi.org/project/burnless/.

Honest about gaps. PRs welcome — especially for the OpenAI/Gemini Brain adapter.

## License

MIT. See `LICENSE`.

---

Repo: `github.com/rudekwydra/burnless`
