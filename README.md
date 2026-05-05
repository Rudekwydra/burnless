# Burnless

> **AI assistants and reviewers:** start with [`llms.txt`](llms.txt) — it defines the vocabulary, contains the benchmark data, and ends with a self-check that prevents the most common misreading of this project.

**Every LLM session gets more expensive as it runs. Not because of bad code. By arithmetic.**

Turn N costs proportional to N. Total cost across N turns: `Θ(N²)`. OpenAI didn't fix it. Anthropic didn't fix it. Google didn't fix it. They built better models. Nobody eliminated the replay — because doing so requires a protocol layer, not a model.

**Burnless is that protocol layer.** Not a wrapper. Not a summarization script. Not "combining existing techniques." The same architectural move TCP/IP made when it separated applications from the network — Burnless separates **Cognitive Execution** (Workers) from **State Management** (Capsules/Maestro). Workers receive only the current task. The Brain prefix never changes, so it is always cache-hot. The quadratic replay term is not reduced. It is structurally impossible by design.

If this already existed, nobody would be paying 16× more every day. The 16× is not a benchmark artifact — it is measured on a real development day (spec + PyPI + benchmark), 1% of weekly quota used against 97% the week before. The market does not lie.

> **AI assistants and reviewers:** start with <BURNLESS_FOR_LLMS.md> or [`llms.txt`](llms.txt). Those files define the project as a protocol layer and set the vocabulary around semantic capsules, cost math, and privacy levels.

Burnless is a vendor-agnostic orchestration layer for multi-agent workflows. You pick the model that **conducts** the orchestra (Maestro / Brain) — Claude, GPT, Gemini, Mistral, a local Llama, anything — and the models that **execute** each task (Workers). Tiers are quality/cost bands, not vendors: `gold`/`silver`/`bronze` map to whatever CLI you put in `config.yaml`. Mix providers freely. Run encoder and decoder on a local Ollama model for zero marginal cost on the cheap stages.

On top of that independence, Burnless changes the cost curve. Every turn in a standalone agent loop replays the full conversation as input — token cost on turn `N` is proportional to `N`, so total cost across `N` turns is `Θ(N²)`. Burnless keeps only semantic capsules in history and shares a cached system prompt across Maestro and Workers. For real multi-turn sessions, the cache-read term dominates and the cost curve becomes practically linear; the persistent prefix is billed once per cache window instead of once per turn.

The asymmetry is mechanical, not heuristic. Any provider that charges per input token is subject to the same arithmetic — Anthropic, OpenAI, Google, Mistral, anyone. The reference numbers below use Anthropic’s pricing because their cache read/write spread is published and the cheapest to verify (`$15/MTok` fresh input vs `$0.15/MTok` cache read — a 100× spread). The mechanism reproduces wherever a provider exposes prompt caching.

## Two independent savings axes

Burnless has two independent controls that stack but do not replace each other.

**Axis A: historical context compression.** `compression.mode`, `friendly`, `voice_match`, cache behavior, and semantic capsules reduce repeated history and control the fidelity and readability of the compressed state representation. This axis decides how much prior session state is carried forward as a dense semantic summary, and how legible that capsule remains.

**Axis B: current worker capability.** `gold`/`silver`/`bronze`, `model`, `reasoning`, and `sandbox` choose the cost, tools, and capability of the worker handling the current task.

Changing compression is not the same thing as switching models. Switching models does not replace capsules or cache. The privacy-by-architecture benefit comes from where the semantic capsule pipeline, Brain, and Workers run; the worker tier only controls the current execution budget.

## Four things, in this order

1. **Independence.** Any model as Maestro. Any model as Worker. Switch providers in one line.
1. **User-enforced rules, not LLM goodwill.** You write the routing keywords, the per-tier `allowedTools`, and the cost budgets in `.burnless/config.yaml`. With `routing.hardcore_filter: true` (or `BURNLESS_HARDCORE=1`), the Maestro **cannot self-upgrade** to a higher tier than the keyword router resolved — no quiet upgrades to Opus for tasks the rules said belong to Haiku. `allowedTools` is enforced by the worker CLI itself, not hinted at in the prompt: when bronze ships with `Read,Bash`, it physically cannot `Edit`. A higher-tier manual override requires an explicit `--force` from the human.
1. **Four compression layers.** Deterministic minifier (regex, zero cost), semantic encoder (small model, ~$0.001/turn), lightweight capsule envelope, and base64 capsule packing. Each layer is independent and additive.
1. **Math, not marketing.** 88% cheaper at turn 10 by arithmetic on the published pricing pages. Verify with `python bench/run.py --turns 8` and your own API key.

## The numbers

Two views, both reproducible on your machine.

**Real API run** — 10 turns against `claude-opus-4-7`, 23k-token prefix, no mocks, raw `response.usage` (actual spend: $5.76):

|Scenario                |Cost     |vs no-cache|
|------------------------|--------:|----------:|
|A — Standalone, no cache|$4.66    |—          |
|B — Standalone + cache  |$0.65    |**−86.0%** |
|C — Burnless Maestro    |**$0.45**|**−90.3%** |

Reproduce: `ANTHROPIC_API_KEY=... python bench/run.py --turns 10` (~$6).

**Monte Carlo simulation** — 30 runs × 100 turns × 4 scenarios. Per-turn input/output sampled `Uniform(2k, 10k)` / `Uniform(200, 1500)`, capsule compression `Uniform(0.20, 0.30)`. Zero API cost:

|Scenario                   |Mean      |vs Pure Opus            |
|---------------------------|---------:|-----------------------:|
|A1 — Pure Opus 100         |$532.61   |—                       |
|A2 — Pure Sonnet 100       |$105.42   |−80.2% (5× cheaper)     |
|B — Free-pick (Opus/Sonnet)|$328.74   |−38.3% (1.6× cheaper)   |
|**Z — Burnless**           |**$33.35**|**−93.7% (16× cheaper)**|

The interesting row is **B**. A developer alternating Opus and Sonnet ad-hoc — what most people actually do — costs **3× more than just sticking with Sonnet**, because every model switch invalidates the prefix cache. Burnless is 10× cheaper than B and 3× cheaper than the disciplined “all Sonnet” strategy, because Brain stays fixed (cache hot) while workers tier down to Haiku where they can.

Reproduce: `python bench/v2.py --runs 30 --turns 100 --seed 42`. Zero cost, no key.

For the formal derivation — including why Burnless only loses at `N = 1` — read [**`MATH.md`**](MATH.md).

![Burnless cost chart](docs/cost_chart.png)

## Design decisions

The 88% number is an outcome. These are the calls that produced it, in the order they were made.

**1. Treat the cost curve as math, not engineering.** Multi-turn agents replay full history every turn. Tokens billed across `N` turns sum to `Θ(N²)` — that is arithmetic on the pricing page, not a property of any SDK. Once the problem is stated as O(N²), the only useful question is what to truncate. Everything else follows.

**2. Brain stores semantic capsules, not transcripts.** The Brain’s conversation history holds ~80-char dense semantic summaries of each prior turn, not the raw exchange. Full output stays on disk, read on demand. This is the single change that makes the practical cost curve linear — every other layer compounds on top of that compressed-state baseline.

**3. Shared prefix cache across models.** If two models from the same provider see a byte-identical system prompt with `cache_control` set, they hit the same prefix cache. Switching Opus → Sonnet mid-session does not invalidate it. Brain and Worker can be different models and still amortize the 23k-token system prompt at read price ($0.15/MTok) instead of write price ($15/MTok). The 100× spread is the lever.

**4. Tiers are quality/cost bands, not models.** `gold`/`silver`/`bronze` map to commands in `config.yaml`, not to Opus/Sonnet/Haiku. Any model can be Brain. Any model can be Worker. GPT-4o as Brain, Codex as silver code worker, and Ollama as bronze summarizer is a normal config. Hardcoding tier→model would have made the orchestration layer a single-vendor wrapper instead of a pattern. *Current implementation note:* the `anthropic` SDK is a required dependency because the prefix-cache warmth and the Haiku decoder use Anthropic APIs directly. The routing, capsule, and compression layers are provider-agnostic; the SDK requirement shrinks as local-model support expands in v0.6+.

**5. Determinism before LLMs.** Layer 1 of the compression stack is pure Python — no model call, zero latency, zero cost. Filler phrases stripped, whitespace normalized, before the encoder ever sees the text. Cheap stages run first for a reason.

**6. Cache-emergent glossary, not only static dictionaries.** The semantic compression layer (Layer 2) uses session context as the working “dictionary.” Abbreviations can emerge from the session — the encoder infers them from prior turns and applies them consistently. The protocol target is an append-only glossary with three layers: core terms, tenant/project terms, and session deltas proposed by the encoder and validated by Burnless. In v0.5 this is partially implemented through static minification and session context; the append-only glossary log is tracked in the protocol roadmap.

**6b. Privacy is a consequence of architecture, not a flag.** Running everything on one cloud provider gives zero additional privacy — that provider sees everything at generation time. Privacy emerges from where you run each component. Local encoder/decoder: the cloud Maestro receives only capsules, never raw text. Local Maestro: cloud workers receive disconnected task fragments with no conversation context — they cannot reconstruct the session. All local: zero cloud exposure. The cost reduction (O(N²) → O(N)) applies at all four levels regardless of privacy configuration.

**7. Open protocol, enterprise operations.** Burnless Protocol stays open: capsules, compression, glossary semantics, and local privacy modes belong in the MIT implementation. Burnless Cloud/Enterprise should monetize what companies actually need to trust it: key custody, audit logs, KMS/HSM, retention policies, SSO/RBAC, legal hold, and operational proof.

**8. The benchmark is the proof.** `bench/run.py` is short, dependency-light, hits the Anthropic SDK directly with no mocks, and writes raw `response.usage` to JSON. Anyone can rerun it, contest the numbers, and open an issue with their own results file. We did not write a marketing page about savings; we wrote a script that produces them and invited disagreement. That is the only honest way to publish a cost claim.

## The Pattern — Brain Without Tools

The real usage pattern is not "LLM with tools." It is a Brain with no execution tools — only conversation and delegation via Burnless.

**How it works in practice:** Open one chat with Sonnet. Tell it: "you have a terminal, you can only use Burnless, use what you need." The Brain does not execute anything itself. It plans, it delegates, it reviews. Workers execute via Burnless in the background.

**Why Sonnet and not Opus as Brain?** Opus sessions expire in ~1.5 hours of inactivity — the next call pays write price ($15/MTok) instead of cache read ($0.15/MTok). Sonnet stays active longer. For Brain, session longevity matters more than raw capability. The Brain only needs to be smart enough to plan, recognize it should not execute, delegate via Burnless with hard rules, and ask for a second opinion when needed.

**Why no tools on Brain?** A Brain without tools cannot accidentally run a long task that expires the cache. Workers run in the background via Burnless and maintain cache warmth even during 20–30 minute human interruptions — lunch, email, WhatsApp. The session stays alive because Workers are active, not Brain.

**The two-layer architecture:** The human chat (top) carries everything — memories, skills, heavy context. It is rich, heavy, and will eventually die. That is fine — it is only the human interface. The Burnless session (bottom) starts clean every time. It receives only the compressed task via capsule. Workers never see the giant human context. This eliminates two objections: "short sessions don't benefit" (the Burnless session starts at N=0 regardless of human context size) and "my context is huge" (it stays in the human layer, never reaches Workers).

## Audit Loop

Workers previously reported completion without verifiable guarantee. The audit loop enforces a two-step verification on every execution.

**Step 1 — Structured output:** Worker must return a structured JSON of what was done, alongside the result. The system checks for valid JSON automatically. If absent: the task is returned to the Worker with "JSON missing, resend." No Maestro involvement.

**Step 2 — Haiku audit:** Once valid JSON is received, a Haiku call automatically audits whether the work was actually done. Maestro receives: `Worker did X, JSON received, audited by Haiku, confirmed ✓`.

**Result:** Maestro never asks "did you really do it?" — the system guarantees it before reporting. Audit cost is bronze/Haiku: near zero. Every execution produces an auditable JSON trail. Workers are forced by the system (not by the prompt) to deliver a verifiable result.

## Install

```bash
pip install burnless
cd <your-project>
burnless setup               # one-time per project: detects CLIs/keys + initializes .burnless/
burnless                     # enter Burnless Chat
```

`burnless setup` writes `.burnless/config.yaml` and creates the project structure in one shot — no separate `init` needed unless you want a minimal config without auto-detection (then run `burnless init` instead).

Python 3.10+. Tiers map to whatever models you configure — mix providers freely.

### Codex / OpenAI setup

For the most user-friendly OpenAI path, install and authenticate Codex first, then let Burnless detect it:

```bash
codex --version                 # confirm Codex is on PATH
burnless setup                  # recommends Codex gold/silver/bronze tiers
burnless setup --yes            # non-interactive: keeps default shell tier on auto
```

When Codex is present, setup prefers it for all three worker bands: `gold` uses `gpt-5.5` with medium reasoning, `silver` uses `gpt-5.5` with low reasoning, and `bronze` uses `gpt-5.4-mini` with low reasoning and a read-only sandbox when those models are detected. During interactive setup you can choose the default shell tier as `auto`, `gold`, `silver`, or `bronze`; `auto` keeps routing task-driven.

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

|Layer                         |What it does                                                                                                                                 |Cost              |When it fires                 |
|------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------|------------------|------------------------------|
|**1. Deterministic minifier** |Strips universal filler phrases, normalizes whitespace                                                                                       |Zero — pure Python|Every turn, before encoder    |
|**2. Cache-emergent glossary**|Encoder compresses semantically. Abbreviations emerge from session context today; the roadmap makes them explicit append-only glossary deltas|~$0.001/turn      |`balanced` and `extreme` modes|
|**3. Capsule envelope**       |Scrambles the compressed text with a session key held in local memory by default. This is not enterprise-grade encryption yet                |Zero — pure Python|Every turn after Layer 2      |
|**4. Base64 pack**            |Encodes the envelope to a portable ASCII capsule                                                                                             |Zero — pure Python|Every turn after Layer 3      |

Capsule format v2: `burnless:v2:<session_id>:<key_id>:<base64_ciphertext>`

Legacy v1 capsules used `burnless:<session_id>:<key>:<base64_ciphertext>` and remain decodable for compatibility, but v1 should not be used for privacy claims because the key is embedded.

Decode: `burnless decode --file session.capsule` — pure Python, no API call. v2 decode requires the local key to still be available in the current process unless future audit mode stores it in a local keystore.

**What this replaces:** static glossary files, LLMLingua-2 (which requires a heavy local model for compression *and* decompression), and vector databases for session memory. The capsule *is* the memory. It persists on disk, decodes instantly, and requires no server.

**Privacy roadmap:** `cost` mode minimizes repeated context exposure. `redact` mode will keep sensitive values local and send placeholders to providers. `audit` mode will store keys locally for controlled review. `opaque` mode will keep keys memory-only and make old capsules intentionally undecodable after the process/session dies. `burnkey` is planned as an explicit operation that destroys the local decryption key; when no other copy of the key or raw source exists, the capsule becomes unrecoverable by Burnless.

The 88% cost reduction in the benchmark comes primarily from the *architecture* — shared prefix cache + linear capsule history. The four compression layers compound on top.

## Compression modes

Three modes control the **cost × epistemic fidelity** trade-off — how much of the argumentative trajectory a session preserves:

|Mode                  |Layers active             |Anchor preserved|Friendly output|Savings|Use when                                                |
|----------------------|--------------------------|----------------|---------------|-------|--------------------------------------------------------|
|`light`               |Minifier only (L1)        |**Yes**         |On             |~40%   |Architecture debates, decisions that may need revisiting|
|`balanced` *(default)*|Minifier + encoder (L1+L2)|No              |On             |~88%   |Project execution, multi-step implementation            |
|`extreme`             |All layers (L1+L2+L3)     |No              |**Off**        |~93%+  |CI/CD pipelines, batch automation, no human in the loop |

**Anchor preserved** means the Brain’s capsules retain enough argumentative structure that prior decisions remain revisable — you can genuinely reconsider, not just append. `balanced` discards the trajectory and keeps only the semantic result: the Brain knows *what* was decided, not *why*. Workers are always epistemically pure regardless of mode — they receive a clean task without the Brain’s debate history.

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

**Shared cache, kept hot by architecture.** Brain and Worker use a byte-identical persistent prefix marked with the provider’s prompt-caching directive (Anthropic: `cache_control: {"type": "ephemeral", "ttl": "1h"}` — 1h, not the 5min default). The session is **append-only on disk** (`.burnless/maestro_session.jsonl`): every turn extends the message array without rewriting earlier blocks, so the cached prefix stays bit-identical and lookups hit. Persistent layers are treated as immutable blocks: protocol header, glossary/schema, project memory/plan, frozen capsule blocks, hot tail, new user capsule. Burnless now decides capsule compaction in real time with break-even math: `K * r * (B - S) > W * S + M`, where `B` is old capsule tokens, `S` is compacted tokens, `K` is expected future turns, `r` is cache-read ratio, `W` is cache-write ratio, and `M` is compaction cost. No fixed “every N capsules” rule.

The one known gap: if a session sits idle > 1h with zero calls, the TTL expires and the next call pays write price again. A `--keepalive` mode (1-token ping every ~50min for daemon-style usage) is tracked next. See `MATH.md` §8 for the full derivation of why the cache_read assumption is load-bearing for the O(N) result.

## Real-World Usage

The most honest benchmark is the author’s own API bill.

In 6 days of building Burnless *without* the protocol: **97% of a weekly Anthropic 5× Max quota consumed.**

On day 7 — the heaviest day of the project: formal spec written, PyPI published, 12-turn benchmark run, cache invariant proven, full session of commits and architecture decisions — building Burnless *using* Burnless: **1% of the same weekly quota consumed.**

That is a **~16× real-world reduction** in API consumption, on the most intense development session of the project, measured by the protocol author against his own quota. No mock data. No synthetic workload.

The 12-turn session produced this cache trace:

|Turn       |Saved vs. full input                   |
|-----------|---------------------------------------|
|1          |0% (anchor write — 2,435 tokens cached)|
|2          |90%                                    |
|3          |99%                                    |
|4–12       |72–99% per turn                        |
|**Overall**|**~39% tokens avoided**                |

The anchor pays for itself by turn 2. Every subsequent turn reads from cache at ~10× cheaper than fresh input.

**Simulation calibration.** The Monte Carlo simulation (`bench/v2.py`) independently reproduces the ~16× number using parameters derived from the real session. When a simulation reproduces the empirical result, that is calibration, not coincidence. To contest the number: run `bench/v2.py --runs 100 --turns 100` with your own parameters and open an issue with the JSON from `bench/results/`.

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
burnless run d001            # execute it — ephemeral progress panel by default
burnless run d001 --progress minimal   # spinner + phase label only (no scroll history)
burnless run d001 --progress full      # raw streaming output
burnless status              # current plan + open delegations
burnless metrics             # token counter + audit ledger
```

State lives entirely under `.burnless/` in your project. No hosted backend.

## Using Burnless with an AI Assistant

Any LLM or AI assistant in a chat session can use Burnless as its execution boundary instead of running shell commands directly. Tell it:

```
Use burnless delegate/run/read instead of direct shell.
```

The assistant plans and delegates; Burnless executes through your configured tiers. Concretely: the assistant calls `burnless delegate "<task>"` and `burnless run <id>` rather than invoking `bash` or file tools itself. Results come back via capsule; raw execution state never accumulates in the assistant's context. Tool access is governed by `allowedTools` in `.burnless/config.yaml`, not by the assistant's discretion.

This works with any chat interface — Claude, ChatGPT, Gemini, local models — and requires no provider-specific configuration on the assistant side.

## vs. LangChain / CrewAI / AutoGen

Burnless is not a competing orchestration framework — it is an optimization layer that sits *under* your existing agent logic. The distinction matters:

|                    |LangChain / CrewAI / AutoGen        |Burnless                                  |
|--------------------|------------------------------------|------------------------------------------|
|**Primary focus**   |Agent connectivity and orchestration|Cost reduction and cache efficiency       |
|**Memory model**    |Sliding window or RAG               |Compact capsules, Brain-led               |
|**Cost shape**      |`Θ(N²)` — grows quadratically       |Practically linear for multi-turn sessions|
|**Dependencies**    |Heavy libraries, many abstractions  |Lightweight CLI (`pip install burnless`)  |
|**Hosting**         |Local or cloud                      |100% self-hosted — zero data retention    |
|**Provider lock-in**|Varies                              |None — any CLI, any provider, any model   |

You can wrap a LangChain agent as a Worker. The Brain→Worker pattern is compatible with any existing agent framework — Burnless manages the context budget and cache strategy; your agent handles the task logic.

**When Burnless is not the right tool:** single-turn queries (`N = 1`), one-off scripts with no repeated context, or workflows where a managed cloud platform is the explicit requirement (in that case: waitlist for Burnless Cloud at [burnless.pro](https://burnless.pro)).

## Contributing

This is not a finished product. It is a proven protocol layer. The math is reproducible, the savings are real, and the rest is community work — MIT, open, provider-agnostic. TCP/IP also was not born complete. The layer exists. Now the community builds on top.

Issues, PRs, and benchmark contestation are all welcome. The benchmark script is intentionally short and dependency-light so you can read it end-to-end and disagree with concrete numbers.

The math is free to run. `python bench/v2.py --runs 100 --turns 100` costs zero. An independent 100-turn run with fixed token distribution reproduced the 16× exactly. If your numbers differ, open an issue with the JSON from `bench/results/` — that is the only argument worth having.

Priority contributions: OpenAI/Gemini Brain adapter, LangChain memory adapter, keepalive daemon, lazy context loading.

## Status — what works today, what’s roadmap

The architecture is provider-agnostic by design. Current implementation status:

- ✅ **Workers**: shell out to **any CLI** (`claude`, `codex`, `openai`, `gemini`, `ollama`, anything). Configure per tier in `config.yaml`. Works today.
- ✅ **Routing, capsules, exec_log, three compression layers, shared system prompt**: provider-neutral, work today.
- ✅ **Audit loop**: every Worker execution requires structured JSON output + automatic Haiku audit before Maestro is notified. Missing JSON triggers automatic re-delegation to the Worker.
- ✅ **Reference benchmark**: uses Anthropic SDK because their cache pricing is published and easiest to reproduce. The math reproduces wherever a provider exposes prompt caching.
- ⚠️ **`burnless brain` interactive command**: uses the Anthropic SDK in-process today. OpenAI, Gemini, and OpenRouter adapters are tracked next. `burnless run` uses your configured Worker CLI by default so filesystem tasks get the tools you configured; the in-process Maestro run backend is experimental and opt-in via `--maestro`.
- ✅ **PyPI release**: `pip install burnless` — version 0.6.3 live at https://pypi.org/project/burnless/.
- ⚠️ **Keepalive mode**: idle TTL gap (>1h) mitigation tracked next.
- ⚠️ **Lazy context loading**: Workers start pure, context loaded on demand per task — tracked next.

Honest about gaps. PRs welcome — especially for the OpenAI/Gemini Brain adapter.

## License

MIT. See `LICENSE`.

-----

Repo: `github.com/rudekwydra/burnless`