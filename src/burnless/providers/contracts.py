"""Ask control-plane contracts (M1a) — types + the AskAdapter interface only.

WHERE THIS LIVES (architectural decision, d955 / EXECUCAO M1a):
    A new `burnless.providers` package, NOT inside `pure_ask.py`. These types
    are shared by every ask adapter (anthropic CLI, ollama HTTP, codex/openai);
    pure_ask becomes a thin caller of them in M1b. Keeping them out of pure_ask
    avoids turning that module into a god-object and gives the adapters a common
    import target with no behaviour attached.

BOUNDARY (handoff invariant 10 — no parallel stack):
    These contracts CONSUME the existing resolvers, they do not duplicate them.
      * tier -> provider / model / auth / cache-mode is resolved by
        `burnless.coreconfig.resolver` (resolve_model / resolve_agent /
        resolve_cache_mode). `ResolvedAskTarget` just carries those results.
      * `ResolvedAskTarget.cache_mode` is the string key that
        `burnless.cache_modes.get()` understands (e.g. "anthropic_subscription")
        — the cache handler and min-prefix sizing stay owned by cache_modes /
        coreconfig, never re-implemented here.
    An ask adapter is stateless and text-only; it differs from a Maestro/PTY
    HostAdapter but shares the same resolved model and capability registry.

PURITY: types and docstrings only. No subprocess, no file I/O, no network in
this module — that is M1b. Frozen dataclasses + a typing.Protocol.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:  # boundary references, never imported at runtime here
    # Resolved cache-mode / agent shapes owned by coreconfig; adapters reuse
    # these instead of minting their own. Imported under TYPE_CHECKING so this
    # contracts module stays free of runtime dependencies and I/O.
    from ..coreconfig.schema import Agent, CacheMode  # noqa: F401


# ---------------------------------------------------------------------------
# Request — the raw call intent (CLI / stdin -> AskRequest), pre-resolution.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AskRequest:
    """One `burnless ask` invocation, before the resolver runs.

    `output_format` is the PROVIDER-native format (text/json passed through to
    the CLI/API); `envelope_format` is the burnless.ask/v1 rendering of the
    result (sec 5.2 — the two are separate). prefix_file / cache_key drive the
    stateless prefix cache (sec 14); the adapter hashes the prefix, never its
    content.
    """

    prompt: str
    tier: str | None = None            # requested tier; None -> route()
    provider: str | None = None        # explicit provider (disambiguates model)
    model: str | None = None           # explicit model override
    system: str | None = None
    effort: str | None = None
    output_format: str = "text"        # provider-native passthrough
    envelope_format: str = "text"      # burnless envelope: "text" | "json"
    timeout_s: int = 120
    explain: bool = False              # sec 10 — show resolution
    dry_run: bool = False              # sec 10 — resolve, do not spend
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    max_total_tokens: int | None = None
    max_budget_usd: float | None = None
    budget_policy: str = "soft"        # "hard" | "soft"
    prefix_file: str | None = None     # sec 14 — stable prefix source path
    cache_key: str | None = None       # sec 14 — stable cache key
    request_id: str | None = None


# ---------------------------------------------------------------------------
# Capabilities — per adapter/model registry (sec 11). What the provider can
# actually observe/enforce; never assume a Claude flag exists on Codex.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ProviderCapabilities:
    observable_token_usage: bool = False
    observable_cache_usage: bool = False
    hard_max_output: bool = False        # can enforce a hard output cap
    hard_spend_cap: bool = False         # can enforce a hard USD cap
    supported_efforts: tuple[str, ...] = ()
    prefix_cache: bool = False           # observable stateless prefix cache
    streaming: bool = False
    json_output: bool = False
    reliable_cancel: bool = False        # cancel()/timeout is dependable


# ---------------------------------------------------------------------------
# Budget plan — resolved budgets + preflight estimate (sec 11). `enforcement`
# records whether the cap can be hard-imposed given the capabilities.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class BudgetPlan:
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    max_total_tokens: int | None = None
    max_budget_usd: float | None = None
    policy: str = "soft"                 # requested: "hard" | "soft"
    enforcement: str = "soft_only"       # actual: "hard" | "soft_only"
    estimated_input_tokens: int | None = None
    estimated_output_tokens: int | None = None
    estimated_cost_usd: float | None = None
    basis: str = "estimate"              # estimate | tokenizer | provider_reported


# ---------------------------------------------------------------------------
# Resolved target — the single object shared by explain / dry-run / execution
# (sec 10: "ambos usam o mesmo ResolvedAskTarget"). Everything downstream reads
# this; nothing re-resolves. Provider/model/auth/cache_mode come from
# coreconfig.resolver; capabilities/budget from the capability registry.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ResolvedAskTarget:
    effective_tier: str
    provider: str
    model: str
    request_id: str = ""
    requested_tier: str | None = None
    auth: str = "subscription"
    effort: str | None = None
    cache_mode: str = "none"             # key for burnless.cache_modes.get()
    adapter_key: str = ""                # which AskAdapter handles this
    route_source: str = "default"        # explicit | policy | signal | keyword | default
    route_reason: str = ""
    route_signals: tuple[str, ...] = ()
    capabilities: ProviderCapabilities = field(default_factory=ProviderCapabilities)
    budget: BudgetPlan = field(default_factory=BudgetPlan)
    prefix_hash: str | None = None       # sec 14 — hash only, never content
    redacted_command: str = ""           # sec 10 — command with no token/key


# ---------------------------------------------------------------------------
# Provider result — raw capture from the adapter transport (sec 8 "bruto").
# raw_json stays internal to the adapter; the caller only sees AskResult.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ProviderResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""
    raw_json: str | None = None          # provider-native, adapter-internal
    timed_out: bool = False
    signal: int | None = None
    parse_error: str | None = None
    duration_ms: int = 0


# ---------------------------------------------------------------------------
# Usage record — normalized usage + basis (sec 9). basis ranks observed >
# provider_reported > tokenizer > chars > estimate. Cost carries its own basis
# and pricing_version so reports reconcile with an explicit label.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class UsageRecord:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    basis: str = "estimate"              # observed | provider_reported | tokenizer | chars | estimate
    cost_usd: float | None = None
    cost_basis: str = "pricing_table"
    pricing_version: str | None = None


# ---------------------------------------------------------------------------
# Ask result — the burnless.ask/v1 envelope (sec 8), provider-independent.
# `content` may be present for the caller; persisted events must never carry
# prompt/content/secret (invariant 8) — that redaction happens at the writer.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AskResult:
    schema: str = "burnless.ask/v1"
    request_id: str = ""
    status: str = "ok"                   # "ok" | "error"
    content: str | None = None
    requested_tier: str | None = None
    effective_tier: str = ""
    provider: str = ""
    model: str = ""
    effort: str | None = None
    route_source: str = ""
    route_reason: str = ""
    route_signals: tuple[str, ...] = ()
    usage: UsageRecord = field(default_factory=UsageRecord)
    duration_ms: int = 0
    cache_mode: str = "none"
    prefix_hash: str | None = None
    cache_key: str | None = None
    dry_run: bool = False
    error_kind: str | None = None        # normalized error, never a bare rc=1
    error_message: str | None = None
    warnings: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Adapter interface — the 6 fixed methods (CODEX_NATIVE Fase 2). A concrete
# adapter is stateless and text-only; it reuses the resolved target rather than
# re-selecting provider/model/cache. Names are FIXED: resolve / explain /
# invoke_text / parse_usage / capabilities / cancel.
# ---------------------------------------------------------------------------
@runtime_checkable
class AskAdapter(Protocol):
    def resolve(
        self, request: AskRequest, cfg: dict, *, prefix_content: str | None = None
    ) -> ResolvedAskTarget:
        """Turn a request into a ResolvedAskTarget using coreconfig.resolver and
        the capability registry. No provider call, no I/O.

        `prefix_content` is the already-read text of `request.prefix_file` (sec
        14) — resolved once by the caller, never re-read here — used only to
        compute `ResolvedAskTarget.prefix_hash`; the content itself is never
        stored on the target."""
        ...

    def explain(self, target: ResolvedAskTarget) -> dict:
        """Render the resolution (sec 10) as a redacted, serializable mapping —
        no token/key. Same object for --explain and --dry-run."""
        ...

    def invoke_text(
        self, request: AskRequest, target: ResolvedAskTarget, *, prefix_content: str | None = None
    ) -> ProviderResult:
        """Execute the pure text completion against the resolved provider and
        return the raw transport result (stdout/stderr/exit/signal/parse).

        `prefix_content`, when present, is appended to the effective system
        prompt (sec 14 decision 1) — never to `request.prompt`."""
        ...

    def parse_usage(self, result: ProviderResult, target: ResolvedAskTarget) -> UsageRecord:
        """Extract normalized usage/cost from the provider-native result, with an
        explicit basis. Estimate when the provider does not report."""
        ...

    def capabilities(self, target: ResolvedAskTarget) -> ProviderCapabilities:
        """Report what this adapter/model can observe and hard-enforce."""
        ...

    def cancel(self) -> bool:
        """Best-effort cancel of an in-flight call. Return True if honored."""
        ...
