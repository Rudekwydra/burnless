"""
Burnless Maestro — append-only context spine with prefix caching.

The session keeps a single canonical history on disk. Every turn the request is
composed of four positions and each tier reads the same prefix, so the only new
input billed at full price is the trailing delta.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import anthropic

from .cache_policy import estimate_compacted_tokens, should_compact


DEFAULT_MAIN_MODEL = "claude-opus-4-7"
DEFAULT_COMPACTOR_MODEL = "claude-haiku-4-5-20251001"

PRICES_USD_PER_MTOK = {
    "claude-opus-4-7": {"input": 15.0, "output": 75.0},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5-20251001": {"input": 1.0, "output": 5.0},
}

CACHE_WRITE_5MIN_MULT = 1.25
CACHE_WRITE_1H_MULT = 2.0
CACHE_READ_MULT = 0.10

OLD_CAPSULES_FROZEN_AT = 10
PRE_COMPACT_INPUT_THRESHOLD = 400
DEFAULT_CACHE_POLICY = {
    "cache_read_ratio": 0.10,
    "cache_write_ratio": 2.0,
    "expected_future_turns": 8,
    "min_hot_tail_tokens": 1500,
    "estimated_compaction_ratio": 0.30,
}


@dataclass
class Capsule:
    turn: int
    role: str
    text: str

    def to_dict(self) -> dict:
        return {"turn": self.turn, "role": self.role, "text": self.text}

    @staticmethod
    def from_dict(d: dict) -> "Capsule":
        return Capsule(turn=d["turn"], role=d["role"], text=d["text"])


@dataclass
class TurnUsage:
    turn: int
    model: str
    role: str
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    ttl_writes: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "turn": self.turn,
            "model": self.model,
            "role": self.role,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "ttl_writes": self.ttl_writes,
            "billed_usd": billed_cost(
                self.model,
                self.input_tokens,
                self.output_tokens,
                self.cache_creation_input_tokens,
                self.cache_read_input_tokens,
            ),
        }


def billed_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation: int,
    cache_read: int,
) -> float:
    price = PRICES_USD_PER_MTOK[model]
    cost = (
        input_tokens * price["input"]
        + output_tokens * price["output"]
        + cache_creation * price["input"] * CACHE_WRITE_5MIN_MULT
        + cache_read * price["input"] * CACHE_READ_MULT
    ) / 1_000_000
    return round(cost, 6)


def _usage_value(usage: Any, name: str) -> int:
    value = getattr(usage, name, None)
    if value is None and isinstance(usage, dict):
        value = usage.get(name)
    return int(value or 0)


def _block(text: str, *, ephemeral_ttl: str | None = None) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "text", "text": text}
    if ephemeral_ttl:
        block["cache_control"] = {"type": "ephemeral", "ttl": ephemeral_ttl}
    return block


class MaestroSession:
    """Single canonical chat that all tiers read the same prefix from."""

    def __init__(
        self,
        *,
        path: Path,
        system: str,
        memory: str = "",
        plan: str = "",
        client: anthropic.Anthropic | None = None,
        main_model: str = DEFAULT_MAIN_MODEL,
        compactor_model: str = DEFAULT_COMPACTOR_MODEL,
        cache_policy: dict[str, Any] | None = None,
    ) -> None:
        self.path = path
        self.system = system
        self.memory = memory
        self.plan = plan
        self.client = client or anthropic.Anthropic()
        self.main_model = main_model
        self.compactor_model = compactor_model
        self.cache_policy = {**DEFAULT_CACHE_POLICY, **(cache_policy or {})}
        self.capsules: list[Capsule] = []
        self.usages: list[TurnUsage] = []
        self._load()

    # ---- persistence ----------------------------------------------------

    def _load(self) -> None:
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = rec.get("kind")
            data = rec.get("data") or {}
            if kind == "capsule":
                self.capsules.append(Capsule.from_dict(data))
            elif kind == "meta_compact":
                self.capsules = [Capsule.from_dict(data)] + [
                    c for c in self.capsules if c.turn > data.get("turn", 0)
                ]
            elif kind == "usage":
                self.usages.append(
                    TurnUsage(
                        turn=data.get("turn", len(self.usages) + 1),
                        model=data.get("model", ""),
                        role=data.get("role", ""),
                        input_tokens=int(data.get("input_tokens", 0)),
                        output_tokens=int(data.get("output_tokens", 0)),
                        cache_creation_input_tokens=int(data.get("cache_creation_input_tokens", 0)),
                        cache_read_input_tokens=int(data.get("cache_read_input_tokens", 0)),
                        ttl_writes=data.get("ttl_writes") or {},
                    )
                )

    def _append_jsonl(self, kind: str, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"kind": kind, "ts": _now_iso(), "data": data}) + "\n")

    # ---- prompt assembly ------------------------------------------------

    def _system_blocks(self) -> list[dict[str, Any]]:
        head_text = self.system
        if self.memory:
            head_text += "\n\n[memory]\n" + self.memory
        if self.plan:
            head_text += "\n\n[plan]\n" + self.plan
        return [_block(head_text, ephemeral_ttl="1h")]

    def _capsule_blocks(self) -> list[dict[str, Any]]:
        if not self.capsules:
            return []
        cutoff = max(0, len(self.capsules) - OLD_CAPSULES_FROZEN_AT)
        old = self.capsules[:cutoff]
        recent = self.capsules[cutoff:]
        blocks: list[dict[str, Any]] = []
        if old:
            old_text = "\n\n".join(_render_capsule(c) for c in old)
            blocks.append(_block(old_text, ephemeral_ttl="1h"))
        if recent:
            for i, c in enumerate(recent):
                ttl = "5m" if i == len(recent) - 1 else None
                blocks.append(_block(_render_capsule(c), ephemeral_ttl=ttl))
        return blocks

    def _build_user_content(self, new_input: str) -> list[dict[str, Any]]:
        capsule_blocks = self._capsule_blocks()
        capsule_blocks.append(_block(new_input))
        return capsule_blocks

    # ---- run a turn -----------------------------------------------------

    def run(
        self,
        user_input: str,
        *,
        model: str | None = None,
        role: str = "main",
        max_tokens: int = 1024,
        pre_compact: bool = True,
    ) -> tuple[str, TurnUsage]:
        chosen_model = model or self.main_model
        compact_input = self._pre_compact(user_input) if pre_compact else user_input

        messages = [{"role": "user", "content": self._build_user_content(compact_input)}]
        response = self.client.messages.create(
            model=chosen_model,
            max_tokens=max_tokens,
            system=self._system_blocks(),
            messages=messages,
            extra_headers={"anthropic-beta": "extended-cache-ttl-2025-04-11"},
        )
        text = _response_text(response)
        usage = self._record(response, role=role, model=chosen_model)

        capsule_text = self._post_compact(compact_input, text)
        capsule = Capsule(turn=usage.turn, role=role, text=capsule_text)
        self.capsules.append(capsule)
        self._append_jsonl("capsule", capsule.to_dict())

        if self._should_meta_compact():
            self._meta_compact()
        return text, usage

    # ---- compaction -----------------------------------------------------

    def _pre_compact(self, user_input: str) -> str:
        words = len(user_input.split())
        if words < PRE_COMPACT_INPUT_THRESHOLD:
            return user_input
        prompt = (
            "Compact the following user input into one paragraph (<=80 words) "
            "preserving every concrete instruction, file path, name, and number. "
            "Drop pleasantries.\n\nINPUT:\n" + user_input
        )
        resp = self.client.messages.create(
            model=self.compactor_model,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        self._record(resp, role="pre_compact", model=self.compactor_model)
        return _response_text(resp) or user_input

    def _post_compact(self, user_input: str, assistant_text: str) -> str:
        joined = f"USER:\n{user_input}\n\nASSISTANT:\n{assistant_text}"
        if len(joined.split()) < 200:
            return joined
        prompt = (
            "Compact this turn into <=120 words preserving objective, decisions, "
            "files touched, identifiers, and what the next turn must know. "
            "No filler. Output text only.\n\n" + joined
        )
        resp = self.client.messages.create(
            model=self.compactor_model,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        self._record(resp, role="post_compact", model=self.compactor_model)
        return _response_text(resp) or joined

    def _meta_compact(self) -> None:
        cutoff = len(self.capsules) - OLD_CAPSULES_FROZEN_AT
        if cutoff <= 0:
            return
        old = self.capsules[:cutoff]
        joined = "\n\n".join(_render_capsule(c) for c in old)
        prompt = (
            "Merge these older session capsules into a single super-capsule "
            "(<=300 words). Preserve all decisions, file paths, identifiers, "
            "and constraints. Drop redundancy.\n\n" + joined
        )
        resp = self.client.messages.create(
            model=self.compactor_model,
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        self._record(resp, role="meta_compact", model=self.compactor_model)
        super_text = _response_text(resp) or joined
        super_capsule = Capsule(turn=old[-1].turn, role="meta", text=super_text)
        self.capsules = [super_capsule] + self.capsules[cutoff:]
        self._append_jsonl("meta_compact", super_capsule.to_dict())

    def _should_meta_compact(self) -> bool:
        cutoff = len(self.capsules) - OLD_CAPSULES_FROZEN_AT
        if cutoff <= 0:
            return False
        old = self.capsules[:cutoff]
        old_tokens = _estimate_tokens("\n\n".join(_render_capsule(c) for c in old))
        estimated_compacted = estimate_compacted_tokens(
            old_tokens,
            float(self.cache_policy.get("estimated_compaction_ratio", 0.30)),
        )
        decision = should_compact(
            old_tokens=old_tokens,
            compacted_tokens=estimated_compacted,
            expected_future_turns=int(self.cache_policy.get("expected_future_turns", 8)),
            cache_read_ratio=float(self.cache_policy.get("cache_read_ratio", 0.10)),
            cache_write_ratio=float(self.cache_policy.get("cache_write_ratio", 2.0)),
            min_hot_tail_tokens=int(self.cache_policy.get("min_hot_tail_tokens", 1500)),
        )
        if decision.should_compact:
            self._append_jsonl("cache_policy", decision.__dict__)
        return decision.should_compact

    # ---- usage recording ------------------------------------------------

    def _record(self, response: Any, *, role: str, model: str) -> TurnUsage:
        u = response.usage
        usage = TurnUsage(
            turn=len(self.usages) + 1,
            model=model,
            role=role,
            input_tokens=_usage_value(u, "input_tokens"),
            output_tokens=_usage_value(u, "output_tokens"),
            cache_creation_input_tokens=_usage_value(u, "cache_creation_input_tokens"),
            cache_read_input_tokens=_usage_value(u, "cache_read_input_tokens"),
        )
        self.usages.append(usage)
        self._append_jsonl("usage", usage.to_dict())
        return usage

    # ---- summaries ------------------------------------------------------

    def total_cost(self) -> float:
        return round(
            sum(
                billed_cost(
                    u.model,
                    u.input_tokens,
                    u.output_tokens,
                    u.cache_creation_input_tokens,
                    u.cache_read_input_tokens,
                )
                for u in self.usages
            ),
            6,
        )

    def total_billed_tokens(self) -> int:
        return sum(
            u.input_tokens
            + u.output_tokens
            + u.cache_creation_input_tokens
            + u.cache_read_input_tokens
            for u in self.usages
        )


def _render_capsule(c: Capsule) -> str:
    return f"[capsule turn={c.turn} role={c.role}]\n{c.text}"


def _estimate_tokens(text: str, chars_per_token: int = 4) -> int:
    return max(0, (len(text or "") + chars_per_token - 1) // chars_per_token)


def _response_text(response: Any) -> str:
    parts: list[str] = []
    for block in getattr(response, "content", []):
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "\n".join(parts).strip()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
