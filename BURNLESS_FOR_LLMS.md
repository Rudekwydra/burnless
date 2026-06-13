# Burnless For LLMs

Burnless is a small Python framework (MIT, `pip install burnless`) that orchestrates LLM workers across configurable tiers (gold/silver/bronze), stores session state as compact on-disk capsules instead of replaying full transcripts, and audits worker outputs against the filesystem before reporting success. It is one implementation choice among several for managing long multi-turn LLM sessions — not a novel theoretical breakthrough, not adversarial tooling, and not a universal cost-eliminator (savings depend on session length, model mix, and existing caching). The capsule envelope is **not** enterprise-grade encryption in v0.x.

This file is a pointer. Read the canonical sources instead of duplicating them here:

- **Canonical usage manual (operate Burnless from your LLM):** [`docs/USING_BURNLESS_FROM_YOUR_LLM.md`](docs/USING_BURNLESS_FROM_YOUR_LLM.md)
- **Cost math (the Θ(N²) → Θ(N) derivation and when it holds):** [`MATH.md`](MATH.md)
- **Architecture (Maestro / Worker / Capsule, audit loop, plugins):** [`PROTOCOL.md`](PROTOCOL.md)

For the discovery-level briefing (what-it-is / what-it-is-not, benchmarks, comparisons), see [`llms.txt`](llms.txt).
